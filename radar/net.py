"""HTTP 工具：帶重試的 GET / POST、YouTube Data API 客戶端，以及一本呼叫帳簿。

只用標準函式庫，所以每日工作可以直接從 cron 跑，不需要 venv。
每次會花配額或花錢的呼叫（YouTube、模型）都經過 ``ledger``，執行報告才知道這一期用了多少。
"""

from __future__ import annotations

import gzip
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from http.cookiejar import CookieJar

from . import config

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0 Safari/537.36")


class FetchError(RuntimeError):
    pass


class Ledger:
    """替執行報告記每個來源的呼叫次數、YouTube 配額單位和模型 token。"""

    def __init__(self) -> None:
        self.calls: dict[str, int] = {}
        self.failures: dict[str, int] = {}
        self.tokens: dict[str, list[int]] = {}
        self.units: dict[str, int] = {}       # YouTube 配額單位，按端點記

    def charge(self, endpoint: str) -> None:
        self.units[endpoint] = self.units.get(endpoint, 0) + config.YT_UNITS.get(endpoint, 1)

    def hit(self, name: str, ok: bool = True) -> None:
        bucket = self.calls if ok else self.failures
        bucket[name] = bucket.get(name, 0) + 1

    def add_tokens(self, model: str, prompt: int, completion: int) -> None:
        cur = self.tokens.setdefault(model, [0, 0])
        cur[0] += prompt
        cur[1] += completion

    def units_total(self) -> int:
        return sum(self.units.values())

    def snapshot(self) -> dict:
        return {"calls": dict(self.calls), "failures": dict(self.failures), "units": dict(self.units),
                "tokens": {k: list(v) for k, v in self.tokens.items()}}

    def summary(self, since: dict | None = None) -> dict:
        """到目前為止的用量，或從某次 ``snapshot()`` 之後的用量（深挖和每日執行在同一個程式裡）。"""
        s = since or {"calls": {}, "failures": {}, "units": {}, "tokens": {}}
        calls = {k: v - s["calls"].get(k, 0) for k, v in self.calls.items() if v - s["calls"].get(k, 0)}
        failures = {k: v - s["failures"].get(k, 0) for k, v in self.failures.items() if v - s["failures"].get(k, 0)}
        units = {k: v - s["units"].get(k, 0) for k, v in self.units.items() if v - s["units"].get(k, 0)}
        tokens = {m: [p - s["tokens"].get(m, [0, 0])[0], c - s["tokens"].get(m, [0, 0])[1]]
                  for m, (p, c) in self.tokens.items()}
        tokens = {m: t for m, t in tokens.items() if any(t)}
        llm_usd = 0.0
        for model, (p, c) in tokens.items():
            pin, pout = config.LLM_PRICE_USD_PER_M
            llm_usd += p / 1e6 * pin + c / 1e6 * pout
        return {
            "calls": calls,
            "failures": failures,
            "tokens": tokens,
            "youtube_units": sum(units.values()),
            "youtube_by_endpoint": units,
            "est_usd_llm": round(llm_usd, 4),
        }


ledger = Ledger()

_jar = CookieJar()
_opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(_jar))


def _read(resp) -> bytes:
    raw = resp.read()
    if resp.headers.get("Content-Encoding") == "gzip":
        raw = gzip.decompress(raw)
    return raw


def request(url: str, *, params: dict | None = None, headers: dict | None = None,
            body: dict | None = None, timeout: float = 30, retries: int = 2,
            name: str = "http") -> bytes:
    if params:
        url += ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
    h = {"User-Agent": UA, "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8"}
    h.update(headers or {})
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        h.setdefault("Content-Type", "application/json")
    last = ""
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, data=data, headers=h)
            with _opener.open(req, timeout=timeout) as resp:
                out = _read(resp)
            ledger.hit(name)
            return out
        except urllib.error.HTTPError as exc:
            last = f"HTTP {exc.code}"
            if exc.code in (400, 401, 403, 404):
                break
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as exc:
            last = type(exc).__name__
        time.sleep(1.5 * (attempt + 1))
    ledger.hit(name, ok=False)
    raise FetchError(f"{name} {last}: {url[:120]}")


def get_json(url: str, **kw):
    return json.loads(request(url, **kw).decode("utf-8", "replace"))


def get_text(url: str, **kw) -> str:
    return request(url, **kw).decode("utf-8", "replace")


# ------------------------------------------------------------------ YouTube Data API v3

def youtube_api(endpoint: str, params: dict, timeout: float = 30) -> dict:
    """GET ``/youtube/v3/<endpoint>``。每次呼叫按 config.YT_UNITS 記配額；
    一次執行用到 ``config.YT_MAX_UNITS`` 就停止呼叫，配額用完（quotaExceeded）也直接停。"""
    cost = config.YT_UNITS.get(endpoint, 1)
    if ledger.units_total() + cost > config.YT_MAX_UNITS:
        raise FetchError("這次執行的 YouTube 配額上限已用完（YT_MAX_UNITS）")
    key = config.YOUTUBE_API_KEY
    if not key:
        raise FetchError("YOUTUBE_API_KEY 沒有設定（見 .env.example）")
    url = f"{config.YT_API_BASE}/{endpoint}?" + urllib.parse.urlencode({**params, "key": key})
    last = ""
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                out = json.loads(_read(resp).decode("utf-8", "replace"))
            ledger.hit("youtube")
            ledger.charge(endpoint)
            return out
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace").replace(key, "[key]")
            last = f"HTTP {exc.code}"
            try:
                err = json.loads(detail).get("error") or {}
                reasons = {e.get("reason") for e in err.get("errors") or []}
                last += f" {','.join(r for r in reasons if r)}: {str(err.get('message'))[:120]}"
                if "quotaExceeded" in reasons or "dailyLimitExceeded" in reasons:
                    ledger.units[endpoint] = ledger.units.get(endpoint, 0) + config.YT_MAX_UNITS   # 今天到此為止
                    break
            except (ValueError, AttributeError):
                pass
            if exc.code not in (429, 500, 502, 503, 504):
                break
        except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
            last = type(exc).__name__
        time.sleep(2 * (attempt + 1))
    ledger.hit("youtube", ok=False)
    raise FetchError(f"YouTube {last}: {endpoint}")
