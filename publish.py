#!/usr/bin/env python3
"""落盘层：渲染 Markdown 笔记并写进 Obsidian。

用法：
  publish.py --job-dir <dir> --config <c> [--upsert] [--revision-suffix r2] [--render-only]

策略：
  create_only（默认）：目标存在就停，绝不覆盖、绝不读已有笔记。
  upsert             ：仅在显式要求时替换同名文件（有风险，慎重）。
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import core as C  # noqa: E402

STAGE = "publish"
TOKEN = re.compile(r"\{\{\s*([a-z_]+)\s*\}\}")


def fill(tpl: str, mapping: dict) -> str:
    missing: list[str] = []

    def repl(m):
        key = m.group(1)
        if key in mapping:
            return mapping[key]
        missing.append(key)
        return ""

    out = TOKEN.sub(repl, tpl)
    if missing:
        raise C.Fail(STAGE, "TEMPLATE_MISSING_TOKEN",
                     f"模板占位符缺失：{sorted(set(missing))}", C.EXIT_VALIDATION)
    return re.sub(r"\n{3,}", "\n\n", out).rstrip() + "\n"


def detect_episode(title: str) -> str | None:
    for pattern in (r"(?:EP|ep|Ep)\s*(\d{1,4})", r"#\s*(\d{1,4})",
                    r"(?:Vol|VOL)\.?\s*(\d{1,4})", r"第\s*(\d{1,4})\s*[期集]"):
        m = re.search(pattern, title or "")
        if m:
            return f"EP{int(m.group(1)):03d}"
    return None


def build_stem(meta: dict, summary: dict, cfg: dict, job_id: str) -> str:
    fn = cfg["filename"]
    title = meta.get("title") or "未命名单集"
    if meta.get("source_type") == "local_audio" and summary.get("title"):
        title = summary["title"]
    parts = []
    if fn.get("date_prefix", True):
        parts.append(meta.get("published") or "undated")
    ep = detect_episode(meta.get("title") or "")
    if ep:
        parts.append(ep)
    parts.append(C.sanitize_filename(title, int(fn.get("max_title_chars", 80))))
    return " ".join(parts) + f" [{job_id}]"


def md_table_value(v) -> str:
    text = "" if v is None else str(v)
    return text.replace("|", "丨").replace("\n", " ").strip() or "—"


def render(jd: Path, cfg: dict, suffix: str = "") -> tuple[str, str, str]:
    meta = C.read_json(jd / "metadata.json")
    transcript = C.read_json(jd / "transcript.json")
    summary = C.read_json(jd / "summary.json")
    segments = transcript.get("segments", [])
    job_id = jd.name
    # P13：按字节截断整个 stem（vault 可能在 ext4 / SMB 上，限制是 255 字节）
    stem = C.truncate_bytes(build_stem(meta, summary, cfg, job_id), 200)
    duration = meta.get("duration_seconds") or transcript.get("duration")

    speakers = transcript.get("speakers") or sorted({s["speaker"] for s in segments})
    roles = {r.get("label"): r.get("role") for r in (summary.get("speaker_roles") or [])}
    speaker_desc = "、".join(
        f"{s}" + (f"（{roles[s]}）" if roles.get(s) and roles[s] != "未知" else "")
        for s in speakers) or "未分离"

    info_rows = [("节目", meta.get("album")), ("主播 / 创作者", meta.get("creator")),
                 ("发布时间", meta.get("published")), ("时长", C.fmt_duration(duration)),
                 ("说话人", speaker_desc),
                 ("原始链接", f"[打开原始单集]({meta['source_url']})" if meta.get("source_url")
                  else "本地音频文件")]
    info_table = "\n".join(f"> | {k} | {md_table_value(v)} |" for k, v in info_rows)

    kp = "\n\n".join(
        f"### {i}. {p['heading']}\n\n{p['detail']}"
        + (f"\n\n- 证据位置：{'、'.join('`' + C.fmt_duration(t) + '`' for t in p['evidence_times'][:4])}"
           if p.get("evidence_times") else "")
        for i, p in enumerate(summary.get("key_points") or [], 1)) or "_（没有结构化要点）_"

    quotes = "\n\n".join(f"> [!quote] {C.fmt_duration(q['start'])} · {q.get('speaker','')}\n"
                         f"> “{q['text']}”" for q in summary.get("quotes", [])) \
        or "_（没有摘录原话）_"
    paraphrases = ""
    if summary.get("paraphrases"):
        paraphrases = ("\n\n> [!note] 以下为模型概括（未逐字命中原文，不是原话）\n> \n"
                       + "\n".join(f"- {C.fmt_duration(q.get('start'))} · {q.get('speaker','')}：{q['text']}"
                                   for q in summary["paraphrases"]))

    if summary.get("actions"):
        actions = "\n".join(["| 优先级 | 行动项 | 适用对象 | 来源时间 |", "|---|---|---|---|"]
                            + [f"| {a.get('priority','中')} | {a['item']} | {a.get('owner','听众')} | "
                               f"{C.fmt_duration(a.get('source_time')) if a.get('source_time') is not None else '—'} |"
                               for a in summary["actions"]])
    else:
        actions = "_（本期没有明确的可执行清单）_"

    chapters = "\n".join(f"| `{C.fmt_duration(c['start'])}` | {c['title']} | {c['summary'] or '—'} |"
                         for c in (summary.get("chapters") or [])) or "| `00:00:00` | 本期 | — |"

    shownotes = (meta.get("shownotes") or "").strip() or "_（该节目没有提供简介）_"
    uncertainties = ""
    if summary.get("uncertainties"):
        uncertainties = "\n## 不确定之处\n\n" + "\n".join(f"- {u}" for u in summary["uncertainties"]) + "\n"

    vtt_name = f"{stem}{suffix}.vtt"
    transcript_stem = f"{stem}{suffix} - 逐字稿"
    sub = cfg["vault"]["output_subdir"]
    tsub = cfg["vault"]["transcript_subdir"]
    materials = f"- [[{transcript_stem}|完整逐字稿]]"
    if cfg["retention"].get("keep_vtt", True):
        # N4：Obsidian 里「相对路径的 Markdown 链接」指向 .vtt 不可靠
        #（会被当成笔记名去解析），改用 wikilink 附件链接，能正常打开/下载。
        materials += f"\n- [[{sub}/{tsub}/{vtt_name}|WebVTT 字幕]]"

    tags = ["播客", meta.get("source_type") or "unknown"]
    album_tag = (meta.get("album") or "").replace(" ", "-")
    if album_tag:
        tags.append(album_tag)
    fm = "\n".join([
        f"title: {C.yaml_escape(meta.get('title'))}",
        f"aliases:\n  - {C.yaml_escape(summary.get('title') or meta.get('title'))}",
        f"source: {C.yaml_escape(meta.get('source_url'))}",
        f"source_type: {C.yaml_escape(meta.get('source_type'))}",
        f"track_id: {C.yaml_escape(meta.get('track_id'))}",
        f"album: {C.yaml_escape(meta.get('album'))}",
        f"creator: {C.yaml_escape(meta.get('creator'))}",
        f"published: {C.yaml_escape(meta.get('published'))}",
        f"duration_seconds: {C.yaml_escape(duration)}",
        f"duration: {C.yaml_escape(C.fmt_duration(duration))}",
        f"language: {C.yaml_escape(transcript.get('language') or cfg['asr'].get('language', 'zh'))}",
        f"transcribed_at: {C.yaml_escape(C.iso_now())}",
        f"asr_model: {C.yaml_escape(transcript.get('model'))}",
        ("speakers:\n" + "\n".join(f"  - {C.yaml_escape(s)}" for s in speakers))
        if speakers else "speakers: []",
        f"word_count: {C.yaml_escape(transcript.get('word_count'))}",
        f"summary_mode: {C.yaml_escape(cfg['summary'].get('mode', 'session'))}",
        'note_type: "podcast"',
        'status: "processed"',
        "tags:",
    ] + [f"  - {C.yaml_escape(t)}" for t in tags])

    tpl = (C.SKILL_ROOT / "templates" / "note.md").read_text(encoding="utf-8")
    main_md = fill(tpl, {
        "frontmatter": fm, "title": meta.get("title") or "未命名单集",
        "info_table": info_table, "tldr": summary.get("tldr") or "",
        "key_points": kp, "quotes": quotes, "paraphrases": paraphrases,
        "actions": actions, "chapters": chapters, "shownotes": shownotes,
        "uncertainties": uncertainties, "materials": materials,
        "speakers_note": ("> [!warning] 说话人说明\n"
                          "> 说话人标签来自 ASR 的说话人分离，跨块做了标签对齐；"
                          "若笔记里出现角色（主播/嘉宾），那是依据内容的推断，不是声纹识别身份。"),
    })

    body = []
    current = None
    chapter_list = summary.get("chapters") or []   # N5：不复用 chapters（那是上面渲染好的表格字符串）
    for s in segments:
        matched = None
        for ch in chapter_list:
            if s["start"] + 0.001 >= ch["start"]:
                matched = ch
            else:
                break
        head = matched["title"] if matched else (chapter_list[0]["title"] if chapter_list else "全文")
        if head != current:
            current = head
            body.append(f"\n## {C.fmt_short(s['start'])} {head}\n")
        body.append(f"[{C.fmt_duration(s['start'])}] **{s['speaker']}**：{s['text']}\n")
    transcript_md = "\n".join([
        "---",
        f"title: {C.yaml_escape((meta.get('title') or '') + ' - 逐字稿')}",
        f"parent: \"[[{stem}]]\"",
        f"source: {C.yaml_escape(meta.get('source_url'))}",
        f"asr_model: {C.yaml_escape(transcript.get('model'))}",
        'note_type: "podcast-transcript"',
        "tags:",
        "  - 播客",
        "  - 逐字稿",
        "---",
        "",
        f"# {meta.get('title')} - 逐字稿",
        "",
        "> [!note] 说明",
        f"> 时间戳与说话人来自 {transcript.get('model')}。返回主笔记：[[{stem}]]",
        "",
        "\n".join(body).strip(),
        "",
    ])
    return stem, main_md, transcript_md


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--job-dir", required=True)
    ap.add_argument("--config", required=True)
    ap.add_argument("--upsert", action="store_true", help="允许替换同名文件（默认拒绝）")
    ap.add_argument("--revision-suffix", default=None)
    ap.add_argument("--render-only", action="store_true", help="只渲染到缓存，不写库")
    args = ap.parse_args()

    cfg = C.load_config(args.config)
    jd = C.resolve_job_dir(cfg, args.job_dir)   # R7：统一校验 job 目录
    suffix = f" - {args.revision_suffix}" if args.revision_suffix else ""
    stem, main_md, transcript_md = render(jd, cfg, suffix)

    render_dir = jd / "render"
    render_dir.mkdir(parents=True, exist_ok=True)
    (render_dir / "main.md").write_text(main_md, encoding="utf-8")
    (render_dir / "transcript.md").write_text(transcript_md, encoding="utf-8")
    if args.render_only:
        C.emit(STAGE, job_id=jd.name,
               artifacts={"main": str(render_dir / "main.md"),
                          "transcript": str(render_dir / "transcript.md")},
               stem=stem, rendered_only=True)
        return

    raw_vault = str(cfg["vault"].get("path", "")).strip()
    if not raw_vault or raw_vault == "PLACEHOLDER_VAULT_PATH":
        raise C.Fail(STAGE, "VAULT_PLACEHOLDER",
                     "vault.path 仍是占位符，请在 config.toml 里填绝对路径。", C.EXIT_BAD_CONFIG)
    vault = Path(raw_vault).expanduser()
    if not vault.exists():
        raise C.Fail(STAGE, "VAULT_NOT_FOUND", f"vault 不存在：{vault}", C.EXIT_BAD_CONFIG)

    sub = cfg["vault"]["output_subdir"]
    tsub = cfg["vault"]["transcript_subdir"]
    targets = {
        "main": f"{sub}/{stem}{suffix}.md",
        "transcript": f"{sub}/{tsub}/{stem}{suffix} - 逐字稿.md",
    }
    if cfg["retention"].get("keep_vtt", True):
        targets["vtt"] = f"{sub}/{tsub}/{stem}{suffix}.vtt"

    upsert = args.upsert or cfg["vault"].get("publish_strategy") == "upsert"

    # P9：先把全部目标解析出来做一次预检，任一冲突就一个字节都不写
    plan = {k: C.safe_target(vault, *[p for p in rel.split("/") if p])
            for k, rel in targets.items()}
    if not upsert:
        clash = [p for p in plan.values() if C.target_exists(p)]   # R8：用公开别名
        if clash:
            raise C.Fail(STAGE, "TARGET_EXISTS",
                         "目标已存在，拒绝覆盖：" + "、".join(p.name for p in clash),
                         C.EXIT_TARGET_EXISTS)

    written = {}
    for key, target in plan.items():
        if key == "vtt":
            data = (jd / "transcript.vtt").read_bytes()
            if upsert:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
            else:
                C.exclusive_write_bytes(target, data)
        else:
            text = main_md if key == "main" else transcript_md
            if upsert:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(text, encoding="utf-8")
            else:
                C.exclusive_write(target, text)
        written[key] = str(target)

    C.update_job(jd / "job.json", {"stages": {"publish": {"status": "done",
                                                          "at": C.iso_now(),
                                                          "files": len(written)}}})
    C.write_json(jd / "receipt.json", {"job_id": jd.name, "published_at": C.iso_now(),
                                       "vault": str(vault), "files": written,
                                       "strategy": "upsert" if upsert else "create_only"})
    C.emit(STAGE, job_id=jd.name, artifacts=written, stem=stem,
           strategy="upsert" if upsert else "create_only",
           published_paths=list(written.values()))


if __name__ == "__main__":
    sys.exit(C.main_with_job(STAGE, main))
