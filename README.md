# AI 影片情報雷達（yt-radar）

每天自動掃一遍 YouTube（長片和 Shorts）、GitHub、Hugging Face、Hacker News 和台灣科技媒體，找出 AI 影片圈剛冒頭的題材和值得一用的新工具，生成一頁能直接播放影片的日報。

**完全免費**：YouTube 走官方的 YouTube Data API（每天 10,000 配額單位免費），模型預設用 Gemini 免費層，其他來源本來就免費。一期執行約用掉 3,000 到 5,000 配額單位，一天跑一次綽綽有餘。

介面是繁體中文。這是 [garycaogan/intel-radar](https://github.com/garycaogan/intel-radar)（MIT）的改版：拿掉了 B 站、抖音、TikTok、小紅書、X 等需要付費第三方介面的來源，YouTube 改走官方 API，介面和提示詞改成繁體中文。

## 它和熱榜有什麼不一樣

熱榜誰都能看，難的是在一個題材剛冒頭、還沒被做爛的時候發現它。雷達做的是這件事：

- **「新起」要兩條獨立訊號**。一個題材想標「新起」，得同時滿足：這個形式以前幾乎沒有（YouTube 今年標題對得上的影片裡，六成以上是最近 10 天發的），**並且**有 3 個以上頻道在做或者有小頻道爆了。只有一條訊號的標「觀察」，以前就有、最近明顯變多的標「升溫」，一直有人做的標「持續」。每張卡片下面都寫著觸發了哪幾條訊號。
- **數字全部是程式算的**。模型只負責把影片歸成題材、起名字、寫點評；觀看、訂閱、幾個頻道在做、小頻道單支多少觀看，都由程式從 YouTube 官方資料裡算，模型改不了。
- **往回查起源**。當天標「新起」「升溫」的題材會再往 YouTube 回翻四年：每年有幾支同類影片、最早能查到哪一支，最後寫三句話：最早從哪來、怎麼傳開的、現在到哪一步。
- **影片就地播放**。頭條和題材卡片先放作品本身，點封面就在頁面裡用 YouTube 官方播放器播，一次只播一個。
- **每條都能做成分享圖**。頭條、影片題材和 GitHub 新倉庫右上角有「分享」，生成一張 1080×1440 的直式圖，帶標題、一句判斷、三個數字、代表作封面和 QR code。

## 降噪規則

- 影片題材：AI 相關度不夠的不要；名字去掉「AI、教學、副業、賺錢」之後說不出具體形式的不要；官方 MV、真人影視、遊戲實況這類看不出是 AI 做的不要。
- 開源與工具：資安、金融、維運類倉庫在給模型之前就去掉；Hugging Face 的 Space 只收已知機構發的；成人、換臉、賭博類內容在採集時就擋掉（簡體繁體都認）。
- 只有一個頻道在說、又查不到來源的收入說法，不進頭條。

## 跑起來

需要 Python 3.9 或更新版本，只用標準函式庫，不用安裝任何套件。

```bash
git clone <你的倉庫網址> yt-radar
cd yt-radar
cp .env.example .env        # 然後編輯 .env，填 YOUTUBE_API_KEY 和 LLM_API_KEY
```

### 申請兩把免費金鑰

1. **YouTube Data API 金鑰**：到 [Google Cloud Console](https://console.cloud.google.com) 建專案 → 「API 和服務」→「程式庫」→ 啟用 **YouTube Data API v3** → 「憑證」→「建立憑證」→「API 金鑰」。不用綁信用卡。
2. **Gemini 金鑰**：到 [Google AI Studio](https://aistudio.google.com) 按「Get API key」。免費層一天幾百次請求，雷達一期只叫 3 到 5 次。

兩把都貼進 `.env`。

### 先不花配額試一下

```bash
python3 -m unittest discover -s tests          # 離線測試，確認 Python 環境正常
python3 -m radar run --only github_trending,hn,hf,news --skip-llm
python3 -m radar serve                          # 開 http://127.0.0.1:8792
```

這樣出來的頁面基本是空架子，因為題材、頭條和工具點評都靠模型歸類和寫。配好金鑰後跑完整一期：

```bash
python3 -m radar run        # 採集 + 分析 + 生成頁面，約 3 到 6 分鐘
python3 -m radar serve
```

其他指令：

```bash
python3 -m radar render              # 用已存的資料重新生成頁面（改了模板或站名時用）
python3 -m radar run --reuse 6       # 不重新採集，用最近 6 小時存下的內容重新分析（改提示詞時用）
python3 -m radar run --only youtube  # 只重新採集某幾個來源（名字見 radar/pipeline.py 的 COLLECTORS）
python3 -m radar deep                # 對最新一期的所有影片題材往回查起源（預設每天只查新起和升溫的）
python3 -m radar status              # 最近幾次執行
```

## 每期用多少配額

| 動作 | 配額單位 |
|---|---|
| 一個關鍵字搜一次（search.list） | 100 |
| 補一批影片的觀看數（videos.list，50 支一次） | 1 |
| 查一個頻道的訂閱數（channels.list） | 1 |
| 一個題材的新鮮度探測 | 100 |
| 一個題材往回查起源（深挖） | 約 600 |

預設關鍵字 20 個加 Shorts 6 個，約 2,700 單位；探測最多 8 個題材 800 單位；深挖 2 到 5 個題材 1,200 到 3,000 單位。合計一期 4,000 到 6,500 單位，在 10,000 的每日免費額度內。單次執行有硬上限 `YT_MAX_UNITS`（預設 9,000），到了就停止呼叫。配額在太平洋時間午夜重置，大約是台灣時間下午 3 到 4 點。

模型方面 Gemini 免費層一期 4 到 6 次呼叫，約 6 到 8 萬 token，不用錢。

## 每天自動跑

用 cron 每天早上跑一次。日期按台北時間算，`--if-missing` 保證當天已經出過就跳過，所以可以設兩個時間點，第一次失敗了第二次補：

```cron
20 6,7 * * * cd /path/to/yt-radar && /usr/bin/python3 -m radar run --if-missing >> ~/.local/share/yt-radar/logs/run.log 2>&1
```

cron 的時間是伺服器本地時間，伺服器不在 UTC+8 的話自己換算一下。

## 公開給別人看

生成的頁面在資料目錄的 `site/` 裡（預設 `~/.local/share/yt-radar/site/`），是純靜態檔案，頁面裡的連結都是相對路徑。把這個目錄同步到任意靜態託管（Cloudflare Pages、GitHub Pages、Nginx）就能公開，不需要後端。

- 公開前把 `.env` 裡的 `RADAR_SHARE_URL` 改成你的公開網址，再 `python3 -m radar render` 一次，分享卡片上的 QR code 才會指向你的站。
- 頁面和 `data/<日期>.json` 裡都沒有配額用量、失敗來源和執行時間，但資料庫和日誌裡有，所以只同步 `site/`，不要把整個資料目錄放出去。
- 頁面預設帶 `noindex`，想被搜尋引擎收錄，改 `radar/render.py` 裡的 robots 標籤。
- `python3 -m radar serve` 只適合本機預覽，公開請放在 Nginx、Caddy 或靜態託管後面。

## 改成你自己的

- **站名和口吻**：`RADAR_SITE_NAME` 是站名；`RADAR_SITE_BY` 是出品方；`RADAR_EDITOR` 是點評用誰的口吻寫。後兩個可以留空。
- **賽道**：所有關鍵字、搜尋式和預算都在 [radar/config.py](radar/config.py)。想盯別的領域（例如 AI 程式開發、跨境電商），改 `YT_KEYWORDS`、`YT_SHORT_KEYWORDS` 和 `AI_TERMS`，再改 `radar/analyze.py` 裡提示詞對讀者的描述。先改採集和過濾，再改提示詞，數字永遠留給程式算。
- **只看台灣**：`.env` 設 `YT_REGION=TW`、`YT_LANG=zh-Hant`。
- **新聞來源**：`config.py` 的 `NEWS_FEEDS`，任何 RSS / Atom 都能加。

## 目錄

- `radar/sources/`：各來源採集器（youtube、github、feeds（資訊 RSS）、misc（Hugging Face、Hacker News））
- `radar/score.py`：每小時觀看、觀看÷訂閱、漲星速度、來源內百分位熱度
- `radar/analyze.py`：題材歸類、新鮮度探測、標籤規則、頭條點評的提示詞
- `radar/deep.py`：題材往回查起源
- `radar/pipeline.py`：一次完整執行
- `radar/render.py`：靜態頁面，分享彈層和畫卡片的腳本也在這裡
- `radar/share.py`：分享卡片的內容、數字、QR code 和封面快取
- `radar/net.py`：HTTP、YouTube API 客戶端和配額帳簿
- `radar/config.py`：設定、關鍵字、搜尋式、預算
- `tests/`：離線測試，`python3 -m unittest discover -s tests`

改程式前後怎麼自查見 [CONTRIBUTING.md](CONTRIBUTING.md)，金鑰和資料怎麼存見 [SECURITY.md](SECURITY.md)。

## 授權

MIT，見 [LICENSE](LICENSE)。原始專案 intel-radar 由 Gary（曹淦）開發，MIT 授權；QR code 生成用的是 Project Nayuki 的 MIT 函式庫，原樣收錄在 `radar/qrcodegen.py`，見 [THIRD_PARTY.md](THIRD_PARTY.md)。
