#!/usr/bin/env python3
"""共享工具：JSON 协议、配置、路径安全、原子写入、时间格式。

约定：stdout 只输出一行 JSON；诊断信息全走 stderr。
绝不读取 vault 里已有笔记，只做精确路径新建。
"""
from __future__ import annotations

import html
import json
import os
import re
import shutil
import subprocess
import sys
import tomllib
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path

SCHEMA_VERSION = 2
SKILL_ROOT = Path(__file__).resolve().parent
CST = timezone(timedelta(hours=8))
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36")
CONTROL_RE = re.compile(r"[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f]")

EXIT_OK, EXIT_BAD_INPUT, EXIT_BAD_CONFIG, EXIT_NETWORK = 0, 2, 3, 4
EXIT_PAID, EXIT_MEDIA, EXIT_ASR = 5, 6, 7
EXIT_API, EXIT_VALIDATION, EXIT_TARGET_EXISTS, EXIT_FS, EXIT_PRIVACY = 9, 10, 11, 12, 13


class Fail(Exception):
    def __init__(self, stage: str, code: str, message: str, exit_code: int, **extra):
        super().__init__(message)
        self.stage, self.code, self.exit_code = stage, code, exit_code
        self.message = message
        self.extra = extra

    def payload(self) -> dict:
        # P7：exit_code 随错误一起输出，run.py 才能透传精确退出码
        d = {"code": self.code, "message": self.message, "exit_code": self.exit_code}
        d.update(self.extra)
        return d


def log(msg: str) -> None:
    sys.stderr.write(f"[podcast] {msg}\n")
    sys.stderr.flush()


def emit(stage: str, job_id: str | None = None, artifacts: dict | None = None,
         warnings=None, next_action=None, **fields) -> None:
    out = {"ok": True, "schema_version": SCHEMA_VERSION, "stage": stage, "job_id": job_id,
           "artifacts": artifacts or {}, "warnings": list(warnings or []),
           "next_action": next_action}
    out.update(fields)
    _line(out)


def fail(stage: str, err: Fail, job_id: str | None = None) -> None:
    _line({"ok": False, "schema_version": SCHEMA_VERSION, "stage": stage,
           "job_id": job_id, "error": err.payload()})


def _line(payload: dict) -> None:
    sys.stdout.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def main_with_job(stage: str, fn) -> int:
    job_id = None
    argv = sys.argv[1:]
    for i, a in enumerate(argv):
        if a == "--job-dir" and i + 1 < len(argv):
            job_id = Path(argv[i + 1]).expanduser().resolve().name
            break
        if a.startswith("--job-dir="):
            job_id = Path(a.split("=", 1)[1]).expanduser().resolve().name
            break
    try:
        fn()
        return EXIT_OK
    except Fail as e:
        fail(e.stage or stage, e, job_id)
        return e.exit_code
    except KeyboardInterrupt:
        fail(stage, Fail(stage, "INTERRUPTED", "已中断", EXIT_OK), job_id)
        return 130
    except OSError as e:
        fail(stage, Fail(stage, "FS_ERROR", f"文件系统错误：{e}", EXIT_FS), job_id)
        return EXIT_FS
    except Exception as e:  # P14：不破坏「stdout 单行 JSON」协议
        fail(stage, Fail(stage, "UNEXPECTED", f"{type(e).__name__}: {e}", EXIT_FS), job_id)
        return EXIT_FS


DEFAULTS = {
    "vault": {"path": "PLACEHOLDER_VAULT_PATH", "output_subdir": "播客",
              "transcript_subdir": "逐字稿", "publish_strategy": "create_only"},
    "runtime": {"cache_root": "~/.cache/podcast-to-obsidian",
                "python": "~/.cache/video-to-subtitle-summary/faster-whisper-venv/bin/python",
                "ffmpeg": "/opt/homebrew/bin/ffmpeg", "ffprobe": "/opt/homebrew/bin/ffprobe"},
    "asr": {"engine": "cloud", "language": "zh",
            "cloud_model": "XingChenAGI/XingChenASR-Diarize-V3.0",
            "cloud_base_url": "https://api.siliconflow.cn/v1",
            "allowed_hosts": ["api.siliconflow.cn"],
            "api_key_env": "SILICONFLOW_API_KEY",
            "chunk_minutes": 20, "overlap_seconds": 30, "concurrency": 4,
            "timeout_seconds": 600, "send_hotword": True,
            "initial_prompt_prefix": "以下是中文播客，可能涉及以下专有名词：",
            "local_model": "mobiuslabsgmbh/faster-whisper-large-v3-turbo",
            "local_fast_model": "small"},
    "summary": {"mode": "session", "block_minutes": 15, "target_min_chars": 800,
                "target_max_chars": 1500, "quote_match_window_seconds": 30,
                "prompt_file": "templates/summary_prompt.md"},
    "retention": {"keep_audio": True, "keep_vtt": True,
                  "audio_archive_dir": "音频库"},
    "filename": {"date_prefix": True, "max_title_chars": 80},
    "safety": {"scan_vault": False, "read_existing_notes": False},
}


def load_config(path) -> dict:
    p = Path(path).expanduser()
    if not p.is_file():
        raise Fail("config", "CONFIG_MISSING", f"配置文件不存在：{p}", EXIT_BAD_CONFIG)
    try:
        cfg = tomllib.loads(p.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as e:
        raise Fail("config", "CONFIG_INVALID", f"TOML 解析失败：{e}", EXIT_BAD_CONFIG)
    # P21：DEFAULTS 只有两层，去掉不可达的嵌套分支
    for section, values in DEFAULTS.items():
        node = cfg.setdefault(section, {})
        for key, val in values.items():
            if isinstance(val, list):
                node.setdefault(key, list(val))
            else:
                node.setdefault(key, val)
    if cfg["safety"].get("scan_vault") or cfg["safety"].get("read_existing_notes"):
        raise Fail("config", "PRIVACY_GUARD",
                   "safety.scan_vault / read_existing_notes 必须为 false", EXIT_PRIVACY)
    # P21：config 里的 schema_version 必须校验，否则是个没人读的装饰
    declared = cfg.get("schema_version")
    if declared is not None:
        try:
            declared_int = int(declared)
        except (TypeError, ValueError):
            raise Fail("config", "CONFIG_VERSION_INVALID",  # R9：非数字是配置错误(3)
                       f"schema_version 不是整数：{declared!r}", EXIT_BAD_CONFIG)
        if declared_int != SCHEMA_VERSION:
            raise Fail("config", "CONFIG_VERSION_MISMATCH",
                       f"config.toml 的 schema_version={declared}，"
                       f"当前代码要求 {SCHEMA_VERSION}", EXIT_BAD_CONFIG)
    return cfg


def reserved_job_names(cfg: dict) -> set[str]:
    """N3：缓存根下已经被占用的名字，不能拿来做 job id。

    主要是音频归档目录（默认 音频库）。相对路径取第一段，绝对路径只在
    它确实落在缓存根之内时才算保留名（否则 archive_dir() 会先报错）。
    """
    raw = str((cfg.get("retention") or {}).get("audio_archive_dir", "音频库") or "音频库")
    p = Path(raw).expanduser()
    if os.path.isabs(raw):
        try:
            arch = p.resolve()
        except OSError:
            return set()
        return {arch.name} if arch.parent == cache_root(cfg).resolve() else set()
    parts = [x for x in p.parts if x not in ("", ".")]
    return {parts[0]} if parts else set()


def resolve_job_dir(cfg: dict, raw: str, must_exist: bool = True) -> Path:
    """R7：四个脚本共用的 --job-dir 解析。

    接受两种形式：裸 job id（xm-123）或缓存根之内的绝对路径。
    其它位置的路径直接报错，不再静默只取最后一段。

    N2：默认要求目录已存在，否则报 JOB_NOT_FOUND（退出码 2），
    而不是让调用方在后半程撞上 KeyError / FileNotFoundError。
    清理类命令（audio --purge）可以显式传 must_exist=False。
    N3：`./xm-9` 与 `xm-9` 等价；保留名一律拒绝。
    """
    text = str(raw or "").strip()
    if not text:
        raise Fail("config", "NEED_JOB_DIR", "缺少 --job-dir", EXIT_BAD_INPUT)
    while text.startswith("./"):                 # N3：./name 与 name 等价
        text = text[2:]
    while len(text) > 1 and text[-1] in "/\\":   # 去掉结尾的斜杠
        text = text[:-1]
    if "/" not in text and "\\" not in text:     # 裸 job id
        jd = job_dir(cfg, text)
    else:
        p = Path(text).expanduser().resolve()
        root = cache_root(cfg).resolve()
        if p.parent != root:
            raise Fail("config", "JOB_NOT_IN_CACHE",
                       f"job 目录必须位于缓存根之内：{root}", EXIT_BAD_INPUT)
        jd = job_dir(cfg, p.name)
    if must_exist and not jd.is_dir():
        raise Fail("config", "JOB_NOT_FOUND", f"找不到 job 目录：{jd}", EXIT_BAD_INPUT)
    return jd


def cache_root(cfg: dict) -> Path:
    return Path(cfg["runtime"]["cache_root"]).expanduser()


def job_dir(cfg: dict, job_id: str) -> Path:
    """P1/P3：job id 一律校验，任何来源都无法把文件写到缓存目录之外。"""
    root = cache_root(cfg)
    text = str(job_id or "")
    if (not text or text in (".", "..") or os.path.isabs(text)
            or "/" in text or "\\" in text or CONTROL_RE.search(text)):
        raise Fail("config", "BAD_JOB_ID", f"非法 job id：{job_id!r}", EXIT_BAD_INPUT)
    # N3：别让 job 目录撞上音频归档目录，否则 --purge 会把归档当成 job
    if text in reserved_job_names(cfg):
        raise Fail("config", "RESERVED_JOB_ID",
                   f"job id 与保留目录同名：{job_id!r}", EXIT_BAD_INPUT)
    path = root / text
    root_r, path_r = root.resolve(), path.resolve()
    if path_r != root_r and root_r not in path_r.parents:
        raise Fail("config", "JOB_OUT_OF_CACHE", "job 目录越出缓存范围", EXIT_PRIVACY)
    return path


def check_url(url: str) -> str:
    """P2：只允许 http(s)，顺带挡掉 file:// 之类意外协议。"""
    import urllib.parse
    parts = urllib.parse.urlsplit(url or "")
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise Fail("download", "BAD_URL", f"只允许 http(s) 链接：{str(url)[:80]}", EXIT_BAD_INPUT)
    return url


def read_json(p) -> dict:
    return json.loads(Path(p).read_text(encoding="utf-8"))


def write_json(p, data) -> None:
    p = Path(p)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, p)


def update_job(job_path, patch: dict) -> dict:
    data = read_json(job_path) if Path(job_path).exists() else {}
    for k, v in patch.items():
        if isinstance(v, dict) and isinstance(data.get(k), dict):
            data[k].update(v)
        else:
            data[k] = v
    write_json(job_path, data)
    return data


def _target_exists(t: Path) -> bool:
    try:
        os.lstat(t)
        return True
    except FileNotFoundError:
        return False


target_exists = _target_exists  # R8：公开别名，避免调用方去碰私有函数


def _silent_unlink(p: Path) -> None:
    try:
        p.unlink()
    except OSError:
        pass


def exclusive_write(target: Path, text: str) -> None:
    """无覆盖写入：目标存在则抛 TARGET_EXISTS。

    P15：硬链接失败后的 rename 分支是 best-effort（存在极小 TOCTOU 窗口），
    但两条路径都会先确认目标不存在，不会静默覆盖。
    """
    if _target_exists(target):
        raise Fail("publish", "TARGET_EXISTS",
                   f"目标已存在，拒绝覆盖：{target.name}", EXIT_TARGET_EXISTS)
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.parent / (".__" + target.name + ".partial")
    if partial.exists():
        partial.unlink()
    try:
        fd = os.open(partial, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(text)
                f.flush()
                os.fsync(f.fileno())
        except OSError:
            os.close(fd)
            raise
        try:
            os.link(partial, target)
            os.unlink(partial)
            return
        except FileExistsError:
            raise Fail("publish", "TARGET_EXISTS",
                       f"目标已存在，拒绝覆盖：{target.name}", EXIT_TARGET_EXISTS)
        except OSError:
            pass
        if _target_exists(target):
            raise Fail("publish", "TARGET_EXISTS",
                       f"目标已存在，拒绝覆盖：{target.name}", EXIT_TARGET_EXISTS)
        os.replace(partial, target)
    except Fail:
        _silent_unlink(partial)
        raise
    except OSError as e:
        _silent_unlink(partial)
        raise Fail("publish", "WRITE_FAILED", f"写入失败：{e}", EXIT_FS)


ENV_SEARCH_PATHS = [
    SKILL_ROOT / ".env",                             # 标准 dotenv 位置，优先
    Path("~/.config/podcast-to-obsidian/.env"),      # 库外备份位置
    Path("~/.config/podcast-to-obsidian/env"),       # 旧命名，兼容
]


def env_file_path() -> Path | None:
    override = os.environ.get("PODCAST_ENV_FILE")
    candidates = ([Path(override).expanduser()] if override else []) + ENV_SEARCH_PATHS
    for p in candidates:
        p = p.expanduser()
        if p.is_file():
            return p
    return None


def _parse_dotenv(path: Path) -> dict:
    """P8：只保留一份解析实现（不依赖 python-dotenv），行为显式可控。

    支持：`#` 整行注释、行内注释、`export ` 前缀、成对的引号包裹。
    不做变量插值（密钥文件不需要），也不覆盖已有环境变量。
    """
    data: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if value[:1] in ("'", '"'):
            quote = value[0]
            end = value.find(quote, 1)
            # 引号内的 # 是内容，引号后的才是注释
            value = value[1:end] if end != -1 else value[1:].split("#", 1)[0].rstrip()
        else:
            value = value.split("#", 1)[0].rstrip()
        data[key] = value
    return data


def load_env() -> dict:
    """读取 .env（内置解析，不依赖任何第三方包）。"""
    path = env_file_path()
    if not path:
        return {}
    return _parse_dotenv(path)


def env_permission_warning() -> str | None:
    """.env 权限过宽时给一句提醒（dotenv 本身不检查）。"""
    path = env_file_path()
    if not path:
        return None
    mode = path.stat().st_mode & 0o777
    if mode & 0o077:
        return f"{path} 权限是 {oct(mode)}，建议改为 600（chmod 600）"
    return None


def api_key(env_name: str) -> str:
    """取密钥：优先进程环境变量，其次 .env 文件。绝不写进代码或日志。"""
    key = os.environ.get(env_name, "").strip()
    if key:
        return key
    warn = env_permission_warning()  # P8：把权限提醒真正接上
    if warn:
        log(warn)
    return load_env().get(env_name, "").strip()


def exclusive_write_bytes(target: Path, data: bytes) -> None:
    """P9：二进制版原子无覆盖写入（VTT 等），与 exclusive_write 同一套语义。"""
    if _target_exists(target):
        raise Fail("publish", "TARGET_EXISTS",
                   f"目标已存在，拒绝覆盖：{target.name}", EXIT_TARGET_EXISTS)
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.parent / (".__" + target.name + ".partial")
    if partial.exists():
        partial.unlink()
    try:
        fd = os.open(partial, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(data)
                f.flush()
                os.fsync(f.fileno())
        except OSError:
            os.close(fd)
            raise
        try:
            os.link(partial, target)
            os.unlink(partial)
            return
        except FileExistsError:
            raise Fail("publish", "TARGET_EXISTS",
                       f"目标已存在，拒绝覆盖：{target.name}", EXIT_TARGET_EXISTS)
        except OSError:
            pass
        if _target_exists(target):
            raise Fail("publish", "TARGET_EXISTS",
                       f"目标已存在，拒绝覆盖：{target.name}", EXIT_TARGET_EXISTS)
        os.replace(partial, target)
    except Fail:
        _silent_unlink(partial)
        raise
    except OSError as e:
        _silent_unlink(partial)
        raise Fail("publish", "WRITE_FAILED", f"写入失败：{e}", EXIT_FS)


def truncate_bytes(name: str, limit: int = 200) -> str:
    """P13：按字节截断（vault 可能在 ext4 / SMB 上，限制是 255 字节而非字符）。"""
    raw = name.encode("utf-8")
    if len(raw) <= limit:
        return name
    return raw[:limit].decode("utf-8", "ignore").rstrip(" .")


def read_hotwords(path) -> list[str]:
    p = Path(path)
    if not p.exists():
        return []
    out, seen = [], set()
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        term = unicodedata.normalize("NFKC", line)
        if term.casefold() not in seen:
            seen.add(term.casefold())
            out.append(term)
    return out


def fmt_duration(seconds) -> str:
    if seconds is None:
        return "--:--:--"
    s = int(max(0, round(float(seconds))))
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{sec:02d}"


def fmt_short(seconds) -> str:
    if seconds is None:
        return "--:--"
    s = int(max(0, round(float(seconds))))
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    return f"{h:02d}:{m:02d}" if h else f"{m:02d}:{sec:02d}"


def fmt_vtt(seconds: float) -> str:
    seconds = max(0.0, float(seconds))
    ms = int(round((seconds - int(seconds)) * 1000))
    total = int(seconds)
    if ms == 1000:
        total, ms = total + 1, 0
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}.{ms:03d}"


def iso_now() -> str:
    return datetime.now(timezone.utc).astimezone(CST).isoformat(timespec="seconds")


def ms_to_date(ms) -> str | None:
    if not ms:
        return None
    try:
        return datetime.fromtimestamp(int(ms) / 1000, tz=CST).strftime("%Y-%m-%d")
    except (OverflowError, OSError, ValueError):
        return None


def html_to_text(raw: str | None) -> str:
    if not raw:
        return ""
    t = re.sub(r"(?is)<(script|style).*?</\1>", " ", raw)
    t = re.sub(r"(?i)<br\s*/?>", "\n", t)
    t = re.sub(r"(?i)</p>", "\n", t)
    t = re.sub(r"<[^>]+>", " ", t)
    t = html.unescape(t)
    t = re.sub(r"[ \t\u3000]+", " ", t)
    return "\n".join(l.strip() for l in t.splitlines()).strip()


def norm_tokens(text: str) -> list[str]:
    t = unicodedata.normalize("NFKC", text or "").casefold()
    return [c for c in t if c.isalnum() or "\u4e00" <= c <= "\u9fff"]


def similarity(a: str, b: str) -> float:
    ta, tb = norm_tokens(a), norm_tokens(b)
    if not ta or not tb:
        return 0.0
    if ta == tb:
        return 1.0
    # 长度差过大时先做廉价预筛，省掉 O(n·m) 的动态规划。
    # R10 前提：调用方的阈值必须 > 0.46 —— 长度比 0.3 时 Dice 的理论上界约 0.46，
    # 低于它的阈值会被这个预筛误判为 0。当前调用方阈值是 0.55 / 0.85，满足前提。
    if min(len(ta), len(tb)) / max(len(ta), len(tb)) < 0.3:
        return 0.0
    prev = [0] * (len(tb) + 1)
    for ca in ta:
        cur = [0]
        for j, cb in enumerate(tb, 1):
            cur.append(prev[j - 1] + 1 if ca == cb else max(prev[j], cur[j - 1]))
        prev = cur
    return 2 * prev[-1] / (len(ta) + len(tb))


def sanitize_filename(name: str, max_chars: int = 80) -> str:
    name = unicodedata.normalize("NFC", name or "")
    name = re.sub(r"[/\\]", " ", name)
    name = CONTROL_RE.sub("", name)
    for bad, good in ((":", "\uff1a"), ("*", "\uff0a"), ("?", "\uff1f"),
                      ('"', "\u201d"), ("<", "\u300a"), (">", "\u300b"),
                      ("|", "\u4e28")):
        name = name.replace(bad, good)
    name = re.sub(r"\s+", " ", name).strip(" .")
    if not name:
        return "untitled"
    return name[:max_chars].rstrip(" .") if len(name) > max_chars else name


def yaml_escape(v) -> str:
    if v is None:
        return "null"  # P19：空值写成 null，而不是空字符串
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    t = str(v).replace("\\", "\\\\").replace('"', '\\"')
    t = t.replace("\r", " ").replace("\n", " ")
    return '"' + CONTROL_RE.sub("", t) + '"'


def has_symlink_component(target: Path, root: Path) -> bool:
    try:
        rel = target.relative_to(root)
    except ValueError:
        return False
    cur = root
    for part in rel.parts:
        cur = cur / part
        if cur.is_symlink():
            return True
    return False


def safe_target(root: Path, *parts: str) -> Path:
    for p in parts:
        if not p or os.path.isabs(p) or ".." in Path(p).parts or CONTROL_RE.search(p):
            raise Fail("publish", "BAD_SUBPATH", f"非法路径片段：{p!r}", EXIT_PRIVACY)
    target = root.joinpath(*[p for p in parts if p])
    try:
        if os.path.commonpath([str(root), str(target)]) != str(root):
            raise ValueError
    except ValueError:
        raise Fail("publish", "PATH_ESCAPE", "目标路径越出 vault 范围", EXIT_PRIVACY)
    if has_symlink_component(target, root):
        raise Fail("publish", "SYMLINK_DETECTED", "路径经过符号链接，拒绝写入", EXIT_PRIVACY)
    return target


def tool_path(cfg: dict, name: str) -> str:
    """外部命令路径：配置里的绝对路径优先，找不到再回落到 PATH 里的同名命令。

    这样同一份 config.toml 换台机器（Homebrew 前缀不同、brew 装在 /usr/local）
    也能跑通，而不是直接报 TOOL_MISSING。
    """
    raw = str((cfg.get("runtime") or {}).get(name) or name).strip()
    p = Path(raw).expanduser()
    if p.is_file():
        return str(p)
    return shutil.which(p.name) or shutil.which(name) or raw


def run(cmd: list[str], timeout: int | None = None, check: bool = True):
    try:
        cp = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            timeout=timeout)
    except FileNotFoundError:
        raise Fail("subprocess", "TOOL_MISSING", f"找不到：{cmd[0]}", EXIT_MEDIA)
    except subprocess.TimeoutExpired:
        raise Fail("subprocess", "TOOL_TIMEOUT", f"超时：{cmd[0]}", EXIT_MEDIA)
    if check and cp.returncode != 0:
        raise Fail("subprocess", "TOOL_FAILED",
                   f"{Path(cmd[0]).name} 退出码 {cp.returncode}："
                   f"{cp.stderr.decode('utf-8', 'replace')[-400:]}", EXIT_MEDIA)
    return cp


def http_bytes(url: str, referer: str | None = None, timeout: int = 30,
               max_bytes: int = 10 * 1024 * 1024) -> bytes:
    """P10：带上大小上限，避免远端返回超大响应把内存吃掉。"""
    import urllib.request
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    if referer:
        req.add_header("Referer", referer)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = resp.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise Fail("network", "TOO_LARGE",
                   f"响应超过 {max_bytes // 1048576} MB，已拒绝", EXIT_NETWORK)
    return data


def http_json(url: str, referer: str | None = None, timeout: int = 30) -> dict:
    return json.loads(http_bytes(url, referer, timeout).decode("utf-8", "replace"))


def ffprobe_duration(ffprobe: str, path) -> float:
    cp = run([ffprobe, "-v", "error", "-show_entries", "format=duration",
              "-of", "default=noprint_wrappers=1:nokey=1", str(path)], check=False)
    try:
        return float(cp.stdout.decode().strip())
    except ValueError:
        raise Fail("media", "PROBE_FAILED", "读不到音频时长", EXIT_MEDIA)


def ffprobe_container(ffprobe: str, path) -> str:
    cp = run([ffprobe, "-v", "error", "-show_entries", "format=format_name",
              "-of", "default=noprint_wrappers=1:nokey=1", str(path)], check=False)
    name = cp.stdout.decode().strip().lower()
    if not name:
        return "mp3"
    for token, ext in (("mp4", "m4a"), ("m4a", "m4a"), ("aac", "m4a"), ("mov", "m4a"),
                       ("mp3", "mp3"), ("flac", "flac"), ("wav", "wav"), ("pcm", "wav"),
                       ("ogg", "ogg"), ("opus", "ogg")):
        if token in name:
            return ext
    return name.split(",")[0] or "audio"
