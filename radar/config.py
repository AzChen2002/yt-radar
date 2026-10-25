"""設定、關鍵字和來源預算。

金鑰、模型端點和站名都從環境變數讀。倉庫根目錄的 ``.env``（``KEY=VALUE`` 一行一個，見 ``.env.example``）
會先被讀進來，所以 cron 工作不需要 shell profile；環境裡已經設好的變數優先。

關鍵字清單就是「賽道」的定義：這裡把什麼算成「AI / AI 影片」。
可以自由改，每個採集器都從這個檔案讀設定。
"""

from __future__ import annotations

import json
import os
from pathlib import Path

HOME = Path.home()
ROOT = Path(__file__).resolve().parent.parent


def load_env_file(path: Path) -> None:
    """把 ``KEY=VALUE`` 行讀進 os.environ；空行和 ``#`` 註解略過，引號去掉，已經設定的變數不動。"""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip().removeprefix("export ").strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        if key and key not in os.environ:
            os.environ[key] = value


load_env_file(Path(os.environ.get("RADAR_ENV_FILE") or ROOT / ".env"))


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, "").strip() or default


DATA_DIR = Path(_env("RADAR_DATA_DIR") or HOME / ".local/share/yt-radar").expanduser()
DB_PATH = DATA_DIR / "radar.db"
SITE_DIR = DATA_DIR / "site"
LOG_DIR = DATA_DIR / "logs"

# ------------------------------------------------------------------ YouTube Data API v3（官方、免費）
# 到 https://console.cloud.google.com 建專案 → 啟用 YouTube Data API v3 → 建 API 金鑰。
# 每天免費 10,000 配額單位：一次 search.list 花 100 單位，videos.list / channels.list 各花 1 單位。
# 配額在太平洋時間午夜重置（台灣時間下午 3 點或 4 點）。
YOUTUBE_API_KEY = _env("YOUTUBE_API_KEY")
YT_API_BASE = "https://www.googleapis.com/youtube/v3"
YT_REGION = _env("YT_REGION")                 # 例如 TW；留空就不限制地區
YT_LANG = _env("YT_LANG")                     # 例如 zh-Hant；留空就不限制語言
YT_MAX_UNITS = int(_env("YT_MAX_UNITS", "9000"))      # 單次執行最多用掉的配額單位，到了就停止呼叫
YT_UNITS = {"search": 100, "videos": 1, "channels": 1}

# ------------------------------------------------------------------ 模型（任何 OpenAI 相容的對話介面）
# 預設是 Google Gemini 的免費層（https://aistudio.google.com 取金鑰），一期只呼叫 3 到 5 次。
# 也可以換成 Groq 免費層（https://api.groq.com/openai/v1）或本機 Ollama（http://localhost:11434/v1）。
LLM_BASE_URL = _env("LLM_BASE_URL", "https://generativelanguage.googleapis.com/v1beta/openai").rstrip("/")
LLM_API_KEY = _env("LLM_API_KEY")
LLM_MAIN = _env("LLM_MODEL", "gemini-2.5-flash")
# 每次請求額外帶上的欄位。DeepSeek 官方介面預設會長時間思考，對它自動關掉；其他端點不加東西。
LLM_EXTRA_BODY = json.loads(_env("LLM_EXTRA_BODY") or
                            ('{"thinking": {"type": "disabled"}}' if "api.deepseek.com" in LLM_BASE_URL else "{}"))
LLM_TIMEOUT = float(_env("LLM_TIMEOUT", "360"))
# 模型單價（每百萬 token，美元，「輸入,輸出」），只用來在執行記錄裡估算花費。免費層就是 0。
LLM_PRICE_USD_PER_M = tuple(float(x) for x in _env("LLM_PRICE_USD_PER_M", "0,0").split(",")[:2])

GH_TOKEN_ENV = "GITHUB_TOKEN"   # 選用；沒有就先試 gh 指令的登入，再不行就匿名搜尋

# 讀者看到的名字。RADAR_SITE_BY 是出品方（「<SITE_BY> · 每日」「<SITE_BY>出品」），RADAR_EDITOR 是點評用誰的口吻；都可以留空。
SITE_NAME = _env("RADAR_SITE_NAME", "AI 影片情報雷達")
SITE_BY = _env("RADAR_SITE_BY")
EDITOR_NAME = _env("RADAR_EDITOR")
# 分享卡片的 QR code 指向哪裡：這個頁面的公開網址，{day} 和 {item} 會被替換
# （head-1、video-<thread_id>、repo-1）。頁面看的是路徑後面的 hash，所以本機預設值可以測試；公開前改成你的網址。
SHARE_URL = _env("RADAR_SHARE_URL", "http://127.0.0.1:8792/#intel-radar?day={day}&item={item}")

SERVE_HOST = _env("RADAR_HOST", "127.0.0.1")
SERVE_PORT = int(_env("RADAR_PORT", "8792"))

# ------------------------------------------------------------------ 賽道

# YouTube 搜尋關鍵字：最近 7 天、依觀看數排序。先放內容形式（題材會從這裡冒出來），再放工具名。
# 中英文都可以放，YouTube 會各自搜。
YT_KEYWORDS = [
    "AI video", "AI animation", "AI short film", "AI cartoon story",
    "AI music video", "AI generated", "Seedance", "Kling AI", "Veo", "Sora",
    "faceless channel AI", "make money with AI", "Claude Code", "AI agent", "vibe coding",
    "AI 短片", "AI 動畫", "AI 短劇", "AI 影片", "AI 生成",
]

# 另外一輪只搜 4 分鐘以內的影片（Shorts 依觀看數排序時會從這裡浮出來）。
YT_SHORT_KEYWORDS = ["AI video", "AI animation", "AI cartoon story", "AI short film", "AI music video", "AI 動畫"]

# 免費的新聞 RSS / Atom。抓不到的來源會記在日誌裡然後跳過，不影響其他來源。
NEWS_FEEDS = [
    ("ithome", "iThome", "https://www.ithome.com.tw/rss"),
    ("technews", "科技新報", "https://technews.tw/feed/"),
    ("inside", "INSIDE", "https://www.inside.com.tw/feed/rss"),
    ("producthunt", "Product Hunt", "https://www.producthunt.com/feed?category=video"),
    ("producthunt", "Product Hunt", "https://www.producthunt.com/feed?category=artificial-intelligence"),
]
NEWS_HOURS = 48

# GitHub 搜尋（有 token 就免費好用）。{d} 是建立日期的下限。
GH_QUERIES = [
    "created:>{d} stars:>150",
    "created:>{d} stars:>40 video in:name,description,topics",
    "created:>{d} stars:>60 agent in:name,description,topics",
    "created:>{d} stars:>60 skill in:name,description,topics",
    "created:>{d} stars:>60 mcp in:name,description,topics",
]

# Hugging Face：這些 pipeline 的模型不管誰發的都和創作者有關；其他模型（LLM 等）和所有 Space 只收已知機構發的，
# 擋掉轉載、SEO 用的「humanizer」Space 和垃圾。
HF_CREATOR_PIPELINES = {
    "text-to-image", "image-to-image", "image-to-video", "text-to-video", "video-to-video", "text-to-speech",
    "text-to-audio", "audio-to-audio", "any-to-any", "image-to-3d", "text-to-3d", "automatic-speech-recognition",
}
HF_ORGS = {
    "black-forest-labs", "qwen", "openmoss-team", "microsoft", "fishaudio", "lightricks", "tencent", "tencent-hunyuan",
    "bytedance", "bytedance-seed", "bytedance-research", "deepseek-ai", "google", "meta-llama", "facebook",
    "meta-models", "mistralai", "stabilityai", "wan-ai", "alibaba-pai", "thudm", "zai-org", "minimaxai", "moonshotai",
    "nvidia", "apple", "openai", "kwai-kolors", "kwaivgi", "comfy-org", "ibm-granite", "liquidai", "baidu", "xai-org",
    "nari-labs", "sesame", "hexgrad", "resembleai", "kyutai", "lightonai", "stepfun-ai", "hunyuanvideo-community",
    "multimodalart", "huggingface", "hf-audio", "allenai", "salesforce", "runwayml", "genmo", "rhymes-ai",
}

# 對這群讀者（AI 影片創作者）來說離題的：資安、金融、維運新聞。符合的倉庫在給模型之前就丟掉。
# 內容可能是簡體也可能是繁體，所以兩種寫法都列。
OFF_LANE_ZH = (r"漏洞|隱私洩露|隱私洩露|資料洩露|資料洩露|洩露事件|洩露事件|安全審計|安全審計|安全事件|供應鏈攻擊|供應鏈攻擊|"
               r"網路攻擊|網路攻擊|駭客|駭客|虧損|虧損|IPO|上市|融資|融資|估值|裁員|裁員|訴訟|訴訟|股價|股價|財報|財報|營收|營收|"
               r"幣圈|幣圈|加密貨幣|加密貨幣|比特幣|比特幣|交易機器人|交易機器人|量化交易|自動化交易|自動化交易|炒股|股票|理財|理財|"
               r"政治|選舉|選舉|關稅|關稅")
OFF_LANE_EN = (r"security|vulnerab|pentest|exploit|malware|\bcve\b|red.?team|php-src|kernel|firmware|crypto|blockchain|"
               r"\btrading\b|wallet|defi|\bsoc\b|siem|forensic|compliance")

# 標記一條內容「在 AI 賽道裡」的詞（用來過濾綜合榜單）。簡體和繁體都列。
AI_TERMS = [
    "ai", "aigc", "人工智慧", "人工智慧", "大模型", "模型", "智慧體", "智慧體", "agent", "gpt", "chatgpt", "openai", "claude",
    "anthropic", "gemini", "deepseek", "grok", "llm", "sora", "veo", "seedance", "可靈", "可靈", "即夢", "即夢",
    "豆包", "kimi", "通義", "通義", "千問", "千問", "qwen", "codex", "cursor", "機器人", "機器人", "算力", "晶片", "晶片",
    "數字人", "數位人", "虛擬人", "生成", "midjourney", "comfyui", "runway", "vidu", "海螺", "minimax", "智譜", "智譜", "glm",
]

# 比 AI_TERMS 嚴格：只有影片真的在講 AI 或用 AI 做的時候才會出現的詞
# （AI_TERMS 裡的 模型 / 生成 / 機器人 會打到電影和遊戲標題）。
STRONG_AI_TERMS = [
    "ai", "aigc", "人工智慧", "人工智慧", "大模型", "gpt", "chatgpt", "openai", "claude", "gemini", "deepseek", "sora", "veo",
    "seedance", "可靈", "可靈", "kling", "即夢", "即夢", "豆包", "kimi", "通義", "通義", "千問", "千問", "midjourney", "comfyui",
    "runway", "vidu", "海螺", "數字人", "數位人", "智慧體", "智慧體", "codex", "cursor",
]

# 每日執行只對這些標籤的題材往回查起源（radar.deep），每個題材約 200 配額單位。空的就不查。
DEEP_DAILY_LABELS = ("新起", "升溫")

# 「最近」和「之前」的分界，用在新鮮度判斷上。
RECENT_DAYS = 7
BASELINE_DAYS = 60
YT_PROBE_DAYS = 10      # YouTube 新鮮度探測：今年相關影片裡，最近幾天內發的算「最近」


def ensure_dirs() -> None:
    for d in (DATA_DIR, SITE_DIR, LOG_DIR, SITE_DIR / "d", SITE_DIR / "data"):
        d.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(d, 0o700)
        except OSError:
            pass
