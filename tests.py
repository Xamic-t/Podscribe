#!/usr/bin/env python3
"""关键路径测试（不联网、不碰真实 Obsidian 库）。

用法：python3 tests.py
"""
from __future__ import annotations

import importlib.util
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import core as C  # noqa: E402
import download as D  # noqa: E402
import summarize as S  # noqa: E402


class SourceRouting(unittest.TestCase):
    def test_kinds(self):
        self.assertEqual(D.kind_of("https://www.xiaoyuzhoufm.com/episode/abc123"), "xiaoyuzhou")
        self.assertEqual(D.kind_of("https://www.ximalaya.com/sound/123456"), "ximalaya")
        self.assertEqual(D.kind_of("https://www.ximalaya.com/album/123"), "ximalaya-unsupported")
        self.assertEqual(D.kind_of("https://xima.tv/short"), "ximalaya-short")
        self.assertEqual(D.kind_of("https://example.com/feed.xml"), "rss")
        self.assertEqual(D.kind_of("https://cdn.example.com/a/ep.mp3"), "direct")
        self.assertEqual(D.kind_of(__file__), "local")
        self.assertEqual(D.kind_of("https://example.com/a.mp4"), "direct")  # 视音频直链
        self.assertEqual(D.kind_of("https://example.com/page.html"), "unknown")

    def test_duration_parsing(self):
        self.assertEqual(D._parse_duration("1079"), 1079)
        self.assertEqual(D._parse_duration("01:43:27"), 6207)
        self.assertEqual(D._parse_duration("43:27"), 2607)
        self.assertIsNone(D._parse_duration(None))


class PathSafety(unittest.TestCase):
    def test_path_escape_blocked(self):
        root = Path(tempfile.mkdtemp())
        for bad in ("../escape", "/absolute", ".."):
            with self.assertRaises(C.Fail):
                C.safe_target(root, bad, "note.md")

    def test_nested_ok(self):
        root = Path(tempfile.mkdtemp())
        target = C.safe_target(root, "播客", "逐字稿", "note.md")
        self.assertTrue(str(target).startswith(str(root)))

    def test_filename_sanitized(self):
        name = C.sanitize_filename('a/b:c*d?e"f<g>h|i')
        for bad in ("/", ":", "*", "?", '"', "<", ">", "|"):
            self.assertNotIn(bad, name)
        self.assertEqual(name.count(" "), 1)  # 只有 / 被换成空格


class AtomicWrite(unittest.TestCase):
    def test_never_overwrite(self):
        d = Path(tempfile.mkdtemp())
        target = d / "note.md"
        C.exclusive_write(target, "第一版")
        with self.assertRaises(C.Fail) as ctx:
            C.exclusive_write(target, "第二版")
        self.assertEqual(ctx.exception.code, "TARGET_EXISTS")
        self.assertIn("第一版", target.read_text(encoding="utf-8"))

    def test_stale_partial_does_not_block(self):
        d = Path(tempfile.mkdtemp())
        target = d / "note.md"
        (d / ".__note.md.partial").write_text("残留", encoding="utf-8")
        C.exclusive_write(target, "正常内容")
        self.assertIn("正常内容", target.read_text(encoding="utf-8"))


class QuoteVerification(unittest.TestCase):
    def test_verbatim_hit(self):
        hay = "前面一些话，然后是好的产品是很难改变的，后面还有话。"
        self.assertTrue(S.verify_quote("好的产品是很难改变的", hay))

    def test_fabricated_miss(self):
        hay = "完全不同的一段话，没有任何重合的内容在这里。"
        self.assertFalse(S.verify_quote("幸福关系的秘诀是两个快乐的人", hay))


class BlockPlan(unittest.TestCase):
    def test_blocks_cover_all_segments(self):
        jd = Path(tempfile.mkdtemp())
        segments = [{"start": float(i * 30), "end": float(i * 30 + 20),
                     "text": f"第{i}句", "speaker": "说话人 A", "chunk": 0}
                    for i in range(60)]
        C.write_json(jd / "transcript.json", {"segments": segments, "duration": 1810.0,
                                              "speakers": ["说话人 A"], "word_count": 120})
        cfg = C.load_config(Path(__file__).resolve().parent / "config.toml")
        cfg["summary"]["block_minutes"] = 5
        plan = S.make_plan(jd, cfg)
        self.assertGreaterEqual(plan["count"], 2)
        covered = sum(b["chars"] for b in plan["blocks"])
        self.assertGreater(covered, 0)
        first = Path(plan["blocks"][0]["file"]).read_text(encoding="utf-8")
        self.assertIn("第0句", first)


class SecurityFixes(unittest.TestCase):
    def setUp(self):
        self.cfg = C.load_config(Path(__file__).resolve().parent / "config.toml")

    def test_job_dir_rejects_escape(self):
        for bad in ("../../x", "xyz-abc/../../evil", "/abs", "..", ".", "a\\b"):
            with self.assertRaises(C.Fail):
                C.job_dir(self.cfg, bad)

    def test_job_dir_accepts_normal(self):
        self.assertTrue(str(C.job_dir(self.cfg, "xyz-abc123")).endswith("xyz-abc123"))

    def test_curl_cmd_has_terminator(self):
        # 以 - 开头的“URL”会被 check_url 直接拒绝（第一道防线）
        with self.assertRaises(C.Fail):
            D.curl_cmd("-K/etc/passwd", Path("/tmp/x.mp3"), None)
        # 正常 URL 前面必须有 --（第二道防线）
        cmd = D.curl_cmd("https://a.cn/x", Path("/tmp/x.mp3"), "https://a.cn")
        self.assertEqual(cmd[cmd.index("--") + 1], "https://a.cn/x")
        self.assertIn("--", cmd)

    def test_check_url_rejects_scheme(self):
        for bad in ("file:///etc/passwd", "ftp://x/y", "notaurl"):
            with self.assertRaises(C.Fail):
                C.check_url(bad)
        self.assertEqual(C.check_url("https://a.cn/b"), "https://a.cn/b")

    def test_quote_must_be_contiguous(self):
        hay = "好的产品是很难改变的，后面还有话。"
        self.assertTrue(S.verify_quote("好的产品是很难改变的", hay))
        for fake in ("好的很难", "好的变的", "产品难改的"):
            self.assertFalse(S.verify_quote(fake, hay))

    def test_fail_payload_has_exit_code(self):
        err = C.Fail("x", "CODE", "msg", C.EXIT_PAID)
        self.assertEqual(err.payload()["exit_code"], C.EXIT_PAID)


class DotenvParser(unittest.TestCase):
    def test_inline_comment_and_quotes(self):
        d = Path(tempfile.mkdtemp())
        f = d / ".env"
        f.write_text('# 注释\nKEY="a#b"  # 行内注释\nexport OTHER=plain\n', encoding="utf-8")
        data = C._parse_dotenv(f)
        self.assertEqual(data.get("KEY"), "a#b")
        self.assertEqual(data.get("OTHER"), "plain")


class ByteTruncate(unittest.TestCase):
    def test_truncate_by_bytes(self):
        name = "标题" * 100
        self.assertLessEqual(len(C.truncate_bytes(name, 40).encode("utf-8")), 40)
        self.assertEqual(C.truncate_bytes("short", 40), "short")


class ConfigGuard(unittest.TestCase):
    def test_privacy_flags_enforced(self):
        d = Path(tempfile.mkdtemp())
        bad = d / "config.toml"
        bad.write_text('[vault]\npath = "/tmp/x"\n[safety]\nscan_vault = true\n', encoding="utf-8")
        with self.assertRaises(C.Fail):
            C.load_config(bad)

    def test_defaults_applied(self):
        cfg = C.load_config(Path(__file__).resolve().parent / "config.toml")
        self.assertEqual(cfg["asr"]["engine"], "cloud")
        self.assertIn("Diarize", cfg["asr"]["cloud_model"])


def load_script(name: str):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).resolve().parent / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class Round2Fixes(unittest.TestCase):
    """第二轮评审 R1–R7 的回归用例。"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.cfg_file = Path(__file__).resolve().parent / "config.toml"
        self.cfg = C.load_config(self.cfg_file)
        self.cfg["runtime"]["cache_root"] = str(self.tmp)
        self.run = load_script("run")

    def _archive(self, *names):
        d = self.tmp / "音频库"
        d.mkdir(parents=True, exist_ok=True)
        for n in names:
            (d / n).write_bytes(b"0" * 16)
        return d

    def test_purge_requires_scope(self):          # R1
        self._archive("xm-111.m4a")
        with self.assertRaises(C.Fail) as ctx:
            self.run.audio_purge(self.cfg, None, False)
        self.assertEqual(ctx.exception.code, "NEED_SCOPE")
        self.assertTrue((self.tmp / "音频库" / "xm-111.m4a").exists())

    def test_purge_exact_match(self):             # R5
        d = self._archive("xm-111.m4a", "xm-1111.m4a")
        result = self.run.audio_purge(self.cfg, "xm-111", False)
        self.assertEqual(len(result["removed"]), 1)
        self.assertFalse((d / "xm-111.m4a").exists())
        self.assertTrue((d / "xm-1111.m4a").exists())

    def test_archive_dir_must_be_in_cache(self):  # R1
        self.cfg["retention"]["audio_archive_dir"] = str(Path(tempfile.mkdtemp()))
        with self.assertRaises(C.Fail) as ctx:
            self.run.archive_dir(self.cfg)
        self.assertEqual(ctx.exception.code, "ARCHIVE_OUT_OF_CACHE")

    def test_resolve_job_dir(self):               # R7（N2：默认要求目录已存在）
        inside = self.tmp / "xm-9"
        inside.mkdir()
        self.assertEqual(C.resolve_job_dir(self.cfg, "xm-9").name, "xm-9")
        self.assertEqual(C.resolve_job_dir(self.cfg, str(inside)).name, "xm-9")
        with self.assertRaises(C.Fail):
            C.resolve_job_dir(self.cfg, "/tmp/别的地方/xm-9")

    def test_rss_rejects_late_doctype(self):      # R4
        dl = load_script("download")
        payload = ("<!--" + "x" * 2500 + "-->") + \
                  '<!DOCTYPE rss [<!ENTITY e "EXPANDED">]><rss><channel><title>&e;</title></channel></rss>'
        orig = C.http_bytes
        C.http_bytes = lambda *a, **k: payload.encode("utf-8")
        try:
            with self.assertRaises(C.Fail) as ctx:
                dl._rss_items("https://example.com/feed.xml")
            self.assertEqual(ctx.exception.code, "UNSAFE_XML")
        finally:
            C.http_bytes = orig

    def test_schema_version_non_numeric(self):    # R9
        bad = self.tmp / "bad.toml"
        bad.write_text('schema_version = "abc"\n[vault]\npath = "/tmp/x"\n', encoding="utf-8")
        with self.assertRaises(C.Fail) as ctx:
            C.load_config(bad)
        self.assertEqual(ctx.exception.code, "CONFIG_VERSION_INVALID")
        self.assertEqual(ctx.exception.exit_code, C.EXIT_BAD_CONFIG)


class AsrConfigGate(unittest.TestCase):
    """ASR 模型与接口地址必须是使用者自己填的，占位符一律拦下。"""

    def setUp(self):
        self.cfg = C.load_config(Path(__file__).resolve().parent / "config.toml")

    def test_placeholder_rejected(self):
        self.cfg["asr"]["cloud_model"] = "FILL_ME_ASR_MODEL"
        self.cfg["asr"]["cloud_base_url"] = "FILL_ME_BASE_URL"
        with self.assertRaises(C.Fail) as ctx:
            C.require_asr_config(self.cfg)
        self.assertEqual(ctx.exception.code, "ASR_NOT_CONFIGURED")
        self.assertEqual(ctx.exception.exit_code, C.EXIT_BAD_CONFIG)

    def test_empty_rejected(self):
        self.cfg["asr"]["cloud_model"] = ""
        self.cfg["asr"]["cloud_base_url"] = "https://api.siliconflow.cn/v1"
        with self.assertRaises(C.Fail):
            C.require_asr_config(self.cfg)

    def test_filled_passes(self):
        self.cfg["asr"]["cloud_model"] = "XingChenAGI/XingChenASR-Diarize-V3.0"
        self.cfg["asr"]["cloud_base_url"] = "https://api.siliconflow.cn/v1"
        self.assertIsNone(C.require_asr_config(self.cfg))


class Round3Fixes(unittest.TestCase):
    """第三轮复核 N1–N7 的回归用例。"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.cfg = C.load_config(Path(__file__).resolve().parent / "config.toml")
        self.cfg["runtime"]["cache_root"] = str(self.tmp)
        self.run = load_script("run")

    def test_purge_rejects_out_of_cache_job_dir(self):      # N1
        outside = Path(tempfile.mkdtemp()) / "xm-9"
        outside.mkdir()
        with self.assertRaises(C.Fail) as ctx:
            C.resolve_job_dir(self.cfg, str(outside), must_exist=False)
        self.assertEqual(ctx.exception.code, "JOB_NOT_IN_CACHE")

    def test_missing_job_dir_is_reported(self):              # N2
        with self.assertRaises(C.Fail) as ctx:
            C.resolve_job_dir(self.cfg, "xm-404")
        self.assertEqual(ctx.exception.code, "JOB_NOT_FOUND")
        self.assertEqual(ctx.exception.exit_code, C.EXIT_BAD_INPUT)

    def test_reserved_name_rejected(self):                   # N3
        for bad in ("音频库", "./音频库", str(self.tmp / "音频库")):
            with self.assertRaises(C.Fail) as ctx:
                C.resolve_job_dir(self.cfg, bad, must_exist=False)
            self.assertEqual(ctx.exception.code, "RESERVED_JOB_ID")

    def test_dot_slash_equals_bare_id(self):                 # N3
        (self.tmp / "xm-9").mkdir()
        bare = C.resolve_job_dir(self.cfg, "xm-9")
        self.assertEqual(C.resolve_job_dir(self.cfg, "./xm-9"), bare)
        self.assertEqual(C.resolve_job_dir(self.cfg, str(self.tmp / "xm-9")), bare)

    def test_purge_clears_pieces(self):                      # N6
        mine = self.tmp / "xm-111" / "pieces" / "20260101-000000"
        mine.mkdir(parents=True)
        (mine / "p000.mp3").write_bytes(b"0" * 2048)
        other = self.tmp / "xm-222" / "pieces" / "20260101-000000"
        other.mkdir(parents=True)
        (other / "p000.mp3").write_bytes(b"0" * 2048)

        result = self.run.audio_purge(self.cfg, "xm-111", False)
        self.assertFalse((self.tmp / "xm-111" / "pieces").exists())
        self.assertTrue((self.tmp / "xm-222" / "pieces").exists())  # 不误伤别的一期
        self.assertEqual(len(result["pieces_removed"]), 1)

        result = self.run.audio_purge(self.cfg, None, True)
        self.assertFalse((self.tmp / "xm-222" / "pieces").exists())
        self.assertEqual(len(result["pieces_removed"]), 1)

    def test_purge_no_match_warns(self):                     # N6
        result = self.run.audio_purge(self.cfg, "xm-404", False)
        self.assertEqual(result["removed"], [])
        self.assertIn("warnings", result)

    def test_vtt_link_and_chapter_var(self):                 # N4 / N5
        jd = self.tmp / "xm-1"
        jd.mkdir(parents=True)
        C.write_json(jd / "metadata.json", {"title": "标题", "album": "专辑",
                                            "source_type": "ximalaya",
                                            "source_url": "https://x.cn/1",
                                            "published": "2026-01-01",
                                            "duration_seconds": 60})
        C.write_json(jd / "transcript.json", {
            "segments": [{"start": 0.0, "end": 1.0, "text": "你好",
                          "speaker": "说话人 A"}], "model": "m"})
        C.write_json(jd / "summary.json", {
            "tldr": "摘要", "title": "标题", "key_points": [], "quotes": [],
            "chapters": [{"start": 0.0, "title": "开场", "summary": "寒暄"}]})
        pub = load_script("publish")
        _stem, main_md, transcript_md = pub.render(jd, self.cfg, "")
        self.assertIn("|WebVTT 字幕]]", main_md)          # N4：wikilink
        self.assertNotIn("[WebVTT 字幕](", main_md)        # 不再是 Markdown 相对链接
        self.assertIn("## 00:00 开场", transcript_md)      # N5：章节变量没被段落循环污染


class StdinPayload(unittest.TestCase):
    """R3：add --json-file - 只读一次 stdin；R2：run.py 失败必须非零退出。"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        base = (Path(__file__).resolve().parent / "config.toml").read_text(encoding="utf-8")
        base = base.replace('cache_root = "~/.cache/podcast-to-obsidian"',
                            f'cache_root = "{self.tmp}"')
        # 不依赖本机填了什么：这一条用例就是要 vault 仍是占位符时 publish 失败
        base = re.sub(r'^path = ".*"$', 'path = "PLACEHOLDER_VAULT_PATH"',
                      base, count=1, flags=re.M)
        self.conf = self.tmp / "config.toml"
        self.conf.write_text(base, encoding="utf-8")
        self.jd = C.job_dir(C.load_config(self.conf), "xm-1")
        (self.jd / "blocks").mkdir(parents=True)

    def _call(self, *args, stdin: str | None = None):
        return subprocess.run(
            [sys.executable, str(Path(__file__).resolve().parent / args[0]), *args[1:]],
            input=stdin, text=True, capture_output=True)

    def test_add_from_stdin(self):                        # R3
        proc = self._call("summarize.py", "add", "--job-dir", "xm-1",
                          "--config", str(self.conf), "--block", "1",
                          "--json-file", "-", stdin='{"block_summary":"x"}')
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertTrue((self.jd / "blocks" / "abstract-001.json").exists())

    def test_run_nonzero_when_publish_fails(self):        # R2
        C.write_json(self.jd / "metadata.json", {"title": "t", "audio": {}})
        C.write_json(self.jd / "transcript.json",
                     {"segments": [{"start": 0, "end": 1, "text": "hi",
                                    "speaker": "说话人 A", "chunk": 0}], "model": "x"})
        C.write_json(self.jd / "summary.json", {"tldr": "x", "key_points": []})
        C.write_json(self.jd / "blocks" / "index.json", {"blocks": []})  # 跳过 plan
        proc = self._call("run.py", "--job-dir", "xm-1", "--config", str(self.conf))
        self.assertNotEqual(proc.returncode, 0, proc.stdout)
        self.assertIn("VAULT_PLACEHOLDER", proc.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
