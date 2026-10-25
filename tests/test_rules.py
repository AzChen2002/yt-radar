"""離線檢查：解析、算分和標籤規則（不連網、不叫模型）。"""

import time
import unittest

from radar import analyze, score
from radar.sources import common, youtube


class Parsing(unittest.TestCase):
    def test_counts(self):
        self.assertEqual(common.count("1,234 views"), 1234)
        self.assertEqual(common.count("1.3K"), 1300)
        self.assertEqual(common.count("12萬次觀看"), 120000)
        self.assertEqual(common.count("12万次观看"), 120000)
        self.assertEqual(common.count("204K subscribers"), 204000)
        self.assertIsNone(common.count(""))

    def test_relative_time(self):
        now = 1_800_000_000
        self.assertEqual(common.relative_ts("3 days ago", now), now - 3 * 86400)
        self.assertEqual(common.relative_ts("2天前", now), now - 2 * 86400)
        self.assertIsNone(common.relative_ts("", now))

    def test_iso_time(self):
        self.assertEqual(common.iso_ts("2026-10-20T00:17:47Z"), 1_792_455_467.0)

    def test_duration(self):
        self.assertEqual(youtube.parse_duration("PT1H2M3S"), 3723)
        self.assertEqual(youtube.parse_duration("PT45S"), 45)
        self.assertEqual(youtube.parse_duration("PT3M"), 180)
        self.assertIsNone(youtube.parse_duration("P0D"))
        self.assertIsNone(youtube.parse_duration(""))

    def test_blocked(self):
        self.assertTrue(common.blocked("Qwen-Image-Uncensored"))
        self.assertTrue(common.blocked("best-face-swap"))
        self.assertTrue(common.blocked("成人短剧合集"))
        self.assertFalse(common.blocked("Lightricks/LTX-2.5"))

    def test_lane(self):
        self.assertTrue(common.in_lane("用 AI 做動畫"))
        self.assertTrue(common.in_lane("用 AI 做动画"))
        self.assertTrue(common.in_lane("Seedance 2.5 實測"))
        self.assertTrue(common.in_lane("人工智慧教學"))
        self.assertFalse(common.in_lane("he said it was fine"))   # 字裡面的 ai 不算
        self.assertFalse(common.in_lane("譚喬回應二仙橋大爺"))


class Scoring(unittest.TestCase):
    def test_video_signals(self):
        now = time.time()
        it = {"kind": "video", "source": "youtube", "published_at": now - 10 * 3600,
              "metrics": {"views": 50000, "followers": 2000, "likes": 1000, "favorites": 500}}
        s = score.derive(it, now)
        self.assertEqual(s["vph"], 5000.0)
        self.assertEqual(s["vsr"], 25.0)

    def test_new_account_floor(self):
        now = time.time()
        it = {"kind": "video", "source": "youtube", "published_at": now - 2 * 3600, "metrics": {"views": 9000, "followers": 3}}
        s = score.derive(it, now)
        self.assertEqual(s["vsr"], 9.0)        # 訂閱數下限 1000
        self.assertEqual(s["vph"], 1500.0)     # 影片年齡下限 6 小時

    def test_fmt_num(self):
        self.assertEqual(score.fmt_num(123456), "12.3萬")
        self.assertEqual(score.fmt_num(250_000_000), "2.5億")
        self.assertEqual(score.fmt_num(None), "?")


def _topic(authors=1, max_vsr=None, yt=None):
    return {"metrics": {"authors": authors, "max_vsr": max_vsr}, "probe_youtube": yt}


def _yt(match, recent, oldest=60, term="x"):
    return {"term": term, "year_n": 50, "match": match, "match_recent": recent, "oldest_match_days": oldest}


class Labels(unittest.TestCase):
    def test_new_needs_two_signals(self):
        label, basis = analyze._label_video(_topic(authors=4, yt=_yt(6, 5, 9)), None, 0)
        self.assertEqual(label, "新起")
        self.assertTrue(any("以前幾乎沒有" in b for b in basis))
        self.assertTrue(any("4 個不同頻道" in b for b in basis))

    def test_new_term_alone_is_watch(self):
        label, _ = analyze._label_video(_topic(authors=1, yt=_yt(6, 5, 9)), None, 0)
        self.assertEqual(label, "觀察")

    def test_partly_old_format_is_rising(self):
        label, _ = analyze._label_video(_topic(authors=6, max_vsr=50, yt=_yt(7, 4)), None, 0)
        self.assertEqual(label, "升溫")

    def test_outlier_supports_rising(self):
        label, basis = analyze._label_video(_topic(authors=1, max_vsr=12, yt=_yt(10, 4)), None, 0)
        self.assertEqual(label, "升溫")
        self.assertTrue(any("小頻道爆了" in b for b in basis))

    def test_evergreen(self):
        label, _ = analyze._label_video(_topic(authors=5, yt=_yt(20, 2, 400)), None, 0)
        self.assertEqual(label, "持續")

    def test_too_few_matches_is_watch(self):
        label, basis = analyze._label_video(_topic(authors=4, yt=_yt(2, 2)), None, 0)
        self.assertEqual(label, "觀察")
        self.assertTrue(any("不到 3 支" in b for b in basis))

    def test_unseen_burst_is_new(self):
        label, _ = analyze._label_video(_topic(authors=3, yt=_yt(10, 4)), None, 5)
        self.assertEqual(label, "新起")

    def test_memory_keeps(self):
        hit = {"thread_id": 1, "first_day": "2026-10-10"}
        label, basis = analyze._label_video(_topic(authors=2, yt=_yt(10, 4)), hit, 5)
        self.assertEqual(label, "持續")
        self.assertTrue(any("2026-10-10 起就在記錄" in b for b in basis))

    def test_youtube_title_match(self):
        self.assertTrue(youtube.title_matches("Claude Code TV", "Claude made my old TV like new"))
        self.assertFalse(youtube.title_matches("Claude Code TV", "Claude Code full course"))
        self.assertTrue(youtube.title_matches("AI food slide", "Ultimate Oddly Satisfying AI Food Slide"))
        self.assertTrue(youtube.title_matches("蔬菜家族", "AI 蔬菜家族 3D 動畫第 3 集"))


class Relevance(unittest.TestCase):
    def test_generic_topic_names(self):
        self.assertTrue(analyze.generic_name("AI副業賺錢教學"))
        self.assertTrue(analyze.generic_name("AI副业赚钱教程"))
        self.assertTrue(analyze.generic_name("AI 工具評測"))
        self.assertFalse(analyze.generic_name("Seedance 2.5寫實教學"))
        self.assertFalse(analyze.generic_name("AI漫劇合集連載"))

    def test_off_lane(self):
        self.assertTrue(common.off_lane("OpenAI 發現 PHP 漏洞"))
        self.assertTrue(common.off_lane("Anthropic 80B 虧損與 IPO"))
        self.assertTrue(common.off_lane("Anthropic 80B 亏损与 IPO"))
        self.assertTrue(common.off_lane("cloudflare/security-audit-skill"))
        self.assertTrue(common.off_lane("AI炒股Agent開源"))
        self.assertFalse(common.off_lane("Kling 4.0 上線"))

    def test_strong_ai(self):
        self.assertTrue(common.strong_ai("趙本山演唱金曲【AI MV大賽】"))
        self.assertFalse(common.strong_ai("影石超旗艦全景相機評測：晶片和機器人"))

    def test_model_family(self):
        self.assertEqual(analyze.model_family("OpenMOSS-Team/MOSS-Image-SFT"), analyze.model_family("Comfy-Org/MOSS-Image"))
        self.assertEqual(analyze.model_family("black-forest-labs/flux-3-image-klein-comfy"), "flux-3-image-klein")
        self.assertEqual(analyze.model_family("Lightricks/LTX-2.5"), "ltx-2.5")

    def test_hf_allowlist(self):
        space = lambda owner: {"kind": "space", "author": owner, "extra": {}}
        model = lambda owner, pipe: {"kind": "model", "author": owner, "extra": {"pipeline": pipe}}
        self.assertTrue(analyze._hf_keep(space("black-forest-labs")))
        self.assertFalse(analyze._hf_keep(space("hitman911")))
        self.assertTrue(analyze._hf_keep(model("someone", "image-to-video")))
        self.assertFalse(analyze._hf_keep(model("someone", "text-generation")))
        self.assertTrue(analyze._hf_keep(model("Qwen", "image-text-to-text")))


class YouTubeCards(unittest.TestCase):
    def _item(self, **kw):
        base = {"id": "abc123xyz00",
                "snippet": {"title": "AI 蔬菜家族 第 1 集", "description": "made with Veo", "channelId": "UC1",
                            "channelTitle": "Veg TV", "publishedAt": "2026-10-20T00:00:00Z",
                            "thumbnails": {"high": {"url": "https://i.ytimg.com/vi/abc123xyz00/hqdefault.jpg"}},
                            "liveBroadcastContent": "none"},
                "statistics": {"viewCount": "120000", "likeCount": "3000"},
                "contentDetails": {"duration": "PT2M30S"}}
        base.update(kw)
        return base

    def test_card(self):
        c = youtube._card(self._item(), "youtube:AI video")
        self.assertEqual(c["id"], "youtube:abc123xyz00")
        self.assertEqual(c["metrics"]["views"], 120000)
        self.assertEqual(c["extra"]["duration"], 150)
        self.assertTrue(c["extra"]["short"])                       # 3 分鐘以內算 Shorts
        self.assertTrue(c["url"].endswith("/shorts/abc123xyz00"))
        self.assertEqual(c["author_id"], "UC1")

    def test_long_video_and_live(self):
        c = youtube._card(self._item(contentDetails={"duration": "PT12M"}), "youtube:x")
        self.assertFalse(c["extra"]["short"])
        self.assertIn("watch?v=", c["url"])
        live = self._item(); live["snippet"]["liveBroadcastContent"] = "live"
        self.assertIsNone(youtube._card(live, "youtube:x"))


class Media(unittest.TestCase):
    def test_embeds(self):
        from radar import render
        m = render.media_of({"url": "https://www.youtube.com/shorts/fP2SGGVsaB0"})
        self.assertTrue(m["src"].startswith("https://www.youtube-nocookie.com/embed/fP2SGGVsaB0"))
        m = render.media_of({"url": "https://www.youtube.com/watch?v=fP2SGGVsaB0"})
        self.assertEqual(m["kind"], "iframe")

    def test_headline_why_fallback(self):
        from radar import render
        h = {"comment": "有手法，可以學一下。這個題材突然冒出來，四個頻道在做。最大的坑是太技術化。"}
        self.assertEqual(render._why(h), "這個題材突然冒出來，四個頻道在做。")
        self.assertEqual(render._why({"why": "一句話", "comment": "x"}), "一句話")

    def test_bars_render(self):
        from radar import render
        qs = [{"start": 1_672_531_200 + i * 365 * 86400, "end": 0, "match": m} for i, m in enumerate([0, 1, 3, 5])]
        html = render._bars(qs, "vegetable family")
        self.assertEqual(html.count('class="bar"') + html.count('class="bar zero"'), 4)
        self.assertIn("repeat(4,", html)
        empty = render._bars([{**q, "match": 0} for q in qs], "fruit baby")
        self.assertIn("chart-empty", empty)
        self.assertNotIn('class="bars"', empty)


class Ledger(unittest.TestCase):
    def test_units_since_snapshot(self):
        from radar import net
        led = net.Ledger()
        led.hit("youtube"); led.charge("search"); led.add_tokens("some-model", 1000, 100)
        mark = led.snapshot()
        led.hit("youtube"); led.charge("videos"); led.charge("search"); led.add_tokens("some-model", 500, 50)
        since = led.summary(since=mark)
        self.assertEqual(since["calls"], {"youtube": 1})
        self.assertEqual(since["youtube_units"], 101)
        self.assertEqual(since["tokens"], {"some-model": [500, 50]})
        self.assertEqual(led.summary()["youtube_units"], 201)
        self.assertEqual(led.units_total(), 201)


class DailyDeep(unittest.TestCase):
    def _run(self, deep_result):
        from unittest import mock
        from radar import __main__ as cli
        calls = []
        brief = {"day": "2026-10-23", "stats": {}}
        def fake_deep(day, labels):
            calls.append(("deep", labels))
            if isinstance(deep_result, Exception):
                raise deep_result
            return deep_result
        with mock.patch.object(cli.pipeline, "run", lambda *a: calls.append(("run",)) or brief), \
                mock.patch.object(cli.deep, "run", fake_deep), \
                mock.patch.object(cli.db, "connect", lambda: None), mock.patch.object(cli.db, "save_brief", lambda *a: None), \
                mock.patch.object(cli.render, "write_site", lambda *a: calls.append(("render",))):
            self.assertEqual(cli.main(["run"]), 0)
        return calls, brief

    def test_deep_runs_for_rising_only(self):
        calls, brief = self._run({"topics": [{}, {}], "cost": {"youtube_units": 400}})
        self.assertEqual([c[0] for c in calls], ["run", "deep", "render"])
        self.assertEqual(calls[1][1], ("新起", "升溫"))
        self.assertEqual(brief["stats"]["deep"]["topics"], 2)

    def test_deep_failure_keeps_the_brief(self):
        calls, _ = self._run(RuntimeError("quota exceeded"))
        self.assertEqual([c[0] for c in calls], ["run", "deep"])

    def test_no_rising_topics(self):
        calls, brief = self._run(None)
        self.assertEqual([c[0] for c in calls], ["run", "deep"])
        self.assertNotIn("deep", brief["stats"])


class Settings(unittest.TestCase):
    def test_env_file(self):
        import os
        import tempfile
        from pathlib import Path
        from unittest import mock
        from radar import config
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / ".env"
            f.write_text("# comment\n\nRADAR_T1=plain\nexport RADAR_T2 = 'quoted value'\nRADAR_T3=\"x=y\"\nRADAR_T4=from-file\nnot a pair\n",
                         encoding="utf-8")
            with mock.patch.dict(os.environ, {"RADAR_T4": "from-env"}):
                config.load_env_file(f)
                self.assertEqual(os.environ["RADAR_T1"], "plain")
                self.assertEqual(os.environ["RADAR_T2"], "quoted value")
                self.assertEqual(os.environ["RADAR_T3"], "x=y")
                self.assertEqual(os.environ["RADAR_T4"], "from-env")       # 真正的環境變數優先
            for k in ("RADAR_T1", "RADAR_T2", "RADAR_T3"):
                os.environ.pop(k, None)
        config.load_env_file(Path("/nonexistent/.env"))                     # 檔案不存在也沒事

    def test_headline_voice(self):
        named = analyze.head_voice("某社群", "小王")
        self.assertEqual(named["role"], "你是某社群的情報主編，替小王寫")
        self.assertIn("某社群社群裡的", named["readers"])
        self.assertEqual(named["voice"], "小王的說話方式")
        plain = analyze.head_voice("", "")
        self.assertEqual(plain["readers"], "")
        prompt = analyze.HEAD_PROMPT.format(videos="", tools="", **plain)
        self.assertTrue(prompt.startswith("你是一份每日 AI 影片情報的主編，寫今天的「AI 影片情報日報」頭條。讀者是AI 影片創作者"))
        self.assertNotIn("{", prompt.split("只輸出")[0])

    def test_prompts_format(self):
        analyze.VIDEO_PROMPT.format(past="", lines="")
        analyze.TOOLS_PROMPT.format(repos="", models="", stories="", news="")
        from radar import deep
        deep.TERMS_PROMPT.format(lines="")
        deep.NOTE_PROMPT.format(blocks="")

    def test_reader_json_has_no_run_info(self):
        from radar import render
        brief = {"day": "2026-10-23", "stats": {"items": 2312, "by_source": {"youtube": 1}, "cost": {"youtube_units": 3000},
                                                "errors": ["x"], "secs": 300, "collect": {}}}
        self.assertEqual(render.public_brief(brief)["stats"], {"items": 2312, "by_source": {"youtube": 1}})
        self.assertIn("cost", brief["stats"])                                # 存在資料庫裡的那份還留著


if __name__ == "__main__":
    unittest.main()
