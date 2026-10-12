#!/usr/bin/env python3
"""总结层：分块读稿 + 终稿校验（纯会话模式，不调用任何外部对话模型）。

  plan   → 把逐字稿切成若干块，写到 <job>/blocks/
  show   → 取某一块的正文
  add    → 存入某一块的摘要（由会话模型或子 Agent 产出）
  final  → 校验金句是否逐字命中，写入 summary.json
  prompt → 打印总结提示词（可编辑的那份）+ 本期节目信息

用法：
  summarize.py plan   --job-dir <dir> --config <c>
  summarize.py show   --job-dir <dir> --config <c> --block 3
  summarize.py add    --job-dir <dir> --config <c> --block 3 --json '<abstract>'
  summarize.py final  --job-dir <dir> --config <c> --json '<summary>'
  summarize.py prompt --job-dir <dir> --config <c>

说明：这里没有任何 LLM 调用。读稿与综合都由会话模型完成；
节目很长时可以让子 Agent 逐块压缩，再把摘要用 add 回写。

子 Agent 只处理局部：给它一块 15 分钟的稿，它只产出该块的章节、局部要点和金句，
不该让它猜全篇的核心观点（它看不到全局）。全篇的 tldr 与【核心】要点在 final 阶段统揽。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import core as C  # noqa: E402

STAGE = "summarize"


def blocks_dir(jd: Path) -> Path:
    return jd / "blocks"


def make_plan(jd: Path, cfg: dict) -> dict:
    transcript = C.read_json(jd / "transcript.json")
    segments = transcript.get("segments", [])
    if not segments:
        raise C.Fail(STAGE, "NO_TRANSCRIPT", "逐字稿为空，先跑 transcribe", C.EXIT_VALIDATION)
    block_s = float(cfg["summary"].get("block_minutes", 15)) * 60
    # P3：只删上一次 plan 记录过的块，不删「目录里长得像的东西」
    out_dir = blocks_dir(jd)
    old_index = out_dir / "index.json"
    if old_index.is_file():
        for b in C.read_json(old_index).get("blocks", []):
            old = Path(b.get("file") or "")
            if old.parent == out_dir and old.name.startswith("block-"):
                old.unlink(missing_ok=True)
    out_dir.mkdir(parents=True, exist_ok=True)

    blocks, current, start = [], [], None
    for seg in segments:
        if start is None:
            start = seg["start"]
        current.append(seg)
        if seg["end"] - start >= block_s:
            blocks.append((start, seg["end"], current))
            current, start = [], None
    if current:
        blocks.append((start or 0.0, segments[-1]["end"], current))

    index = []
    for i, (s, e, segs) in enumerate(blocks, 1):
        text = "\n".join(f"[{C.fmt_duration(x['start'])}] {x['speaker']}：{x['text']}" for x in segs)
        path = out_dir / f"block-{i:03d}.md"
        path.write_text(f"# 第 {i}/{len(blocks)} 块  {C.fmt_duration(s)} - {C.fmt_duration(e)}\n\n"
                        f"{text}\n", encoding="utf-8")
        index.append({"block": i, "start": round(s, 1), "end": round(e, 1),
                      "chars": len(text), "file": str(path)})
    C.write_json(out_dir / "index.json", {"blocks": index})
    return {"count": len(index), "blocks": index,
            "total_chars": sum(b["chars"] for b in index)}


def window_text(segments, target: float, window: float) -> str:
    return "".join(s["text"] for s in segments
                   if s["end"] >= target - window and s["start"] <= target + window)


def verify_quote(text: str, hay: str) -> bool:
    """P5：逐字稿是连续语流，真原话归一化后必须是**连续子串**。

    旧实现用子序列匹配，导致「只是用到同样的字」也被判为原话
    （例如 "好的很难" 能匹配 "好的产品是很难改变的"）。
    """
    n, h = C.norm_tokens(text), C.norm_tokens(hay)
    if not n or not h:
        return False
    return "".join(n) in "".join(h)


CORE_PREFIX, DETAIL_PREFIX = "【核心】", "【细节】"


def split_heading(heading) -> tuple[bool, str]:
    """拆出要点标题里的分层前缀。

    提示词要求核心要点以【核心】开头、其余以【细节】开头。前缀只是给代码看的标记，
    不进最终笔记：这里返回 (是否核心, 剥掉前缀后的标题)。
    """
    text = str(heading or "").strip()
    is_core = text.startswith(CORE_PREFIX)
    for prefix in (CORE_PREFIX, DETAIL_PREFIX):
        if text.startswith(prefix):
            text = text[len(prefix):].strip()
            break
    return is_core, text or "要点"


def store_final(jd: Path, cfg: dict, raw: dict) -> tuple[dict, list[str]]:
    transcript = C.read_json(jd / "transcript.json")
    segments = transcript.get("segments", [])
    window = float(cfg["summary"].get("quote_match_window_seconds", 30))
    warnings: list[str] = []

    if not raw.get("tldr"):
        raise C.Fail(STAGE, "NO_TLDR", "缺少 TL;DR", C.EXIT_VALIDATION)
    points = [p for p in (raw.get("key_points") or []) if isinstance(p, dict)
              and (p.get("heading") or p.get("detail"))]
    if not points:
        raise C.Fail(STAGE, "NO_KEY_POINTS", "缺少结构化要点", C.EXIT_VALIDATION)

    quotes, paraphrases = [], []
    for q in raw.get("quotes") or []:
        if not isinstance(q, dict) or not q.get("text"):
            continue
        start = q.get("start")
        hit = isinstance(start, (int, float)) and verify_quote(
            q["text"], window_text(segments, float(start), window))
        (quotes if hit else paraphrases).append(q)
        if not hit:
            warnings.append(f"金句未逐字命中，降级为模型概括：{q['text'][:20]}…")

    chapters = []
    last = -1.0
    duration = transcript.get("duration") or (segments[-1]["end"] if segments else 0)
    for ch in raw.get("chapters") or []:
        if not isinstance(ch, dict) or not isinstance(ch.get("start"), (int, float)):
            continue
        start = min(max(float(ch["start"]), 0.0), max(float(duration) - 1, 0))
        if start < last:
            start = last
        last = start
        chapters.append({"start": start, "title": ch.get("title") or "章节",
                         "summary": ch.get("summary") or ""})

    key_points = []
    for p in points:
        is_core, heading = split_heading(p.get("heading"))
        key_points.append({
            "is_core": is_core,                       # 前端/渲染据此区分核心与细节
            "heading": heading,
            "detail": p.get("detail") or "",
            "evidence_times": sorted({round(float(t), 2) for t in
                                      (p.get("evidence_times") or [])
                                      if isinstance(t, (int, float))}),
        })

    summary = {
        "schema_version": C.SCHEMA_VERSION,
        "title": raw.get("title"),
        "tldr": raw["tldr"],
        "key_points": key_points,
        "quotes": quotes,
        "paraphrases": paraphrases,
        "actions": [a for a in (raw.get("actions") or []) if isinstance(a, dict) and a.get("item")],
        "chapters": chapters,
        "speaker_roles": raw.get("speaker_roles") or [],
        "uncertainties": raw.get("uncertainties") or [],
    }
    # 章节摘要也算正文：否则模型把细节全塞进 chapters.summary 时，字数校验形同虚设，
    # JSON 体积会悄悄膨胀而没有任何 warning。
    body = (summary["tldr"]
            + "".join(p["heading"] + p["detail"] for p in summary["key_points"])
            + "".join(ch["summary"] for ch in summary["chapters"]))
    n = len([c for c in body if not c.isspace()])
    lo = int(cfg["summary"].get("target_min_chars", 1200))
    hi = int(cfg["summary"].get("target_max_chars", 2500))
    if n < lo:
        warnings.append(f"正文约 {n} 字，低于目标 {lo}。")
    elif n > hi * 1.6:
        warnings.append(f"正文约 {n} 字，超过目标 {hi} 较多。")
    if not any(p["is_core"] for p in key_points):
        warnings.append("要点里没有一条以【核心】开头，核心/细节分层可能没生效。")
    C.write_json(jd / "summary.json", summary)
    C.update_job(jd / "job.json", {"stages": {"summarize": {"status": "done",
                                                            "at": C.iso_now()}}})
    return summary, warnings


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("action", choices=["plan", "show", "add", "final", "prompt"])
    ap.add_argument("--job-dir", required=True)
    ap.add_argument("--config", default=None,
                    help="配置文件；默认自动挑 config.local.toml / config.toml")
    ap.add_argument("--block", type=int, default=None)
    ap.add_argument("--json", dest="json_str", default=None, help="小块摘要可直接传字符串")
    # P12：终稿 JSON 可能上万字符，走文件更安全（ARG_MAX / 引号转义）
    ap.add_argument("--json-file", default=None, help="终稿 JSON 文件路径；- 表示 stdin")
    args = ap.parse_args()

    def read_payload() -> str:
        if args.json_file == "-":
            return sys.stdin.read()
        if args.json_file:
            return Path(args.json_file).read_text(encoding="utf-8")
        return args.json_str or "{}"

    cfg = C.load_config(args.config)
    jd = C.resolve_job_dir(cfg, args.job_dir)   # R7：统一校验 job 目录
    payload = read_payload() if args.action in ("add", "final") else ""  # R3：只读一次

    if args.action == "prompt":
        rel = cfg["summary"].get("prompt_file", "templates/summary_prompt.md")
        path = Path(rel)
        if not path.is_absolute():
            path = C.SKILL_ROOT / rel
        if not path.exists():
            raise C.Fail(STAGE, "PROMPT_MISSING", f"提示词文件不存在：{path}",
                         C.EXIT_BAD_CONFIG)
        meta = C.read_json(jd / "metadata.json") if (jd / "metadata.json").exists() else {}
        C.emit(STAGE, job_id=jd.name,
               artifacts={"prompt_file": str(path)},
               prompt=path.read_text(encoding="utf-8"),
               program=meta.get("album"), title=meta.get("title"),
               published=meta.get("published"),
               duration=C.fmt_duration(meta.get("duration_seconds")))
        return

    if args.action == "plan":
        plan = make_plan(jd, cfg)
        C.emit(STAGE, job_id=jd.name,
               artifacts={"index": str(blocks_dir(jd) / "index.json")},
               next_action={"kind": "summarize_blocks",
                            "hint": "依次读 blocks/block-*.md（长节目可派子 Agent 逐块压缩），"
                                    "用 add 回写摘要，最后 final。"
                                    "子 Agent 只做局部抽取：该块的 chapters、局部要点（标【细节】）、"
                                    "quotes，别让它提炼全篇核心观点或 TL;DR——它只看得到 15 分钟；"
                                    "全篇的 tldr 与【核心】要点留到 final 统揽时再写。"
                                    "详见提示词里的「分块读稿阶段」一节。"},
               **plan)
    elif args.action == "show":
        index = C.read_json(blocks_dir(jd) / "index.json")["blocks"]
        b = next((x for x in index if x["block"] == args.block), None)
        if not b:
            raise C.Fail(STAGE, "BAD_BLOCK", f"没有第 {args.block} 块", C.EXIT_BAD_INPUT)
        C.emit(STAGE, job_id=jd.name, block=b["block"],
               text=Path(b["file"]).read_text(encoding="utf-8"))
    elif args.action == "add":
        if not args.block or not payload.strip():
            raise C.Fail(STAGE, "BAD_INPUT", "需要 --block 和 --json / --json-file",
                         C.EXIT_BAD_INPUT)
        data = json.loads(payload)
        target = blocks_dir(jd) / f"abstract-{args.block:03d}.json"
        C.write_json(target, data)
        C.emit(STAGE, job_id=jd.name, artifacts={"abstract": str(target)}, block=args.block)
    elif args.action == "final":
        raw = json.loads(payload)
        summary, warnings = store_final(jd, cfg, raw)
        C.emit(STAGE, job_id=jd.name, artifacts={"summary": str(jd / "summary.json")},
               warnings=warnings, quotes=len(summary["quotes"]),
               paraphrases=len(summary["paraphrases"]),
               chapters=len(summary["chapters"]))
    else:
        raise C.Fail(STAGE, "BAD_ACTION", f"不支持的动作：{args.action}", C.EXIT_BAD_INPUT)


if __name__ == "__main__":
    sys.exit(C.main_with_job(STAGE, main))
