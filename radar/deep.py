"""題材深挖：每個影片題材最早什麼時候在 YouTube 出現，再多找一批同類爆款。

    python3 -m radar deep [--day D] [--labels 新起,升溫]   # 所有題材：每個約 200 配額單位

每日執行只對標 config.DEEP_DAILY_LABELS（新起 / 升溫）的題材跑。

模型只負責提搜尋詞，最後再根據查到的事實寫三句話。其他都是程式算的：
- YouTube：兩次不限日期的搜尋（相關度、史上觀看最高），標題要對得上搜尋詞。
- 另外按年份切四個區間各搜一次（相關度），畫出這個詞過去四年每年有幾支對得上的影片。
每一條對得上的都要明說是 AI（標題、說明或頻道名裡有強 AI 詞）；不然「vegetable family」會搜到 2018 年的兒歌。

「最早能查到」是這些搜尋回傳的最舊一支。平台搜尋按熱度排，形式可能更早就有；頁面上會這樣說。
"""

from __future__ import annotations

import json
import time
from datetime import datetime

from . import analyze, config, db, llm, net, pipeline
from .analyze import BJ, _bj
from .score import fmt_num
from .sources import youtube
from .sources.common import log, strong_ai

YEARS = 4

TERMS_PROMPT = """下面是 AI 影片情報雷達今天識別出的影片題材。為了查清每個題材最早什麼時候出現、再多找一些同類爆款，請給每個題材寫 YouTube 搜尋詞。

題材（編號 | 名字 | 說明 | 已有搜尋詞 | 代表作標題）：
{lines}

只輸出一個 JSON 物件（不要 Markdown 程式碼區塊）：
{{"topics": [{{
  "id": 編號,
  "en": "英文搜尋詞，2 到 4 個詞，YouTube 上這類影片標題裡常見的說法（例如 vegetable family、fruit baby、AI food slide）；已有的搜尋詞合適就沿用",
  "zh": "中文搜尋詞，2 到 6 個字，這類影片的中文標題裡常見、又能代表這個形式的詞；這個題材沒有中文影片就寫空字串"
}}]}}

搜尋詞要和題材名一樣具體，能把它和別的 AI 影片分開：題材是「AI 美食滑梯解壓」就用「food slide」，不要用「AI 解壓」；題材是「AI 遊戲角色 MV」就用代表性的角色或遊戲名，不要用「AI MV」。不要用「AI video」「AI animation」這種泛詞。用繁體中文輸出。"""

NOTE_PROMPT = """你在替 AI 影片創作者講每個題材的來龍去脈。下面是系統從 YouTube 搜尋查到的事實：日期、標題、觀看都來自搜尋結果，你不能改，也不能編造沒給出的事實。平台搜尋按熱度排序，所以「最早能查到」不一定是真正的源頭，說法要留餘地。

{blocks}

每個題材寫三句話：
- origin：最早能查到是什麼時候、什麼樣的作品；之後是一直有，還是最近才突然多起來。
- spread：後來長出了什麼變體（換角色、換語言、換劇情）；看不出來就寫空字串。
- now：現在進場的時機（還在早期 / 正在起量 / 已經很擠），說出依據。

只輸出一個 JSON 物件（不要 Markdown 程式碼區塊）：
{{"topics": [{{"id": 編號, "origin": "...", "spread": "...", "now": "..."}}]}}

用大白話，不要用「不是…而是…」句式，不要冒號清單。用繁體中文輸出。"""


def _match(term: str, c: dict) -> bool:
    return youtube.title_matches(term, c["title"]) and strong_ai(f"{c['title']} {c.get('text') or ''} {c.get('author') or ''}")


def youtube_history(term: str) -> dict:
    """不限日期的兩次搜尋，標題對得上的留下來，最舊的那支就是「最早能查到」。"""
    rows = []
    for order in ("relevance", "viewCount"):
        try:
            rows += youtube.search_any_time(term, order)
        except Exception as exc:
            log(f"deep youtube {term}: {exc}")
    seen, hits = set(), []
    for c in rows:
        if c["id"] not in seen and _match(term, c):
            seen.add(c["id"])
            hits.append(c)
    dated = [h for h in hits if h.get("published_at")]
    return {"term": term, "hits": hits, "earliest": min(dated, key=lambda h: h["published_at"]) if dated else None}


def yearly_history(term: str, now: float) -> dict:
    """過去 YEARS 年，每年一次相關度搜尋（限定該年的上傳日期），數標題對得上的有幾支。
    回傳格式和舊版的 B 站季度圖一樣（start / end / match），頁面上的長條圖直接沿用。"""
    years, found = [], {}
    this_year = datetime.fromtimestamp(now, BJ).year
    for y in range(this_year - YEARS + 1, this_year + 1):
        start = datetime(y, 1, 1, tzinfo=BJ).timestamp()
        end = min(datetime(y + 1, 1, 1, tzinfo=BJ).timestamp(), now)
        hits = []
        try:
            page = youtube._search_page(term, published_after=start, order="relevance")
            page["items"] = [it for it in page.get("items") or []
                             if (youtube.iso_ts((it.get("snippet") or {}).get("publishedAt") or "") or 0) < end]
            ids = [it["id"]["videoId"] for it in page["items"] if (it.get("id") or {}).get("videoId")]
            cards = [c for c in (youtube._card(v, f"youtube:{term}") for v in (youtube._details(ids) if ids else [])) if c]
            hits = [c for c in cards if _match(term, c)]
        except Exception as exc:
            log(f"deep youtube year {y} {term}: {exc}")
        years.append({"start": start, "end": end, "match": len(hits),
                      "top_views": max((h["metrics"]["views"] or 0 for h in hits), default=0)})
        for h in hits:
            found[h["id"]] = h
    return {"years": years, "hits": list(found.values()), "older_than_window": bool(years and years[0]["match"])}


def _sample(c: dict, now: float) -> dict:
    s = {"title": c["title"], "url": c["url"], "thumb": c.get("thumb") or "", "author": c.get("author") or "",
         "platform": analyze.platform(c), "duration": (c.get("extra") or {}).get("duration"),
         "vertical": bool((c.get("extra") or {}).get("vertical")),
         "views": c["metrics"].get("views"), "likes": c["metrics"].get("likes"), "followers": c["metrics"].get("followers"),
         "published": _bj(c.get("published_at"), "%Y-%m-%d"), "published_at": c.get("published_at")}
    s["age_h"] = round((now - c["published_at"]) / 3600, 1) if c.get("published_at") else None
    return s


PER_PLATFORM = {"YouTube": 8, "YouTube Shorts": 6}


def _hits(found: list[dict], today_urls: set, now: float) -> list[dict]:
    """每個平台觀看最高的幾支，最多 14 支，今天已經在頁面上的不重複放。"""
    by: dict[str, list] = {}
    for c in found:
        if c["url"] in today_urls:
            continue
        by.setdefault(analyze.platform(c), []).append(c)
    out = []
    for plat, rows in by.items():
        rows.sort(key=lambda c: -(c["metrics"].get("views") or 0))
        out += rows[:PER_PLATFORM.get(plat, 3)]
    out.sort(key=lambda c: -(c["metrics"].get("views") or 0))
    return [_sample(c, now) for c in out[:14]]


def _fact_block(i: int, t: dict) -> str:
    lines = [f"T{i} | {t['name']} | {t['summary']}"]
    for plat, e in (t.get("earliest_by") or {}).items():
        lines.append(f"  {plat} 最早能查到：{e['published']}《{e['title'][:50]}》 {fmt_num(e.get('views'))} 次觀看")
    q = t.get("bili_quarters") or []
    if q:
        lines.append("  YouTube 每年相關度最高的結果裡標題含「" + (t["terms"].get("en") or t["terms"].get("zh") or "") +
                     "」的支數（從四年前到現在）：" + "、".join(f"{_bj(x['start'], '%Y')}年 {x['match']}" for x in q))
    for h in t["hits"][:6]:
        lines.append(f"  爆款：{h['platform']} {h['published']}《{h['title'][:50]}》 {fmt_num(h.get('views'))} 次觀看")
    return "\n".join(lines)


def run(day: str, labels=None) -> dict | None:
    """深挖當天的影片題材（給了 ``labels`` 就只挖那些標籤的）。沒有符合的就回傳 None。"""
    con = db.connect()
    row = con.execute("SELECT payload FROM briefs WHERE day=?", (day,)).fetchone()
    if not row:
        raise SystemExit(f"no brief for {day}")
    brief = json.loads(row["payload"])
    topics = [t for t in (brief.get("sections") or {}).get("video") or [] if not labels or t["label"] in labels]
    if not topics:
        log(f"deep: no video topics on {day}" + (f" labelled {'/'.join(labels)}" if labels else ""))
        return None
    mark = net.ledger.snapshot()
    now = time.time()
    lines = "\n".join(
        f"{i} | {t['name']} | {t['summary'][:80]} | 「{t.get('youtube_term') or ''}」 | "
        + "；".join(s["title"][:30] for s in t["samples"][:3]) for i, t in enumerate(topics))
    terms = {x.get("id"): x for x in (llm.ask_json(TERMS_PROMPT.format(lines=lines), max_tokens=3000).get("topics") or [])}
    out = []
    for i, t in enumerate(topics):
        tm = terms.get(i) or {}
        en = (tm.get("en") or t.get("youtube_term") or "").strip()
        zh = (tm.get("zh") or "").strip()
        term = en or zh
        log(f"deep {t['name']}: en={en!r} zh={zh!r}")
        found, earliest_by, d = [], {}, {"name": t["name"], "label": t["label"], "kind": t["kind"], "summary": t["summary"],
                                          "thread_id": t.get("thread_id"), "terms": {"zh": zh, "en": en, "home": "overseas"}}
        if term:
            yt = youtube_history(term)
            found += yt["hits"]
            if yt["earliest"]:
                earliest_by["YouTube"] = yt["earliest"]
            yh = yearly_history(term, now)
            d["bili_quarters"], d["bili_older"] = yh["years"], yh["older_than_window"]     # 欄位名沿用舊版，頁面才看得懂
            found += yh["hits"]
            dated = [h for h in yh["hits"] if h.get("published_at")]
            if dated and (not earliest_by.get("YouTube") or min(h["published_at"] for h in dated) < earliest_by["YouTube"]["published_at"]):
                earliest_by["YouTube"] = min(dated, key=lambda h: h["published_at"])
        d["earliest_by"] = {p: _sample(c, now) for p, c in earliest_by.items()}
        first = min(earliest_by.items(), key=lambda kv: kv[1]["published_at"], default=None)
        d["earliest"] = {"platform": first[0], **_sample(first[1], now)} if first else None
        seen = {c["id"] for c in found}
        d["matched"] = {"YouTube": len(seen)}
        d["hits"] = _hits(list({c["id"]: c for c in found}.values()), {s["url"] for s in t["samples"]}, now)
        out.append(d)
    try:
        notes = {x.get("id"): x for x in (llm.ask_json(NOTE_PROMPT.format(
            blocks="\n\n".join(_fact_block(i, d) for i, d in enumerate(out))), max_tokens=6000).get("topics") or [])}
    except Exception as exc:
        log(f"deep notes failed: {exc}")
        notes = {}
    for i, d in enumerate(out):
        n = notes.get(i) or notes.get(f"T{i}") or {}
        d["note"] = {k: n.get(k) or "" for k in ("origin", "spread", "now")}
    thumbs = pipeline.cache_samples([s for d in out for s in d["hits"] + list(d["earliest_by"].values())])
    for d in out:
        if d.get("earliest"):
            d["earliest"]["thumb"] = d["earliest_by"][d["earliest"]["platform"]]["thumb"]
    payload = {"day": day, "generated_bj": datetime.now(BJ).strftime("%Y-%m-%d %H:%M"),
               "topics": out, "labels": list(labels or []), "cost": net.ledger.summary(since=mark), "thumbs": thumbs}
    db.save_deep(con, day, payload)
    return payload
