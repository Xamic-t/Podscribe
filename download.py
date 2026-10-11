#!/usr/bin/env python3
"""下载层：喜马拉雅 / 小宇宙 / 通用 RSS / 裸音频直链 / 本地文件。

用法：
  download.py --input <URL 或本地音频路径> --config <config.toml> [--job-id <id>]
产出：<job_dir>/metadata.json + <job_dir>/audio/source.<ext>
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import core as C  # noqa: E402

STAGE = "download"
IT = "{http://www.itunes.com/dtds/podcast-1.0.dtd}"
AUDIO_EXT = (".m4a", ".mp3", ".aac", ".wav", ".flac", ".ogg", ".mp4", ".m4b")
_SANE_ID = re.compile(r"^[0-9A-Za-z][0-9A-Za-z_-]{0,63}$")


# ---------------------------------------------------------------- 来源识别


def _host_of(raw: str) -> str:
    return (urllib.parse.urlsplit(raw).hostname or "").lower()


def _is_host(host: str, domain: str) -> bool:
    """P11：严格域名匹配，避免 xiaoyuzhoufm.com.evil.com 被误判。"""
    return host == domain or host.endswith("." + domain)


def kind_of(raw: str) -> str:
    if re.match(r"^https?://", raw, re.I):
        host = _host_of(raw)
        path = urllib.parse.urlsplit(raw).path or "/"
        if _is_host(host, "xiaoyuzhoufm.com"):
            return "xiaoyuzhou"
        if _is_host(host, "ximalaya.com"):
            return "ximalaya" if "/sound/" in path else "ximalaya-unsupported"
        if _is_host(host, "xima.tv"):
            return "ximalaya-short"
        clean = path.lower().split("?")[0]
        if clean.endswith(AUDIO_EXT):
            return "direct"
        if clean.endswith((".xml", ".rss")) or "feed" in clean or "rss" in clean:
            return "rss"
        return "unknown"
    p = Path(raw).expanduser()
    return "local" if p.is_file() else "unknown"


def curl_cmd(url: str, out: Path, referer: str | None = None) -> list[str]:
    """P2：URL 前加 `--`，以 `-` 开头的远端 URL 不会被 curl 当成选项。"""
    cmd = ["curl", "-fL", "--retry", "3", "--retry-all-errors", "--max-time", "3600",
           "-H", f"User-Agent: {C.UA}"]
    if referer:
        cmd += ["-H", f"Referer: {referer}"]
    cmd += ["-o", str(out), "--", C.check_url(url)]
    return cmd


def expand_short(url: str) -> str:
    # os.devnull：POSIX 是 /dev/null，Windows 是 nul（写 /dev/null 在 Windows 上会生成文件）
    cp = C.run(["curl", "-sSL", "-o", os.devnull, "-w", "%{url_effective}",
                "--max-time", "20", "-H", f"User-Agent: {C.UA}", "--", C.check_url(url)],
               timeout=30, check=False)
    final = cp.stdout.decode("utf-8", "replace").strip()
    if not final:
        raise C.Fail(STAGE, "SHORT_URL_FAILED", "短链无法展开", C.EXIT_NETWORK)
    return final


# ---------------------------------------------------------------- 各平台解析


def parse_ximalaya(raw: str, cfg: dict) -> tuple[str, dict]:
    url = expand_short(raw) if kind_of(raw) == "ximalaya-short" else raw
    tid = None
    for pattern in (r"ximalaya\.com/sound/(\d+)",):
        m = re.search(pattern, url)
        if m:
            tid = m.group(1)
    if not tid:
        if re.fullmatch(r"\d{4,}", raw.strip()):
            tid = raw.strip()
        else:
            raise C.Fail(STAGE, "NOT_SINGLE_TRACK", "只支持单集链接 /sound/<数字>", C.EXIT_BAD_INPUT)
    job_id = f"xm-{tid}"
    referer = f"https://www.ximalaya.com/sound/{tid}"
    base = C.http_json(f"https://m.ximalaya.com/tracks/{tid}.json", referer=referer)
    if not base.get("id"):
        raise C.Fail(STAGE, "TRACK_NOT_FOUND", f"取不到单集（{tid}）", C.EXIT_NETWORK)
    if base.get("is_paid"):
        raise C.Fail(STAGE, "PAID_EPISODE",
                     "付费/VIP 内容，不绕过鉴权。可自行下载音频后把文件路径给我走本地旁路。",
                     C.EXIT_PAID)
    audio = next((base.get(k) for k in ("play_path_64", "play_path_32", "play_path")
                  if isinstance(base.get(k), str) and base[k].startswith("http")), None)
    if not audio:
        raise C.Fail(STAGE, "NO_FREE_SOURCE", "没有可用的免费直链", C.EXIT_PAID)

    meta = {"source_type": "ximalaya", "track_id": tid, "source_url": referer,
            "title": base.get("title") or f"单集 {tid}",
            "album": base.get("album_title"), "creator": base.get("nickname"),
            "published": None, "duration_seconds": base.get("duration"),
            "cover_url": base.get("cover_url"), "shownotes": C.html_to_text(base.get("intro"))}
    try:
        extra = C.http_json(f"https://mobile.ximalaya.com/mobile/v1/track/baseInfo/"
                            f"{int(datetime.now().timestamp() * 1000)}?device=pc&trackId={tid}",
                            referer=referer)
        meta["published"] = C.ms_to_date(extra.get("createdAt"))
        meta["album"] = extra.get("albumTitle") or meta["album"]
        meta["title"] = extra.get("title") or meta["title"]
        meta["duration_seconds"] = extra.get("duration") or meta["duration_seconds"]
    except C.Fail:
        pass
    try:
        simple = C.http_json(f"https://www.ximalaya.com/revision/track/simple?trackId={tid}",
                             referer=referer)
        notes = C.html_to_text((simple.get("data") or {}).get("trackInfo", {}).get("richIntro"))
        meta["shownotes"] = notes or meta["shownotes"]
    except C.Fail:
        pass
    return job_id, {"audio_url": audio, "referer": referer, **meta}


def parse_xiaoyuzhou(raw: str, cfg: dict) -> tuple[str, dict]:
    html_text = C.http_bytes(raw).decode("utf-8", "replace")
    m = re.search(r'<meta property="og:audio" content="([^"]+)"', html_text)
    eid = None
    m_id = re.search(r"xiaoyuzhoufm\.com/episode/([0-9a-fA-F]+)", raw)
    if m_id:
        eid = m_id.group(1)
    data = {}
    m_next = re.search(r'id="__NEXT_DATA__"[^>]*>(.*?)</script>', html_text, re.S)
    if m_next:
        try:
            data = json.loads(m_next.group(1))["props"]["pageProps"].get("episode") or {}
        except (json.JSONDecodeError, KeyError):
            data = {}
    audio = (m.group(1) if m else None) or (data.get("enclosure") or {}).get("url")
    if not audio:
        raise C.Fail(STAGE, "NO_AUDIO", "小宇宙页面里没找到音频直链（可能是付费或私密单集）",
                     C.EXIT_PAID)
    if data.get("payType") not in (None, "FREE") or data.get("isPrivateMedia"):
        raise C.Fail(STAGE, "PAID_EPISODE", "该小宇宙单集为付费/私密内容", C.EXIT_PAID)
    podcast = data.get("podcast") or {}
    hosts = [p.get("nickname") for p in (podcast.get("podcasters") or []) if p.get("nickname")]
    pub = data.get("pubDate")
    published = pub[:10] if isinstance(pub, str) and len(pub) >= 10 else None
    # P1：eid 来自远端页面，必须清洗后才能当目录名
    eid = str(eid or data.get("eid") or "")
    if not _SANE_ID.match(eid):
        eid = "u" + hashlib.sha256(raw.encode()).hexdigest()[:12]
    return f"xyz-{eid}", {
        "audio_url": audio, "source_type": "xiaoyuzhou", "track_id": eid, "source_url": raw,
        "title": data.get("title") or (re.search(r'og:title" content="([^"]+)"', html_text)
                                       .group(1) if re.search(r'og:title" content="([^"]+)"',
                                                              html_text) else f"单集 {eid}"),
        "album": podcast.get("title"), "creator": "、".join(hosts) if hosts else None,
        "published": published, "duration_seconds": data.get("duration"),
        "cover_url": (podcast.get("image") or {}).get("picture", {}).get("picUrl"),
        "shownotes": C.html_to_text(data.get("shownotes") or data.get("description")),
    }


def _rss_items(url: str) -> tuple[str, str | None, list[dict]]:
    raw = C.http_bytes(url, timeout=60).decode("utf-8", "replace")
    # P10 + R4：全量扫描（只查前 2000 字符能被“长注释 + DOCTYPE”绕过）
    low = raw.lower()
    if "<!doctype" in low or "<!entity" in low:
        raise C.Fail(STAGE, "UNSAFE_XML", "RSS 含 DOCTYPE / ENTITY，已拒绝解析",
                     C.EXIT_BAD_INPUT)
    root = ET.fromstring(raw)
    channel = root.find("channel")
    if channel is None:
        raise C.Fail(STAGE, "BAD_RSS", "RSS 里没有 channel", C.EXIT_BAD_INPUT)
    title = channel.findtext("title")
    author = channel.findtext(IT + "author")
    items = []
    for it in channel.findall("item"):
        enc = it.find("enclosure")
        items.append({
            "audio": enc.get("url") if enc is not None else None,
            "title": it.findtext("title"), "guid": it.findtext("guid"),
            "link": it.findtext("link"), "pub": it.findtext("pubDate"),
            "dur": it.findtext(IT + "duration"),
            "desc": C.html_to_text(it.findtext("description")),
        })
    return title or "", author, items


def _parse_duration(raw: str | None) -> int | None:
    if not raw:
        return None
    raw = raw.strip()
    if raw.isdigit():
        return int(raw)
    parts = [int(x) for x in re.findall(r"\d+", raw)]
    if len(parts) == 3:
        return parts[0] * 3600 + parts[1] * 60 + parts[2]
    if len(parts) == 2:
        return parts[0] * 60 + parts[1]
    return None


def parse_rss(raw: str, cfg: dict, key: str | None) -> tuple[str, dict]:
    show, author, items = _rss_items(raw)
    if not items:
        raise C.Fail(STAGE, "BAD_RSS", "RSS 里没有 item", C.EXIT_BAD_INPUT)
    pick = items[0]
    if key:
        hit = next((x for x in items if key in (x["guid"] or "") or key in (x["link"] or "")), None)
        pick = hit or pick
    if not pick.get("audio"):
        raise C.Fail(STAGE, "NO_AUDIO", "该 RSS item 没有 enclosure", C.EXIT_BAD_INPUT)
    gid = pick.get("guid") or (pick.get("link") or "").rsplit("/", 1)[-1] or "rss"
    return f"rss-{re.sub(r'[^0-9A-Za-z_-]', '', gid)[:24]}", {
        "audio_url": pick["audio"], "source_type": "rss", "track_id": gid, "source_url": raw,
        "title": pick.get("title") or "未命名单集", "album": show,
        "creator": author, "published": (pick.get("pub") or "")[:10] or None,
        "duration_seconds": _parse_duration(pick.get("dur")),
        "cover_url": None, "shownotes": pick.get("desc") or "",
    }


def parse_direct(raw: str, cfg: dict) -> tuple[str, dict]:
    gid = hashlib.sha256(raw.encode()).hexdigest()[:10]
    return f"direct-{gid}", {
        "audio_url": raw, "source_type": "direct", "track_id": gid, "source_url": raw,
        "title": raw.rsplit("/", 1)[-1].split("?")[0], "album": None, "creator": None,
        "published": None, "duration_seconds": None, "cover_url": None, "shownotes": "",
    }


def parse_local(raw: str, cfg: dict) -> tuple[str, dict]:
    p = Path(raw).expanduser().resolve()
    st = p.stat()
    gid = hashlib.sha256(f"{p}|{st.st_size}|{st.st_mtime_ns}".encode()).hexdigest()[:12]
    return f"local-{gid}", {
        "audio_url": None, "local_path": str(p), "source_type": "local_audio", "track_id": gid,
        "source_url": None, "title": p.stem, "album": None, "creator": None,
        "published": None, "duration_seconds": None, "cover_url": None, "shownotes": "",
    }


# ---------------------------------------------------------------- 下载


def download(url: str, target: Path, referer: str | None = None) -> int:
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".part")
    if tmp.exists():
        tmp.unlink()
    cp = C.run(curl_cmd(url, tmp, referer), timeout=3700, check=False)
    if cp.returncode != 0 or not tmp.exists() or tmp.stat().st_size < 1024:
        _silent(tmp)
        raise C.Fail(STAGE, "DOWNLOAD_FAILED",
                     f"下载失败（curl {cp.returncode}）", C.EXIT_NETWORK)
    size = tmp.stat().st_size
    shutil.move(str(tmp), str(target))
    return size


def _silent(p: Path) -> None:
    try:
        p.unlink()
    except OSError:
        pass


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True)
    ap.add_argument("--config", default=None,
                    help="配置文件；默认自动挑 config.local.toml / config.toml")
    ap.add_argument("--rss-key", default=None, help="RSS 里要挑的单集 guid / 链接片段")
    ap.add_argument("--job-id", default=None)
    args = ap.parse_args()

    cfg = C.load_config(args.config)
    raw = args.input.strip().strip('"').strip("'")
    kind = kind_of(raw)
    warnings: list[str] = []
    local_path = None

    if kind == "local":
        job_id, meta = parse_local(raw, cfg)
        local_path = meta["local_path"]
    elif kind == "ximalaya" or kind == "ximalaya-short":
        job_id, meta = parse_ximalaya(raw, cfg)
    elif kind == "xiaoyuzhou":
        job_id, meta = parse_xiaoyuzhou(raw, cfg)
    elif kind == "direct":
        job_id, meta = parse_direct(raw, cfg)
    elif kind == "rss":
        job_id, meta = parse_rss(raw, cfg, args.rss_key)
        warnings.append("RSS 来源：默认取最新一集，可用 --rss-key 指定单集。")
    elif kind == "ximalaya-unsupported":
        raise C.Fail(STAGE, "NOT_SINGLE_TRACK", "喜马拉雅只支持单集链接", C.EXIT_BAD_INPUT)
    else:
        raise C.Fail(STAGE, "INPUT_NOT_FOUND", "既不是链接也找不到该文件", C.EXIT_BAD_INPUT)

    jd = C.job_dir(cfg, args.job_id or job_id)
    jd.mkdir(parents=True, exist_ok=True)
    ffprobe = C.tool_path(cfg, "ffprobe")

    if local_path:
        audio = Path(local_path)
        size = audio.stat().st_size
    else:
        target = jd / "audio" / "source.audio"
        existing = sorted((jd / "audio").glob("source.*")) if (jd / "audio").exists() else []
        if existing:
            audio, size = existing[0], existing[0].stat().st_size
            warnings.append("复用缓存中已下载的音频。")
        else:
            size = download(meta["audio_url"], target, meta.get("referer"))
            ext = C.ffprobe_container(ffprobe, target)
            audio = jd / "audio" / f"source.{ext}"
            if audio.exists():
                audio.unlink()
            target.rename(audio)

    duration = meta.get("duration_seconds") or C.ffprobe_duration(ffprobe, audio)
    record = {
        "schema_version": C.SCHEMA_VERSION,
        "job_id": jd.name,
        "source_type": meta["source_type"],
        "track_id": meta["track_id"],
        "source_url": meta["source_url"],
        "title": meta["title"], "album": meta["album"], "creator": meta["creator"],
        "published": meta["published"], "duration_seconds": duration,
        "cover_url": meta["cover_url"], "shownotes": meta["shownotes"],
        "audio": {"path": str(audio), "extension": audio.suffix.lstrip("."),
                  "size": size, "owned_by_job": local_path is None},
    }
    C.write_json(jd / "metadata.json", record)
    C.update_job(jd / "job.json", {"job_id": jd.name, "created_at": C.iso_now(),
                                   "stages": {"download": {"status": "done",
                                                           "at": C.iso_now()}}})
    C.emit(STAGE, job_id=jd.name,
           artifacts={"metadata": str(jd / "metadata.json"), "audio": str(audio)},
           warnings=warnings, source_kind=meta["source_type"], title=meta["title"],
           album=meta["album"], duration=C.fmt_duration(duration))


if __name__ == "__main__":
    sys.exit(C.main_with_job(STAGE, main))
