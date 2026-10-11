#!/usr/bin/env python3
"""首次配置：只问两样东西，写完就能用（macOS / Linux / Windows 通用）。

  python3 setup.py                  # 交互式：问库路径 + API key（推荐）
  python3 setup.py --check          # 只体检，不改任何文件（退出码恒为 0）
  python3 setup.py --vault "<路径>"  # 只写库路径
  python3 setup.py --key-stdin      # 从 stdin 读 key（脚本 / Agent 用）
  python3 setup.py --verify         # 校验库可写 + key 有效

写到哪儿：
  [vault].path  → config.local.toml（本机私有，已在 .gitignore）
  API key       → <skill>/.env（POSIX 下 600，Windows 下尝试 icacls）

三条硬规则：
  1. key 不进 stdout / 日志，交互时用隐藏输入，回显只给尾 4 位；
  2. stdout 仍然只输出一行 JSON，人看的提示全部走 stderr；
  3. 只校验库目录是否存在、是否可写，不列目录、不读笔记。
"""
from __future__ import annotations

import argparse
import getpass
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import core as C  # noqa: E402

STAGE = "setup"


def say(msg: str = "") -> None:
    """给人看的输出一律走 stderr，避免污染 stdout 的单行 JSON 协议。"""
    try:
        sys.stderr.write(msg + "\n")
    except UnicodeEncodeError:
        enc = getattr(sys.stderr, "encoding", None) or "utf-8"
        sys.stderr.write(msg.encode(enc, "replace").decode(enc, "replace") + "\n")
    sys.stderr.flush()


# ── 路径与写入 ────────────────────────────────────────────────────────────


def target_local_config(args) -> Path:
    """本地私有配置的落点：--local-config > 与 --config 同目录 > skill 根。"""
    if args.local_config:
        return Path(args.local_config).expanduser().resolve()
    base = C.resolve_config_path(args.config)
    if base.name == C.LOCAL_CONFIG_NAME:
        return base
    return (base.parent / C.LOCAL_CONFIG_NAME).resolve()


def target_env_file(args) -> Path:
    if args.env_file:
        return Path(args.env_file).expanduser().resolve()
    return C.default_env_file()


def _toml_str(v: str) -> str:
    return '"' + str(v).replace("\\", "\\\\").replace('"', '\\"') + '"'


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="\n") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def write_vault(path: Path, vault: str) -> bool:
    """把 [vault].path 写进本地配置；文件里其它配置项原样保留。"""
    line = f"path = {_toml_str(vault)}"
    if path.is_file():
        lines = path.read_text(encoding="utf-8").splitlines()
        out, in_vault, replaced = [], False, False
        for ln in lines:
            stripped = ln.strip()
            if stripped.startswith("[") and stripped.endswith("]"):
                in_vault = (stripped == "[vault]")
            if in_vault and re.match(r"^\s*path\s*=", ln) and not replaced:
                out.append(line)
                replaced = True
                continue
            out.append(ln)
        if not replaced:
            out += ["", "[vault]", line]
        text = "\n".join(out).rstrip("\n") + "\n"
        if text == path.read_text(encoding="utf-8"):
            return False
    else:
        text = ("# podscribe 本地私有配置：不要提交（已在 .gitignore）。\n"
                "# 由 `python3 setup.py` 生成，也可以手工编辑；它叠加在 config.toml 之上。\n"
                "\n[vault]\n" + line + "\n")
    _atomic_write_text(path, text)
    return True


def harden_permissions(p: Path) -> str | None:
    """尽量把密钥文件收成「只有自己能读」，返回一句提示（没问题时 None）。"""
    if os.name != "nt":
        try:
            os.chmod(p, 0o600)
            return None
        except OSError as e:
            return f"未能设置 600 权限（{e}），请手动 chmod 600 {p}"
    user = os.environ.get("USERNAME") or os.environ.get("USER")
    if not user:
        return f'Windows：请手动限制权限：icacls "{p}" /inheritance:r /grant:r %USERNAME%:F'
    cp = subprocess.run(["icacls", str(p), "/inheritance:r", "/grant:r", f"{user}:F"],
                        capture_output=True, text=True)
    if cp.returncode != 0:
        return (f'Windows：icacls 收紧权限失败，请手动执行 '
                f'icacls "{p}" /inheritance:r /grant:r %USERNAME%:F')
    return None


def write_key(env_path: Path, key_name: str, key: str) -> str | None:
    """写密钥；返回权限提示（正常时 None）。key 绝不回显。"""
    line = f"{key_name}={key}"
    if env_path.is_file():
        lines = env_path.read_text(encoding="utf-8").splitlines()
        out, replaced = [], False
        for ln in lines:
            if re.match(rf"^\s*(export\s+)?{re.escape(key_name)}\s*=", ln):
                out.append(line)
                replaced = True
            else:
                out.append(ln)
        if not replaced:
            out.append(line)
        text = "\n".join(out).rstrip("\n") + "\n"
    else:
        text = f"# podscribe 密钥：不要提交（已在 .gitignore）。\n{line}\n"
    _atomic_write_text(env_path, text)
    return harden_permissions(env_path)


def key_fingerprint(key: str) -> str:
    return f"…{key[-4:]}（长度 {len(key)}）" if len(key) > 8 else "****"


# ── 体检 ─────────────────────────────────────────────────────────────────


def check(cfg: dict, cfg_path: Path, env_path: Path) -> dict:
    asr = cfg.get("asr") or {}
    vault = cfg.get("vault") or {}
    key_name = asr.get("api_key_env") or "SILICONFLOW_API_KEY"

    raw_vault = str(vault.get("path") or "").strip()
    vault_set = bool(raw_vault) and raw_vault not in C.ASR_PLACEHOLDERS
    vault_dir = Path(raw_vault).expanduser() if vault_set else None
    vault_ok = bool(vault_dir and vault_dir.is_dir())

    asr_missing = [k for k in C.REQUIRED_ASR_FIELDS
                   if str(asr.get(k, "")).strip().upper() in C.ASR_PLACEHOLDERS]
    key = C.api_key(key_name)

    ffmpeg, ffprobe = C.tool_path(cfg, "ffmpeg"), C.tool_path(cfg, "ffprobe")
    cache = C.cache_root(cfg)
    # --check 承诺不改任何东西：只测「最近的一个已存在目录」是否可写
    probe = cache
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    cache_writable = os.access(probe, os.W_OK)

    missing = []
    if not vault_ok:
        missing.append("vault_path")
    if asr_missing:
        missing.append("asr_config")
    if not key:
        missing.append("api_key")

    return {
        "needs_setup": bool(missing),
        "missing": missing,
        "next_step": (None if not missing else
                      "在终端运行 `python3 setup.py`（交互式，密钥不回显），"
                      "或让 Agent 依次调用 --vault / --key-stdin / --verify"),
        "vault": {"configured": vault_set, "valid": vault_ok,
                  "value": raw_vault or None, "output_subdir": vault.get("output_subdir")},
        "api_key": {"configured": bool(key), "env_file": str(env_path),
                    "env_var": key_name, "tail": (key[-4:] if len(key) > 8 else None)},
        "asr": {"model": asr.get("cloud_model"), "base_url": asr.get("cloud_base_url"),
                "missing": asr_missing, "allowed_hosts": asr.get("allowed_hosts")},
        "runtime": {
            "platform": sys.platform,
            "python": ".".join(str(x) for x in sys.version_info[:3]),
            "python_ok": sys.version_info >= (3, 11),
            "ffmpeg": ffmpeg, "ffmpeg_ok": bool(ffmpeg and Path(ffmpeg).exists()),
            "ffprobe": ffprobe, "ffprobe_ok": bool(ffprobe and Path(ffprobe).exists()),
            "cache_root": str(cache), "cache_writable": cache_writable,
        },
        "config_file": str(C.resolve_config_path(None)),
        "local_config_file": str(cfg_path),
    }


# ── 校验 ─────────────────────────────────────────────────────────────────


def verify(cfg: dict, timeout: int = 15) -> dict:
    """库可写 + key 有效。只做只读探测：不列库内容、不往库里写东西。"""
    out: dict = {}
    vault = Path(str((cfg.get("vault") or {}).get("path") or "")).expanduser()
    out["vault"] = {"path": str(vault), "exists": vault.is_dir(),
                    "writable": bool(vault.is_dir() and os.access(vault, os.W_OK))}

    asr = cfg.get("asr") or {}
    key_name = asr.get("api_key_env") or "SILICONFLOW_API_KEY"
    key = C.api_key(key_name)
    base = str(asr.get("cloud_base_url") or "").rstrip("/")
    info: dict = {"env_var": key_name, "base_url": base}
    if not key:
        info.update({"checked": False, "ok": None, "reason": "没有密钥"})
    elif not base:
        info.update({"checked": False, "ok": None, "reason": "没填 cloud_base_url"})
    else:
        req = urllib.request.Request(f"{base}/models",
                                     headers={"Authorization": f"Bearer {key}"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                resp.read(4096)
                info.update({"checked": True, "status": resp.status, "ok": True})
        except urllib.error.HTTPError as e:
            info.update({"checked": True, "status": e.code, "ok": False,
                         "reason": "密钥被拒绝" if e.code in (401, 403) else f"HTTP {e.code}"})
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            info.update({"checked": False, "ok": None,
                         "reason": f"网络不通，暂不判断（{type(e).__name__}）"})
    out["api_key"] = info
    out["ok"] = out["vault"]["writable"] and info.get("ok") is not False
    return out


# ── 交互式 ───────────────────────────────────────────────────────────────


def _ask_vault() -> str | None:
    say("\n1/2  Obsidian 库的绝对路径")
    say("     提示：可以把库文件夹拖进终端；Windows 上直接粘贴路径")
    for _ in range(3):
        raw = input().strip().strip('"').strip("'")
        if not raw:
            say("     还没填，再试一次（Ctrl-C 退出）")
            continue
        p = Path(os.path.expandvars(raw)).expanduser()
        if not p.is_dir():
            say(f"     找不到这个目录：{p}")
            continue
        return str(p.resolve())
    return None


def _ask_key() -> str | None:
    say("\n2/2  硅基流动 API Key（输入不回显，粘贴后回车）")
    say("     没有的话去 https://cloud.siliconflow.cn 后台生成一个")
    for _ in range(3):
        try:
            raw = getpass.getpass("").strip().strip('"').strip("'")
        except (EOFError, KeyboardInterrupt):
            return None
        if not raw:
            say("     空值，再试一次（Ctrl-C 退出）")
            continue
        if not raw.startswith("sk-"):
            say("     注意：这个值不是 sk- 开头，确认无误就继续")
        return raw
    return None


def interactive(args, cfg: dict, cfg_path: Path, env_path: Path) -> None:
    if not sys.stdin.isatty():
        raise C.Fail(STAGE, "NOT_A_TTY",
                     "当前不是交互式终端，没法弹输入。请在终端里运行 "
                     "`python3 setup.py`，或用 --vault / --key-stdin 直接传值。",
                     C.EXIT_BAD_INPUT)
    key_name = (cfg.get("asr") or {}).get("api_key_env") or "SILICONFLOW_API_KEY"
    say("== podscribe 首次配置（只问两样）==")
    say(f"   库路径写入：{cfg_path}")
    say(f"   密钥写入：  {env_path}")

    vault = _ask_vault()
    if not vault:
        raise C.Fail(STAGE, "CANCELLED", "没有拿到库路径，已取消", C.EXIT_BAD_INPUT)
    key = _ask_key()
    if not key:
        raise C.Fail(STAGE, "CANCELLED", "没有拿到密钥，已取消", C.EXIT_BAD_INPUT)

    changed = write_vault(cfg_path, vault)
    warn = write_key(env_path, key_name, key)
    say("")
    say(f"[vault].path → {cfg_path}{'（已更新）' if changed else '（内容未变）'}")
    say(f"{key_name} → {env_path}  尾号 {key_fingerprint(key)}")
    if warn:
        say(f"！{warn}")

    warnings = [warn] if warn else []
    result: dict = {"skipped": True}
    if not args.no_verify:
        cfg2 = C.load_config(args.config)          # 重读，拿到刚写的值
        result = verify(cfg2)
        v, k = result["vault"], result["api_key"]
        say("")
        say(f"库目录：{'存在且可写' if v['writable'] else '不可写或不存在'}（{v['path']}）")
        if k.get("checked") and k.get("ok"):
            say("API Key：有效")
        elif k.get("checked"):
            say(f"API Key：{k.get('reason')}——去后台确认，或重跑 setup.py")
            raise C.Fail(STAGE, "API_KEY_INVALID",
                         "密钥校验没通过，请确认后重跑 setup.py", C.EXIT_API)
        else:
            say(f"API Key：未验证（{k.get('reason')}）")

    C.emit(STAGE, artifacts={"local_config": str(cfg_path), "env_file": str(env_path)},
           vault_path=vault, api_key_tail=key_fingerprint(key),
           verify=result, warnings=warnings)


# ── 入口 ─────────────────────────────────────────────────────────────────


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=None, help="默认自动挑 config.local.toml / config.toml")
    ap.add_argument("--local-config", default=None, help="本地配置写入位置（默认 <skill>/config.local.toml）")
    ap.add_argument("--env-file", default=None, help=".env 写入位置（默认 <skill>/.env）")
    ap.add_argument("--vault", default=None, help="只写库路径，不进入交互")
    ap.add_argument("--key-stdin", action="store_true", help="从 stdin 读 API key")
    ap.add_argument("--verify", action="store_true", help="只做校验")
    ap.add_argument("--check", action="store_true", help="只体检，不改文件（退出码恒为 0）")
    ap.add_argument("--no-verify", action="store_true", help="交互式写入后不联网校验")
    args = ap.parse_args()

    cfg_path, env_path = target_local_config(args), target_env_file(args)
    cfg = C.load_config(args.config)

    if args.check:
        C.emit(STAGE, **check(cfg, cfg_path, env_path))
        return

    if args.vault:
        p = Path(os.path.expandvars(args.vault)).expanduser()
        if not p.is_dir():
            raise C.Fail(STAGE, "VAULT_NOT_FOUND",
                         f"这个目录不存在，先确认路径：{p}", C.EXIT_BAD_INPUT)
        changed = write_vault(cfg_path, str(p.resolve()))
        C.emit(STAGE, artifacts={"local_config": str(cfg_path)},
               vault_path=str(p.resolve()), changed=changed)
        return

    if args.key_stdin:
        key = sys.stdin.read().strip().strip('"').strip("'")
        if not key:
            raise C.Fail(STAGE, "EMPTY_KEY", "stdin 里没有读到密钥", C.EXIT_BAD_INPUT)
        key_name = (cfg.get("asr") or {}).get("api_key_env") or "SILICONFLOW_API_KEY"
        warn = write_key(env_path, key_name, key)
        notes = [warn] if warn else []
        if os.environ.get("PODCAST_KEY_FROM_CHAT"):
            notes.append("若这个 key 经过聊天窗口或日志，请在服务商后台轮换一次")
        C.emit(STAGE, artifacts={"env_file": str(env_path)},
               env_var=key_name, api_key_tail=key_fingerprint(key), warnings=notes)
        return

    if args.verify:
        result = verify(cfg)
        v, k = result["vault"], result["api_key"]
        if not v["exists"]:
            raise C.Fail(STAGE, "VAULT_NOT_FOUND",
                         f"库目录不存在或还没配置：{v['path']}", C.EXIT_BAD_CONFIG,
                         verify=result)
        if not v["writable"]:
            raise C.Fail(STAGE, "VAULT_NOT_WRITABLE",
                         f"库目录不可写：{v['path']}", C.EXIT_BAD_CONFIG, verify=result)
        if k.get("checked") and not k.get("ok"):
            raise C.Fail(STAGE, "API_KEY_INVALID",
                         f"密钥校验没通过：{k.get('reason')}", C.EXIT_API, verify=result)
        C.emit(STAGE, **result)
        return

    interactive(args, cfg, cfg_path, env_path)


if __name__ == "__main__":
    sys.exit(C.main_with_job(STAGE, main))
