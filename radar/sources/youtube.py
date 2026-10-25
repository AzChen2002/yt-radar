"""YouTube，走官方的 YouTube Data API v3（免費，每天 10,000 配額單位）。

- ``search``：賽道關鍵字，最近 7 天、觀看數最高的在前。一個關鍵字 = 一次 search.list（100 單位）
  加一次 videos.list（1 單位，拿觀看、按讚、時長）。
- ``channel_followers``：頻道訂閱數（1 單位），只替可能被選上的幾十支影片查。
- ``window_count``：新鮮度探測。今年和這個詞相關的影片裡，標題真的對得上的有幾條、其中幾條是最近 10 天發的。
- ``search_any_time``：不限日期的搜尋，給題材深挖用。
"""

from __future__ import annotations

import re
import time
from datetime import datetime, timedelta, timezone

from .. import config, net
from .common import iso_ts, log

TZ8 = timezone(timedelta(hours=8))


def _rfc3339(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


_ISO_DUR = re.compile(r"P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?")


def parse_duration(s: str) -> int | None:
    """``PT1H2M3S`` -> 秒數。直播和沒有時長的回傳 None。"""
    m = _ISO_DUR.fullmatch(s or "")
    if not m or not any(m.groups()):
        return None
    d, h, mi, sec = (int(x or 0) for x in m.groups())
    return d * 86400 + h * 3600 + mi * 60 + sec or None


def _int(v) -> int | None:
    try:
        return int(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _thumb(sn: dict) -> str:
    th = sn.get("thumbnails") or {}
    for k in ("maxres", "standard", "high", "medium", "default"):
        if th.get(k, {}).get("url"):
            return th[k]["url"]
    return ""


def _card(v: dict, query: str, short: bool = False) -> dict | None:
    """videos.list 的一筆（snippet + statistics + contentDetails）變成雷達的一條內容。"""
    vid = v.get("id")
    sn, st, cd = v.get("snippet") or {}, v.get("statistics") or {}, v.get("contentDetails") or {}
    if not vid or not sn:
        return None
    dur = parse_duration(cd.get("duration") or "")
    if sn.get("liveBroadcastContent") not in (None, "none"):
        return None          # 直播和預告沒有可比的觀看數
    is_short = short or (dur is not None and dur <= 180)
    return {
        "id": f"youtube:{vid}", "source": "youtube", "kind": "video",
        "title": sn.get("title") or "", "text": (sn.get("description") or "")[:300],
        "url": f"https://www.youtube.com/shorts/{vid}" if is_short else f"https://www.youtube.com/watch?v={vid}",
        "thumb": _thumb(sn) or f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg",
        "author": sn.get("channelTitle") or "", "author_id": sn.get("channelId") or "",
        "published_at": iso_ts(sn.get("publishedAt") or ""),
        "metrics": {"views": _int(st.get("viewCount")), "likes": _int(st.get("likeCount")),
                    "comments": _int(st.get("commentCount"))},
        "extra": {"duration": dur, "short": is_short, "published_text": (sn.get("publishedAt") or "")[:10],
                  "tag": " ".join(sn.get("tags") or [])[:200]},
        "query": query,
    }


def _search_page(q: str, *, published_after: float | None = None, order: str = "viewCount",
                 duration: str | None = None, page_token: str | None = None) -> dict:
    params = {"part": "snippet", "q": q, "type": "video", "order": order, "maxResults": 50,
              "safeSearch": "moderate"}
    if published_after:
        params["publishedAfter"] = _rfc3339(published_after)
    if duration:
        params["videoDuration"] = duration        # short = 4 分鐘以內
    if config.YT_REGION:
        params["regionCode"] = config.YT_REGION
    if config.YT_LANG:
        params["relevanceLanguage"] = config.YT_LANG
    if page_token:
        params["pageToken"] = page_token
    return net.youtube_api("search", params)


def _details(ids: list[str]) -> list[dict]:
    """videos.list，一次最多 50 支。"""
    out = []
    for i in range(0, len(ids), 50):
        data = net.youtube_api("videos", {"part": "snippet,statistics,contentDetails", "id": ",".join(ids[i:i + 50]),
                                          "maxResults": 50})
        out += data.get("items") or []
    return out


def search(keywords: list[str] | None = None, days: int = 7, duration: str | None = None) -> list[dict]:
    """``duration="short"`` 只看 4 分鐘以內的影片，Shorts 依觀看數排序時會從這裡浮出來。"""
    out = []
    since = time.time() - days * 86400
    for kw in keywords or config.YT_KEYWORDS:
        try:
            page = _search_page(kw, published_after=since, duration=duration)
            ids = [it["id"]["videoId"] for it in page.get("items") or [] if (it.get("id") or {}).get("videoId")]
            rows = _details(ids) if ids else []
        except net.FetchError as exc:
            log(f"youtube search {kw}: {exc}")
            continue
        for v in rows:
            c = _card(v, f"youtube:{kw}" + (":short" if duration else ""), short=(duration == "short"))
            if c:
                out.append(c)
    return out


def search_any_time(term: str, order: str = "relevance") -> list[dict]:
    """不限上傳日期：相關度排序，或 ``order="viewCount"`` 看史上觀看最高的。"""
    page = _search_page(term, order=order)
    ids = [it["id"]["videoId"] for it in page.get("items") or [] if (it.get("id") or {}).get("videoId")]
    return [c for c in (_card(v, f"youtube:{term}") for v in (_details(ids) if ids else [])) if c]


def channel_followers(channel_id: str) -> int | None:
    try:
        data = net.youtube_api("channels", {"part": "statistics", "id": channel_id})
    except net.FetchError as exc:
        log(f"youtube channel {channel_id}: {exc}")
        return None
    for it in data.get("items") or []:
        st = it.get("statistics") or {}
        if st.get("hiddenSubscriberCount"):
            return None
        return _int(st.get("subscriberCount"))
    return None


_STOP = {"the", "a", "an", "and", "or", "of", "for", "to", "in", "on", "with", "my", "how", "i", "is", "by", "from", "video", "videos"}


def _tokens(term: str) -> list[str]:
    return [w for w in re.findall(r"[a-z0-9]+|[^\x00-\x7f]+", term.lower()) if w not in _STOP and len(w) >= 2]


# 對得上的標題可以漏掉這些字；有辨識度的字（"tv"、"food"、"slide"）一個都不能少。
_SOFT = {"ai", "code", "claude", "chatgpt", "gpt", "gemini", "3d", "2d", "animation", "animated", "cartoon", "story",
         "short", "film", "tutorial", "full", "guide", "free", "new", "satisfying"}


def title_matches(term: str, title: str) -> bool:
    """搜尋詞裡每個有辨識度的字都要在標題裡；最多隻能少一個「軟」字。"""
    toks = _tokens(term)
    if not toks:
        return False
    low = title.lower()
    missing = [t for t in toks if t not in low]
    return not missing or (len(missing) == 1 and missing[0] in _SOFT and len(toks) >= 2)


def window_count(term: str) -> dict:
    """今年和 ``term`` 最相關的 50 支影片；標題真的對得上的裡面，有幾支是最近 10 天發的。
    幾乎全是最近的 = 這個形式是新的。（依觀看排序會讓大的舊片蓋住小的新形式，依日期排序又看不到舊的。）
    只用 search.list 的 snippet（100 單位），不再查 videos.list。"""
    now = time.time()
    year_start = datetime.now(TZ8).replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0).timestamp()
    page = _search_page(term, published_after=year_start, order="relevance")
    yr = []
    for it in page.get("items") or []:
        vid, sn = (it.get("id") or {}).get("videoId"), it.get("snippet") or {}
        if vid and sn.get("title"):
            yr.append({"title": sn["title"], "url": f"https://www.youtube.com/watch?v={vid}",
                       "published_at": iso_ts(sn.get("publishedAt") or ""), "published": (sn.get("publishedAt") or "")[:10]})
    hits = [c for c in yr if title_matches(term, c["title"])]
    recent = [c for c in hits if c.get("published_at") and now - c["published_at"] <= config.YT_PROBE_DAYS * 86400]
    ages = [round((now - c["published_at"]) / 86400) for c in hits if c.get("published_at")]
    return {
        "year_n": len(yr), "match": len(hits), "match_recent": len(recent),
        "oldest_match_days": max(ages) if ages else None,
        "samples": [{"title": c["title"], "url": c["url"], "views": None, "published": c["published"]} for c in hits[:3]],
    }
