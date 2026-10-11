#!/usr/bin/env python3
"""主编排：下载 → 转写 → 总结 → 落盘。每步先查缓存，有就跳过（天然断点续跑）。

用法：
  run.py --input <链接或音频路径> --config <config.toml> [--engine cloud|local] [--fast]
  run.py --job-dir <dir> --config <config.toml>          # 从已有 job 继续
  run.py status --job-dir <dir> --config <config.toml>
  run.py audio --config <config.toml> --list
  run.py audio --config <config.toml> --purge [--all]
  run.py audio --config <config.toml> --purge --job-dir <dir>

--purge 同时清理归档音频和转写分片残留（pieces/），两者都需要明确范围。
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import core as C  # noqa: E402

STAGE = "run"
SCRIPTS = C.SKILL_ROOT


def sh(script: str, *args) -> tuple[int, dict]:
    cp = subprocess.run([sys.executable, str(SCRIPTS / script), *args],
                        capture_output=True, text=True)
    try:
        data = json.loads(cp.stdout.strip().splitlines()[-1])
    except (IndexError, ValueError):
        data = {"ok": False, "error": {"code": "NO_JSON_OUTPUT",
                                       "message": cp.stderr[-400:] or cp.stdout[-400:]}}
    if cp.returncode != 0 and cp.stderr:
        C.log(cp.stderr.strip()[-300:])
    return cp.returncode, data


def raise_from(data: dict, fallback_code: str, fallback_exit: int, **extra) -> None:
    """P7：把子脚本的精确退出码透传出来，不再一律变成网络错误。"""
    err = data.get("error") or {}
    raise C.Fail(STAGE, err.get("code", fallback_code),
                 err.get("message", "子步骤失败"),
                 err.get("exit_code", fallback_exit), **extra)


def status(jd: Path) -> dict:
    stages = {}
    for name, files in (("download", ["metadata.json"]),
                        ("transcribe", ["transcript.json"]),
                        ("summarize", ["summary.json"]),
                        ("publish", ["receipt.json"])):
        stages[name] = all((jd / f).exists() for f in files)
    blocks = jd / "blocks" / "index.json"
    abstracts = sorted((jd / "blocks").glob("abstract-*.json")) if (jd / "blocks").exists() else []
    stages["blocks"] = blocks.exists()
    stages["abstracts"] = len(abstracts)
    return stages


def archive_dir(cfg: dict) -> Path:
    """R1：归档目录必须落在缓存根之内，避免 `--purge --all` 变成「删任意目录」。"""
    rel = cfg["retention"].get("audio_archive_dir", "音频库")
    p = Path(rel).expanduser()
    arch = p if p.is_absolute() else C.cache_root(cfg) / p
    arch_r, root_r = arch.resolve(), C.cache_root(cfg).resolve()
    if arch_r != root_r and root_r not in arch_r.parents:
        raise C.Fail(STAGE, "ARCHIVE_OUT_OF_CACHE",
                     f"audio_archive_dir 不在缓存根之内：{arch}", C.EXIT_PRIVACY)
    return arch


def audio_list(cfg: dict) -> dict:
    arch = archive_dir(cfg)
    files = sorted(arch.glob("*")) if arch.exists() else []
    in_job = []
    root = C.cache_root(cfg)
    if root.exists():
        for job in root.iterdir():
            if not job.is_dir():
                continue
            for f in (job / "audio").glob("source.*") if (job / "audio").exists() else []:
                in_job.append(str(f))
    pieces = []
    for job in (root.iterdir() if root.exists() else []):
        pdir = job / "pieces"
        if pdir.is_dir():
            size = sum(f.stat().st_size for f in pdir.rglob("*") if f.is_file())
            if size:
                pieces.append({"job": job.name, "mb": round(size / 1048576, 1)})
    return {
        "archive_dir": str(arch),
        "archived": [{"file": str(f), "mb": round(f.stat().st_size / 1048576, 1)} for f in files],
        "archived_total_mb": round(sum(f.stat().st_size for f in files) / 1048576, 1),
        "still_in_job": in_job,
        "pieces": pieces,                      # R6：成功转写会自动清，这里显示残留
        "pieces_total_mb": round(sum(p["mb"] for p in pieces), 1),
    }


def purge_pieces(cfg: dict, job_id: str | None, all_jobs: bool) -> tuple[list[str], int]:
    """N6：清理转写分片残留。

    `--list` 会把 pieces 占用显示出来，但之前没有任何命令能清掉它。
    成功转写会自动清理，所以这里只处理失败/中断留下的孤儿目录。
    """
    root = C.cache_root(cfg)
    removed: list[str] = []
    freed = 0
    if not root.exists():
        return removed, freed
    for job in sorted(root.iterdir()):
        if not job.is_dir():
            continue
        if job_id and job.name != job_id:
            continue
        pdir = job / "pieces"
        if not pdir.is_dir():
            continue
        size = sum(f.stat().st_size for f in pdir.rglob("*") if f.is_file())
        shutil.rmtree(pdir, ignore_errors=True)
        if pdir.exists():                     # 没删掉就不记账，避免误报已清理
            continue
        removed.append(str(pdir))
        freed += size
    return removed, freed


def audio_purge(cfg: dict, job_id: str | None, all_jobs: bool) -> dict:
    # R1：没有明确范围就拒绝，而不是「默认全清」
    if not all_jobs and not job_id:
        raise C.Fail(STAGE, "NEED_SCOPE",
                     "purge 需要明确范围：--all（全部）或 --job-dir <dir>（单期）",
                     C.EXIT_BAD_INPUT)
    arch = archive_dir(cfg)
    removed, freed = [], 0
    if arch.exists():
        for f in sorted(arch.glob("*")):
            if not f.is_file():                       # 别对目录 unlink
                continue
            if job_id and f.stem != job_id:           # R5：精确匹配，不误伤 xm-1111
                continue
            size = f.stat().st_size
            f.unlink()
            removed.append(str(f))
            freed += size
    pieces, pieces_bytes = purge_pieces(cfg, job_id, all_jobs)   # N6
    out = {"removed": removed + pieces,
           "freed_mb": round((freed + pieces_bytes) / 1048576, 1),
           "pieces_removed": pieces}
    if not out["removed"]:
        # 范围写错时静默「清理成功」最容易误导，这里明确提示没匹配到
        out["warnings"] = [f"没有匹配到任何可清理文件（job={job_id or '全部'}），"
                           "确认 job id 是否与 audio --list 里的一致"]
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("action", nargs="?", default="run",
                    choices=["run", "status", "audio"])
    ap.add_argument("--input", default=None)
    ap.add_argument("--job-dir", default=None)
    ap.add_argument("--config", default=None,
                    help="配置文件；默认自动挑 config.local.toml / config.toml")
    ap.add_argument("--engine", default=None)
    ap.add_argument("--fast", action="store_true")
    ap.add_argument("--upsert", action="store_true", help="允许替换同名文件")
    ap.add_argument("--revision-suffix", default=None)
    ap.add_argument("--render-only", action="store_true")
    ap.add_argument("--all", action="store_true", help="audio purge 时清空全部")
    ap.add_argument("--list", action="store_true", help="audio：列出归档音频")
    ap.add_argument("--purge", action="store_true", help="audio：清理归档音频")
    args = ap.parse_args()

    # 解析成绝对路径再往下传：子脚本拿到同一个文件，不受各自 cwd 影响
    args.config = str(C.resolve_config_path(args.config))
    cfg = C.load_config(args.config)

    if args.action == "audio":
        if args.purge:
            # N1：越界的 --job-dir 必须报错，不能静默只取最后一段。
            # N2：清理场景允许 job 目录本身已不存在（归档文件可能还在），
            # 所以显式 must_exist=False，匹配不到时会给出 warning。
            job_id = (C.resolve_job_dir(cfg, args.job_dir, must_exist=False).name
                      if args.job_dir else None)
            result = audio_purge(cfg, job_id, args.all)
            C.emit(STAGE, **result)
        else:
            C.emit(STAGE, **audio_list(cfg))
        return

    if args.action == "status":
        if not args.job_dir:
            raise C.Fail(STAGE, "NEED_JOB_DIR", "status 需要 --job-dir", C.EXIT_BAD_INPUT)
        jd = C.resolve_job_dir(cfg, args.job_dir)
        C.emit(STAGE, job_id=jd.name, stages=status(jd))
        return

    # R7：--job-dir 统一走 resolve_job_dir，不再静默只取最后一段
    jd = C.resolve_job_dir(cfg, args.job_dir) if args.job_dir else None
    if jd is None:
        if not args.input:
            raise C.Fail(STAGE, "NEED_INPUT", "需要 --input 或 --job-dir", C.EXIT_BAD_INPUT)
        code, data = sh("download.py", "--input", args.input, "--config", args.config)
        if not data.get("ok"):
            raise_from(data, "DOWNLOAD_FAILED", C.EXIT_NETWORK)
        jd = C.job_dir(cfg, data["job_id"])

    if not status(jd)["transcribe"]:
        cmd = ["transcribe.py", "--job-dir", str(jd), "--config", args.config]
        if args.engine:
            cmd += ["--engine", args.engine]
        if args.fast:
            cmd += ["--fast"]
        code, data = sh(*cmd)
        if not data.get("ok"):
            raise_from(data, "ASR_FAILED", C.EXIT_ASR)
        C.log(f"转写完成：{data.get('segments')} 段，说话人 {data.get('speakers')}")

    # 总结只走会话模型：读稿与综合都由我完成，不调用任何外部对话模型。
    if not (jd / "blocks" / "index.json").exists():
        code, data = sh("summarize.py", "plan", "--job-dir", str(jd), "--config", args.config)
        if not data.get("ok"):
            raise_from(data, "PLAN_FAILED", C.EXIT_VALIDATION)
        C.log("已切好分块，等会话模型逐块读稿后 final。")
    if not (jd / "summary.json").exists():
        C.emit(STAGE, job_id=jd.name, waiting_for="session_summary",
               next_action={"kind": "session_summary",
                            "blocks": str(jd / "blocks" / "index.json"),
                            "hint": "依次读 blocks/block-*.md（长节目可派子 Agent 压缩），"
                                    "add 回写，再 final，然后 publish。"})
        return

    if args.render_only:
        code, data = sh("publish.py", "--job-dir", str(jd), "--config", args.config,
                        "--render-only")
        if not data.get("ok"):
            raise_from(data, "PUBLISH_FAILED", C.EXIT_FS)
    else:
        cmd = ["publish.py", "--job-dir", str(jd), "--config", args.config]
        if args.upsert:
            cmd += ["--upsert"]
        if args.revision_suffix:
            cmd += ["--revision-suffix", args.revision_suffix]
        code, data = sh(*cmd)
        if not data.get("ok"):
            # R2：publish 失败必须是非零退出，否则上层会以为已入库
            raise_from(data, "PUBLISH_FAILED", C.EXIT_FS,
                       hint="可能需要先填 vault.path，或加 --upsert / --revision-suffix")
    C.emit(STAGE, job_id=jd.name, stages=status(jd))


if __name__ == "__main__":
    sys.exit(C.main_with_job(STAGE, main))
