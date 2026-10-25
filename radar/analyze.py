"""把一天採到的內容整理成分區：影片題材、開源倉庫、工具與模型、資訊。

The model groups and names things and writes the comments. Every number shown, and every
新起 / 升溫 / 持續 label, is computed here from collected data and probes, and each label carries
the list of signals that fired (``basis``) so a pick can be checked afterwards.
"""

from __future__ import annotations

import re
import statistics
import time
from datetime import datetime, timedelta, timezone

from . import config, db, llm
from .score import fmt_num
from .sources import youtube
from .sources.common import blocked, in_lane, log, off_lane

BJ = timezone(timedelta(hours=8))      # 台北時間（UTC+8）；變數名沿用舊版


def _bj(ts: float | None, fmt: str = "%m-%d") -> str:
    return datetime.fromtimestamp(ts, BJ).strftime(fmt) if ts else "?"


def _age(h) -> str:
    if h is None:
        return "?"
    return f"{int(h)}小時" if h < 48 else f"{int(h // 24)}天"


# ------------------------------------------------------------------ video

VIDEO_PROMPT = """你是 AI 影片賽道的情報編輯。下面是最近 7 天 YouTube 上和 AI 相關的熱門影片（長片和 Shorts 都有）。請把它們按「題材 / 玩法」歸類，找出值得 AI 影片創作者注意的東西。

「題材」是觀眾買單的那個內容形式，粒度要細到別人能照著做，例如「印地語蔬菜家族 3D 動畫」「AI 美食滑梯解壓」「用 Claude 做 2D 動畫教學」「AI 殭屍短劇連載」「貓咪第一人稱 Vlog」。同一個梗、同一種畫面形式、同一套劇情結構、同一個新工具的同一種用法，歸為一個題材；不同的就拆開。

禁止的大籃子名字：「AI 工具教學」「AI 影片教學」「AI Agent 討論」「AI 副業賺錢」「AI 科普」「AI 資訊」這類說不出具體形式的；名字裡也不要用括號或斜線列舉幾種型別（例如「AI 短劇（懸疑/恐怖/科幻）」），那是好幾個題材，要拆開，或者只留證據最強的那一種。教學類必須寫清用什麼工具做出什麼效果（例如「用 Seedance 做產品廣告」）；只是在聊 AI 新聞、觀點、評測的影片不屬於這裡，直接不放進任何題材。

之前幾天識別過的題材（同一個題材請在 same_as 裡寫它的編號，例如 "T12"；不是同一個就寫 null）：
{past}

影片（編號 | 平台 | 發布 | 觀看 | 每小時觀看 | 按讚 | 訂閱 | 觀看÷訂閱 | 時長 | 頻道 | 標題 | 搜尋詞）：
{lines}

只輸出一個 JSON 物件（不要 Markdown 程式碼區塊）：
{{"topics": [{{
  "name": "題材名，14 個字以內，說清楚是什麼形式",
  "kind": "內容題材 | 製作玩法 | 平台活動 | 其他",
  "videos": [屬於這個題材的影片編號],
  "summary": "觀眾在看什麼、這個形式的看點是什麼，兩句話以內，只根據標題和資料",
  "youtube_term": "在 YouTube 搜這個題材用的搜尋詞，2 到 5 個詞，用這類影片標題裡最常見的語言（多半是英文）；選最能代表這個題材、又能把它和別的 AI 影片分開的詞（例如「vegetable family」「food slide」「Claude Code TV」），不要用「AI video」「AI」這種泛詞",
  "ai_relevance": 0 到 100 的整數,
  "same_as": "T 編號或 null"
}}]}}

規則：
- 同一個題材同時出現在長片和 Shorts、或同時有不同語言的版本，歸在一起，這是很強的訊號。
- 一個題材通常至少 2 支影片；只有 1 支的，必須是觀看÷訂閱很高（小頻道爆了）才單獨列。最多列 16 個，按值得關注的程度從高到低。
- ai_relevance：100 = 用 AI 做出來的內容或 AI 工具本身；60 = 和 AI 強相關的討論；30 以下 = 只是標題裡帶了 AI，和 AI 影片創作關係不大。和 AI 無關的影片直接不放進任何題材。
- 同名不等於同一個東西，看標題判斷。官方 MV、真人影視、遊戲官方內容、動漫劇情解說、遊戲實況這類看不出是 AI 做的，不要放進任何題材。
- 不要寫任何數字，不要編造標題裡看不出的事實。用繁體中文輸出。"""


PLATFORM = {"youtube": "YouTube"}


def platform(v: dict) -> str:
    if v["source"] == "youtube" and v["extra"].get("short"):
        return "YouTube Shorts"
    return PLATFORM.get(v["source"], v["source"])


def _video_line(i: int, v: dict) -> str:
    s, m = v["signals"], v["metrics"]
    dur = v["extra"].get("duration")
    dur_s = f"{dur // 60}:{dur % 60:02d}" if dur else "?"
    return " | ".join(str(x) for x in (
        i, platform(v).replace("YouTube Shorts", "Shorts").replace("YouTube", "YT"), _bj(v.get("published_at")),
        fmt_num(m.get("views")), fmt_num(s.get("vph")), fmt_num(m.get("likes")), fmt_num(m.get("followers")),
        s.get("vsr") if s.get("vsr") is not None else "?", dur_s, (v.get("author") or "")[:16],
        (v.get("title") or "").replace("\n", " ")[:80], (v.get("query") or "").split(":", 1)[-1]))


def _past_lines(past: list[dict]) -> str:
    if not past:
        return "（還沒有歷史，這是第一次執行）"
    return "\n".join(f"T{p['thread_id']} | {p['name']} | 首次 {p['first_day']} | 最近 {p['day']}" for p in past[:60])


def _resolve_same(same, past: list[dict]):
    if not same or not isinstance(same, str):
        return None
    try:
        tid = int(same.strip().lstrip("Tt"))
    except ValueError:
        return None
    return next((p for p in past if p["thread_id"] == tid), None)


def _yt_probe(con, term: str, now: float) -> dict | None:
    key = f"youtube3:{term}:{_bj(now, '%Y-%m-%d')}"
    cached = db.probe_get(con, key)
    if cached is not None:
        return cached
    try:
        out = youtube.window_count(term)
    except Exception as exc:
        log(f"youtube probe {term}: {exc}")
        return None
    out["term"] = term
    db.probe_put(con, key, out)
    return out


def _label_video(t: dict, memory_hit: dict | None, history_days: int) -> tuple[str, list[str]]:
    """從彼此獨立的訊號決定標籤；「新起」至少要兩條同時成立。

    - term_new：YouTube 今年標題對得上的影片裡，六成以上是最近 10 天發的（這個形式以前幾乎沒有）。
    - burst：三成到六成是最近發的（以前就有，最近明顯變多）。
    - evergreen：大多是舊的（一直有人做）。
    - breadth / outlier：3 個以上頻道在做、或小頻道單支爆了。這兩條只能「支援」新起，不能單獨成立。
    - unseen：雷達有三天以上歷史、卻從沒記錄過這個題材。
    """
    basis, strong = [], set()
    py = t.get("probe_youtube")
    if py and py.get("match", 0) >= 3:
        mr, mt = py["match_recent"], py["match"]
        line = f"YouTube 搜「{py['term']}」：今年的相關影片裡 {mt} 支標題對得上，其中 {mr} 支是最近 {config.YT_PROBE_DAYS} 天發的"
        if mr / mt >= 0.6:
            basis.append(line + "，以前幾乎沒有")
            strong.add("term_new")
        elif mr / mt >= 0.3:
            basis.append(line)
            strong.add("burst")
        else:
            basis.append(line + f"，最早的是 {py.get('oldest_match_days')} 天前")
            strong.add("evergreen")
    elif py:
        basis.append(f"YouTube 搜「{py['term']}」：今年的相關影片裡標題對得上的不到 3 支，看不出新舊")
    m = t["metrics"]
    if m["authors"] >= 3:
        basis.append(f"{m['authors']} 個不同頻道在做")
        strong.add("breadth")
    if (m.get("max_vsr") or 0) >= 5:
        o = m.get("outlier") or {}
        if o.get("followers") is not None:
            basis.append(f"有小頻道爆了：{fmt_num(o['followers'])} 訂閱的頻道單支 {fmt_num(o['views'])} 次觀看")
        else:
            basis.append(f"有小頻道爆了：觀看是訂閱數的 {m['max_vsr']} 倍")
        strong.add("outlier")
    if memory_hit:
        basis.append(f"雷達 {memory_hit['first_day']} 起就在記錄")
    elif history_days >= 3:
        basis.append("雷達之前沒記錄過")
        strong.add("unseen")

    support = {"breadth", "outlier"} & strong
    # 「新起」要有「這個形式以前幾乎不存在」的證據：搜尋探測說它是新的，或（雷達有了歷史之後）一波雷達從沒記錄過的升溫。
    # 頻道數和小頻道爆款只能支援，不能單獨成立。
    if "term_new" in strong and support:
        return "新起", basis
    if {"unseen", "burst"} <= strong and support:
        return "新起", basis
    if "burst" in strong and support:
        return "升溫", basis
    if "evergreen" in strong or memory_hit:
        return "持續", basis
    return "觀察", basis


def _topic_metrics(members: list[dict]) -> dict:
    views = [v["metrics"].get("views") or 0 for v in members]
    vph = [v["signals"].get("vph") or 0 for v in members]
    vsr = [v["signals"].get("vsr") for v in members if v["signals"].get("vsr") is not None]
    best = max((v for v in members if v["signals"].get("vsr") is not None), key=lambda v: v["signals"]["vsr"], default=None)
    pubs = [v["published_at"] for v in members if v.get("published_at")]
    return {
        "videos": len(members), "authors": len({(v["source"], v.get("author_id") or v.get("author")) for v in members}),
        "platforms": sorted({PLATFORM.get(v["source"], v["source"]) for v in members}),
        "views_total": sum(views), "views_max": max(views or [0]), "vph_max": max(vph or [0]),
        "likes_total": sum(v["metrics"].get("likes") or 0 for v in members if v["metrics"].get("views") is None),
        "max_vsr": max(vsr) if vsr else None,
        "outlier": {"views": best["metrics"].get("views"), "followers": best["metrics"].get("followers"),
                    "title": best["title"]} if best else None,
        "earliest": min(pubs) if pubs else None,
        "median_age_h": round(statistics.median(v["signals"]["age_h"] for v in members if v["signals"].get("age_h") is not None), 1)
        if any(v["signals"].get("age_h") is not None for v in members) else None,
    }


def _sample(v: dict) -> dict:
    return {"title": v["title"], "url": v["url"], "thumb": v.get("thumb") or "", "author": v.get("author") or "",
            "platform": platform(v), "duration": v["extra"].get("duration"), "vertical": bool(v["extra"].get("vertical")),
            "views": v["metrics"].get("views"), "likes": v["metrics"].get("likes"), "followers": v["metrics"].get("followers"),
            "vsr": v["signals"].get("vsr"), "vph": v["signals"].get("vph"), "age_h": v["signals"].get("age_h"),
            "published": _bj(v.get("published_at"), "%m-%d %H:%M")}


# A topic name made only of these says no concrete format ("AI副業賺錢教程"), so it can't be copied.
_GENERIC = re.compile(r"AI|ai|影片|视频|教程|教學|副業|副业|賺錢|赚钱|變現|变现|工具|科普|資訊|资讯|討論|讨论|合集|分享|推薦|推荐|測評|测评|評測|评测|盤點|盘点|技巧|方法|攻略|入門|入门|實戰|实战|\s")


def generic_name(name: str) -> bool:
    return len(_GENERIC.sub("", name or "")) < 2


POOL_SIZES = {"youtube": 90}   # 給模型看的候選數


def video_topics(con, day: str, videos: list[dict], report: dict, history_days: int) -> list[dict]:
    now = time.time()
    cand = [v for v in videos if v["signals"].get("age_h") is not None and v["signals"]["age_h"] <= 8 * 24
            and in_lane(" ".join(str(x) for x in (v.get("title"), v.get("text"), v["extra"].get("tag"), v.get("author"))))
            and not blocked(v.get("title"))]
    pool = []
    for src, n in POOL_SIZES.items():
        pool += sorted((v for v in cand if v["source"] == src), key=lambda v: -v["signals"]["heat"])[:n]
    if not pool:
        return []
    past = db.recent_topics(con, "video", day)
    lines = "\n".join(_video_line(i, v) for i, v in enumerate(pool))
    out = llm.ask_json(VIDEO_PROMPT.format(past=_past_lines(past), lines=lines), max_tokens=10000)
    topics = []
    dropped = report.setdefault("dropped", {})
    for raw in (out.get("topics") or [])[:16]:
        members = [pool[i] for i in raw.get("videos") or [] if isinstance(i, int) and 0 <= i < len(pool)]
        if not members or (raw.get("ai_relevance") or 0) < 55:
            continue
        if generic_name(raw.get("name")) or (raw.get("kind") == "其他" and (raw.get("ai_relevance") or 0) < 80):
            dropped["說不出具體形式的影片題材"] = dropped.get("說不出具體形式的影片題材", 0) + 1
            continue
        if len(members) == 1:   # 單支影片要自己成一個題材，必須是明顯的小頻道爆款
            v = members[0]
            if (v["signals"].get("vsr") or 0) < 20 or (v["metrics"].get("views") or 0) < 200_000:
                continue
        members.sort(key=lambda v: -(v["metrics"].get("views") or 0))
        t = {"name": (raw.get("name") or "").strip()[:30], "kind": raw.get("kind") or "其他",
             "summary": raw.get("summary") or "", "ai_relevance": raw.get("ai_relevance"),
             "youtube_term": (raw.get("youtube_term") or "").strip(),
             "metrics": _topic_metrics(members), "samples": [_sample(v) for v in members[:6]],
             "member_ids": [v["id"] for v in members], "same_as": raw.get("same_as")}
        t["weight"] = round(sum(v["signals"]["heat"] for v in members[:5]) / 5 + 6 * min(t["metrics"]["authors"], 5), 1)
        topics.append(t)
    topics.sort(key=lambda t: -t["weight"])

    yt_budget = 8      # 每次探測 100 配額單位
    for t in topics[:12]:
        if t["youtube_term"] and yt_budget > 0:
            t["probe_youtube"] = _yt_probe(con, t["youtube_term"], now)
            yt_budget -= 1
    kept = []
    for t in topics:
        hit = _resolve_same(t.pop("same_as", None), past)
        t["label"], t["basis"] = _label_video(t, hit, history_days)
        if not t["basis"]:
            continue
        t["first_day"] = hit["first_day"] if hit else day
        t["thread_id"] = db.save_topic(con, day, "video", t["name"], [t["youtube_term"]], t["label"],
                                       hit["thread_id"] if hit else None, t["first_day"],
                                       {"metrics": t["metrics"], "basis": t["basis"]})
        t["days_seen"] = len(db.thread_days(con, t["thread_id"]))
        kept.append(t)
    report["video_topics"] = len(kept)
    return kept


# ------------------------------------------------------------------ repos, models, stories

TOOLS_PROMPT = """你是 AI 工具情報編輯，讀者是做 AI 影片、AI 內容和小產品的中文創作者（會用 Claude Code、Codex 這類工具，不一定是專業工程師）。下面是今天 GitHub 上漲星快的新倉庫、Hugging Face 熱門模型、Hacker News 上的 AI 討論，以及台灣科技媒體的 AI 資訊和 Product Hunt 新產品。

請給每條寫一句中文說明，並判斷對這群人有沒有用。

GitHub 倉庫（編號 | 倉庫 | 星數 | 日均漲星 | 今日漲星 | 建立 | 語言 | 簡介）：
{repos}

Hugging Face（編號 | 型別 | 名稱 | 按讚 | 下載 | 建立 | 標籤）：
{models}

Hacker News（編號 | 標題 | 分數 | 評論 | 連結）：
{stories}

資訊與新產品（編號 | 來源 | 標題 | 摘要）：
{news}

只輸出一個 JSON 物件（不要 Markdown 程式碼區塊）：
{{"items": [{{
  "ref": "R3 / M2 / H5 / N1 這樣的編號",
  "zh": "這是什麼，一句話，30 個字以內，說人話（資訊就寫發生了什麼）",
  "use": "對 AI 影片創作者 / 內容創作者 / 小產品開發者有什麼用，一句話；沒用就寫空字串",
  "group": "AI 影片與影象 | 音訊與配音 | 內容創作與經營 | Agent 與程式開發 | 模型 | 其他",
  "value": 0 到 100 的整數
}}]}}

規則：
- 每個編號都要給結果。和 AI 無關的（普通遊戲、系統工具、灰產）value 給 0。
- 這群人關心：能做影片、圖片、配音、音樂的新模型和工具；能幫他們寫指令碼、剪輯、經營頻道、批次出片的工具；Claude Code / Codex 的好用技能和 Agent 工具；新的賺錢打法。
- 資安稽核、漏洞掃描、滲透測試、資料庫、編譯器、維運監控、金融交易、區塊鏈這類只對專業工程師或別的行業有用的，value 不超過 25。公司財報、融資、人事、訴訟類資訊 value 不超過 30。
- Hugging Face Spaces 裡明顯是刷榜、轉載別人模型、賭博、色情的，value 給 0。
- 不要編造簡介裡看不出的功能。用繁體中文輸出。"""

TOOL_CAPS = {"repos": 10, "models": 8, "stories": 6, "news": 8}
GROUP_CAPS = {"Agent 與程式開發": 5}      # coding/agent repos are plentiful; keep room for video/audio tools


_VARIANT = re.compile(r"[-_.](sft|base|instruct|comfy|diffusers|fp8|fp4|nvfp4|gguf|awq|gptq|turbo|preview|demo|\d+-?steps?.*)$", re.I)


def model_family(name: str) -> str:
    """'OpenMOSS-Team/MOSS-Image-SFT' and 'Comfy-Org/MOSS-Image' are one model for the reader."""
    base = name.split("/")[-1].lower()
    while _VARIANT.search(base):
        base = _VARIANT.sub("", base)
    return base


def _hf_keep(h: dict) -> bool:
    """Creator pipelines from anyone; other models and every Space only from known labs."""
    owner = (h.get("author") or "").lower()
    if h["kind"] == "space":
        return owner in config.HF_ORGS
    return h["extra"].get("pipeline") in config.HF_CREATOR_PIPELINES or owner in config.HF_ORGS


def tools(repos: list[dict], hf: list[dict], hn: list[dict], report: dict, news: list[dict] | None = None) -> dict:
    news = news or []
    dropped = report.setdefault("dropped", {})
    n_repos = len(repos)
    repos = [r for r in repos if not blocked(r["title"] + " " + (r.get("text") or ""))
             and not off_lane(r["title"] + " " + (r.get("text") or "") + " " + " ".join(r["extra"].get("topics") or []))]
    if n_repos - len(repos):
        dropped["安全、金融、運維類倉庫"] = n_repos - len(repos)
    hf_all = [h for h in hf if not blocked(h["title"] + " " + " ".join(h["extra"].get("tags") or []))]
    hf = [h for h in hf_all if _hf_keep(h)]
    if len(hf_all) - len(hf):
        dropped["Hugging Face 個人轉載和刷榜專案"] = len(hf_all) - len(hf)
    repos = sorted(repos, key=lambda r: -r["signals"]["heat"])[:28]
    hf = sorted(hf, key=lambda r: r["metrics"].get("rank") or 99)
    model_ids = {h["title"].lower() for h in hf if h["kind"] == "model"}
    hf = [h for h in hf if h["kind"] == "model"][:14] + \
         [h for h in hf if h["kind"] == "space" and h["title"].lower() not in model_ids][:6]
    hn = sorted(hn, key=lambda h: -(h["metrics"].get("points") or 0))[:14]
    news = [n for n in news if not off_lane(n["title"])][:40]
    if not (repos or hf or hn or news):
        return {"repos": [], "models": [], "stories": [], "news": []}
    rl = "\n".join(" | ".join(str(x) for x in (
        f"R{i}", r["title"], fmt_num(r["metrics"].get("stars")), r["signals"].get("stars_per_day") or "?",
        r["metrics"].get("stars_today") or "?", _bj(r.get("published_at"), "%Y-%m-%d"), r["extra"].get("language") or "?",
        (r.get("text") or "")[:160].replace("\n", " "))) for i, r in enumerate(repos))
    ml = "\n".join(" | ".join(str(x) for x in (
        f"M{i}", h["kind"], h["title"], h["metrics"].get("likes"), fmt_num(h["metrics"].get("downloads")),
        _bj(h.get("published_at"), "%Y-%m-%d"), ",".join(h["extra"].get("tags") or [])[:80] or h.get("text"))) for i, h in enumerate(hf))
    sl = "\n".join(" | ".join(str(x) for x in (
        f"H{i}", h["title"], h["metrics"].get("points"), h["metrics"].get("comments"), h["url"][:80])) for i, h in enumerate(hn))
    nl = "\n".join(" | ".join(str(x) for x in (
        f"N{i}", n["extra"].get("source_name") or n["source"], n["title"][:80], (n.get("text") or "")[:120].replace("\n", " ")))
        for i, n in enumerate(news))
    out = llm.ask_json(TOOLS_PROMPT.format(repos=rl or "（無）", models=ml or "（無）", stories=sl or "（無）",
                                           news=nl or "（無）"), max_tokens=9000)
    notes = {str(x.get("ref")).upper(): x for x in out.get("items") or []}

    def pack(prefix, rows, fn, cap, floor=45):
        res, names = [], set()
        for i, r in enumerate(rows):
            n = notes.get(f"{prefix}{i}") or {}
            key = model_family(r["title"]) if prefix == "M" else r["title"].lower()
            if (n.get("value") or 0) < floor or n.get("group") == "其他" or key in names:
                continue
            names.add(key)
            res.append({**fn(r), "zh": n.get("zh") or "", "use": n.get("use") or "", "group": n.get("group") or "其他",
                        "value": n.get("value")})
        res.sort(key=lambda x: -(x["value"] or 0))
        kept, per = [], {}
        for x in res:
            per[x["group"]] = per.get(x["group"], 0) + 1
            if per[x["group"]] <= GROUP_CAPS.get(x["group"], cap):
                kept.append(x)
        if len(res) - len(kept[:cap]):
            dropped["價值低或同類太多的工具條目"] = dropped.get("價值低或同類太多的工具條目", 0) + len(res) - len(kept[:cap])
        return kept[:cap]

    result = {
        "repos": pack("R", repos, lambda r: {
            "name": r["title"], "url": r["url"], "desc": r.get("text") or "", "stars": r["metrics"].get("stars"),
            "stars_today": r["metrics"].get("stars_today"), "stars_per_day": r["signals"].get("stars_per_day"),
            "stars_gain_per_day": r["signals"].get("stars_gain_per_day"), "created": _bj(r.get("published_at"), "%Y-%m-%d"),
            "language": r["extra"].get("language"), "from_trending": "github:trending" in (r.get("query") or "")},
            TOOL_CAPS["repos"], 60),
        "models": pack("M", hf, lambda h: {
            "name": h["title"], "url": h["url"], "kind": h["kind"], "likes": h["metrics"].get("likes"),
            "downloads": h["metrics"].get("downloads"), "created": _bj(h.get("published_at"), "%Y-%m-%d"),
            "tags": h["extra"].get("tags") or []}, TOOL_CAPS["models"]),
        "stories": pack("H", hn, lambda h: {
            "name": h["title"], "url": h["url"], "hn_url": h["extra"].get("hn_url"), "points": h["metrics"].get("points"),
            "comments": h["metrics"].get("comments"), "published": _bj(h.get("published_at"), "%m-%d %H:%M")},
            TOOL_CAPS["stories"], 50),
        "news": pack("N", news, lambda n: {
            "name": n["title"], "url": n["url"], "source_name": n["extra"].get("source_name") or n["source"],
            "votes": n["metrics"].get("votes"), "published": _bj(n.get("published_at"), "%m-%d %H:%M")},
            TOOL_CAPS["news"], 50),
    }
    report["repos"] = len(result["repos"])
    return result


# ------------------------------------------------------------------ headline

HEAD_PROMPT = """{role}今天的「AI 影片情報日報」頭條。讀者是{readers}AI 影片創作者（做 YouTube，用 AI 批次出片）和想找新專案、新選題、新工具的人。

下面是系統今天整理出來的候選，數字和「新起 / 升溫 / 持續」標籤都是系統按資料算的，你不能改數字，也不能編造候選裡沒有的事實。

影片題材（編號 V）：
{videos}

開源倉庫、工具和資訊（編號 R / M / H / N）：
{tools}

從中挑 6 到 8 條今天最值得看的，寫成頭條。頁面會把頭條對應的影片直接放出來，所以有影片作品的題材優先。標「新起」「升溫」的要優先考慮，它們是資料確認的冒頭訊號。挑選標準按重要性：
1. 突然冒頭、之前沒有的題材或玩法，有多個頻道在做或小頻道爆了的證據；
2. 創作者能複製、AI 能明顯降低成本、能做成系列的；
3. 對變現、新專案有直接啟發的大事（例如某個工具改變了生產方式）；
4. 真正有用的新工具或開源倉庫。
純熱鬧、和這群人無關的不要選，公司財務、資安漏洞、融資上市這類行業新聞不進頭條。收入、月入這類數字只有一個頻道在說、又沒有可查的連結的，不進頭條。一條頭條只講一件事，不要把幾件不相干的事拼成一條。
標題和點評的說法要和標籤一致：標「持續」「觀察」的不能寫成「爆發」「突然」「新出現」；S 級只給標「新起」「升溫」的題材或真正的行業大事。
標題和點評裡不要出現 V1、R3 這種編號，也不要寫「觀看訂閱比多少倍」這種說法，要說小頻道就直接說「幾百訂閱的頻道單支多少萬觀看」。

讀者在手機上掃一眼就要懂，所以每條分開寫：verdict 是判斷短句（"有手法，可以學一下""值得馬上測""先觀察""上過了"）；why 一句話說清為什麼值得看（觀眾買單的點、證據），不超過 40 個字；next_step 一個能馬上做的小測試，不超過 40 個字；risk 最大的坑，不超過 30 個字；comment 再用兩三句大白話補充能怎麼改（換 IP、換語言、換場景、換平台形式、用 AI 降成本），它預設收合。用{voice}，不要用"不是因為…而是因為…""真正值得學的是…"這類句式，不要冒號清單，不要"賦能""抓手"這種詞。

只輸出一個 JSON 物件（不要 Markdown 程式碼區塊）：
{{"summary": "今天整體在發生什麼，兩句話，像同事跟老闆口頭回報",
  "headline": [{{
    "refs": ["V2", "R1"],
    "title": "頭條標題，20 個字以內，說清楚是什麼事",
    "verdict": "一句話判斷，12 個字以內",
    "why": "為什麼值得看，一句話，40 個字以內",
    "next_step": "一個具體可執行的小測試或動作，40 個字以內",
    "risk": "最大的風險或坑，30 個字以內；沒有就寫空字串",
    "comment": "補充點評，兩三句話",
    "grade": "S | A | B | C",
    "audience": "創作者 | 做產品的人 | 都適合"
  }}]}}

等級：S = 少見、正在起勢、證據強、值得馬上測；A = 強推薦，做一次小測試；B = 有參考價值；C = 先放觀察。用繁體中文輸出。"""


def _video_brief(i, t):
    m = t["metrics"]
    top = "; ".join(f"《{s['title'][:40]}》{s['platform']} "
                    + (f"{fmt_num(s['views'])}觀看" if s.get('views') is not None else f"{fmt_num(s.get('likes'))}讚")
                    + f" 訂閱{fmt_num(s['followers'])} {_age(s['age_h'])}前"
                    for s in t["samples"][:3])
    return (f"V{i} | {t['name']} | {t['kind']} | 標籤 {t['label']} | {m['videos']} 支影片 {m['authors']} 個頻道 "
            f"{'/'.join(m['platforms'])} 合計 {fmt_num(m['views_total'])} 觀看 | 依據：{'；'.join(t['basis'])} | "
            f"概述：{t['summary']} | 代表作：{top}")


def head_voice(org: str | None = None, editor: str | None = None) -> dict:
    """Who writes the headlines and for whom, from RADAR_SITE_BY / RADAR_EDITOR (either may be empty)."""
    org = config.SITE_BY if org is None else org
    editor = config.EDITOR_NAME if editor is None else editor
    role = (f"你是{org}的情報主編，" if org else "你是一份每日 AI 影片情報的主編，") + (f"替{editor}寫" if editor else "寫")
    return {"role": role, "readers": f"{org}社群裡的 " if org else "",
            "voice": f"{editor}的說話方式" if editor else "大白話，像同事之間聊天"}


def headline(videos: list[dict], tl: dict, news: list[dict] | None = None) -> dict:
    vl = "\n".join(_video_brief(i, t) for i, t in enumerate(videos)) or "（無）"
    tlines = []
    for i, r in enumerate(tl.get("repos") or []):
        tlines.append(f"R{i} | {r['name']} | {r['zh']} | 星 {fmt_num(r['stars'])} 今日 +{r['stars_today'] or '?'} 日均 {r['stars_per_day'] or '?'} | 用處：{r['use']}")
    for i, r in enumerate(tl.get("models") or []):
        tlines.append(f"M{i} | {r['name']} | {r['zh']} | 按讚 {r['likes']} | 用處：{r['use']}")
    for i, r in enumerate(tl.get("stories") or []):
        tlines.append(f"H{i} | {r['name']} | {r['zh']} | HN {r['points']} 分 | 用處：{r['use']}")
    for i, r in enumerate(news or []):
        tlines.append(f"N{i} | {r['source_name']} | {r['name']} | {r['zh']} | 用處：{r['use']}")
    out = llm.ask_json(HEAD_PROMPT.format(videos=vl, tools="\n".join(tlines) or "（無）", **head_voice()), max_tokens=8000)
    items = []
    for h in out.get("headline") or []:
        refs = [str(r).upper() for r in h.get("refs") or []]
        links = []
        pools = {"V": videos, "R": tl.get("repos") or [], "M": tl.get("models") or [], "H": tl.get("stories") or [],
                 "N": news or []}
        for r in refs:
            try:
                idx = int(r[1:])
            except ValueError:
                continue
            pool = pools.get(r[0]) or []
            if 0 <= idx < len(pool):
                obj = pool[idx]
                if r[0] == "V":
                    links.append({"section": "video", "name": obj["name"], "label": obj["label"],
                                  "thread_id": obj.get("thread_id")})
                else:
                    links.append({"section": "news" if r[0] == "N" else "tools", "name": obj["name"], "url": obj["url"]})
        item = {**{k: h.get(k) or "" for k in ("title", "verdict", "why", "comment", "next_step", "risk", "grade", "audience")},
                "refs": refs, "links": links}
        labels = {l.get("label") for l in links if l.get("label")}
        rising = {"新起", "升溫"}
        if item["grade"].strip().upper().startswith("S") and labels and not labels & rising:
            item["grade"] = "A"     # S 級要有資料說是新的或正在升溫的題材
        items.append(item)
    return {"summary": out.get("summary") or "", "headline": items}
