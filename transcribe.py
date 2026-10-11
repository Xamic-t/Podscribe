#!/usr/bin/env python3
"""转写层：云端（默认，带说话人分离 + 句级时间戳）或本地 faster-whisper。

用法：
  transcribe.py --job-dir <dir> --config <config.toml> [--engine cloud|local] [--fast] [--model <id>]

云端：按 asr.chunk_minutes（默认 20 分钟）切大块，相邻块重叠 30 秒，
      上传后按块起点还原绝对时间，并用重叠区的文本匹配把各块局部的
      speaker id 对齐成全局「说话人 A / B」。
本地：整文件交给 faster-whisper（VAD 自行处理，不再手工切块）。
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
import urllib.error
import urllib.request
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import core as C  # noqa: E402

STAGE = "transcribe"
LETTERS = "ABCDEFGHIJKLMNOP"


def mp3_cut(ffmpeg: str, src: Path, start: float, end: float, out: Path) -> None:
    if out.exists():
        out.unlink()
    C.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
           "-ss", f"{start:.3f}", "-i", str(src), "-t", f"{max(1.0, end - start):.3f}",
           "-vn", "-ac", "1", "-ar", "16000", "-c:a", "libmp3lame", "-b:a", "64k", str(out)],
          timeout=1800)


def post_audio(url: str, key: str, model: str, path: Path, timeout: int,
               hotword: str | None) -> dict:
    def build(with_hotword: bool) -> bytes:
        boundary = "----podcast" + uuid.uuid4().hex
        body = bytearray()
        body += f"--{boundary}\r\n".encode()
        body += b'Content-Disposition: form-data; name="file"; filename="chunk.mp3"\r\n'
        body += b"Content-Type: audio/mpeg\r\n\r\n"
        body += path.read_bytes() + b"\r\n"
        body += f"--{boundary}\r\n".encode()
        body += b'Content-Disposition: form-data; name="model"\r\n\r\n'
        body += model.encode() + b"\r\n"
        if with_hotword and hotword:
            body += f"--{boundary}\r\n".encode()
            body += b'Content-Disposition: form-data; name="hotword"\r\n\r\n'
            body += hotword.encode() + b"\r\n"
        body += f"--{boundary}--\r\n".encode()
        return bytes(body)

    def call(data: bytes) -> dict:
        req = urllib.request.Request(
            url, data=data, method="POST",
            headers={"Content-Type": f'multipart/form-data; boundary={data.split(b"\r\n")[0][2:].decode()}',
                     "Authorization": f"Bearer {key}"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))

    try:
        return call(build(hotword is not None))
    except urllib.error.HTTPError as e:
        if e.code in (400, 413, 422) and hotword:  # 该模型不接受 hotword，退回不带热词
            return call(build(False))
        raise


def cloud_transcribe(cfg: dict, meta: dict, jd: Path, model: str) -> tuple[list[dict], list[str]]:
    asr = cfg["asr"]
    env_name = asr.get("api_key_env") or "SILICONFLOW_API_KEY"
    key = C.api_key(env_name)
    if not key:
        env_file = C.env_file_path()
        raise C.Fail(STAGE, "API_KEY_MISSING",
                     f"没找到 {env_name}。请把它写进 env 文件"
                     f"（{env_file or '~/.config/podscribe/env'}，权限 600）"
                     f"，或用 export {env_name}=... 注入。不要写进 config.toml。",
                     C.EXIT_API)
    base_url = asr["cloud_base_url"].rstrip("/")
    # P4：密钥只允许发往白名单主机，配置被改也发不出去
    import urllib.parse as _up
    host = (_up.urlsplit(base_url).hostname or "").lower()
    allowed = [h.lower() for h in (asr.get("allowed_hosts") or ["api.siliconflow.cn"])]
    if host not in allowed:
        raise C.Fail(STAGE, "HOST_NOT_ALLOWED",
                     f"cloud_base_url 指向未授权主机：{host}（允许：{'、'.join(allowed)}）",
                     C.EXIT_PRIVACY)
    url = f"{base_url}/audio/transcriptions"
    target = float(asr.get("chunk_minutes", 20)) * 60
    overlap = float(asr.get("overlap_seconds", 30))
    timeout = int(asr.get("timeout_seconds", 600))
    concurrency = int(asr.get("concurrency", 4))
    ffprobe, ffmpeg = C.tool_path(cfg, "ffprobe"), C.tool_path(cfg, "ffmpeg")
    audio = Path(meta["audio"]["path"])
    duration = float(meta["duration_seconds"] or C.ffprobe_duration(ffprobe, audio))

    # P16：热词真正生效——云端随请求提交（接口不支持时 post_audio 会自动退回）
    hotwords = C.read_hotwords(C.SKILL_ROOT / "hotwords.txt")
    hotword = " ".join(hotwords[:50]) if hotwords and asr.get("send_hotword", True) else None
    if hotwords:
        C.log(f"已载入 {len(hotwords)} 个热词")

    pieces = jd / "pieces" / time.strftime("%Y%m%d-%H%M%S")
    pieces.mkdir(parents=True, exist_ok=True)
    jobs = []
    start_i = 0.0
    idx = 0
    truncated = False
    while start_i < duration - 1:
        media_start = max(0.0, start_i - (overlap if idx > 0 else 0.0))
        media_end = min(duration, start_i + target)
        out = pieces / f"p{idx:03d}.mp3"
        mp3_cut(ffmpeg, audio, media_start, media_end, out)
        jobs.append({"index": idx, "media_start": media_start, "logical_start": start_i,
                     "logical_end": media_end, "path": out})
        idx += 1
        start_i = media_end
        if idx > 200:  # P19：超长音频不再静默截断
            truncated = True
            break

    C.log(f"切成 {len(jobs)} 块（每块 {target/60:.0f} 分钟，重叠 {overlap:.0f}s）")
    results: dict[int, dict] = {}
    notes: list[str] = []
    if truncated:
        notes.append("音频过长（超过 200 块），只处理了前 200 块，后续内容缺失。")
    t0 = time.time()

    raw_dir = jd / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)

    def work(job):
        cached = raw_dir / f"raw-{job['index']:03d}.json"
        if cached.exists():  # 断点续跑：已上传过的块不重复计费
            try:
                return job["index"], json.loads(cached.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                pass
        for attempt in range(3):
            try:
                data = post_audio(url, key, model, job["path"], timeout, hotword)
                C.write_json(cached, data)
                return job["index"], data
            except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError,
                    json.JSONDecodeError, OSError) as e:
                if attempt == 2:
                    return job["index"], {"__error__": str(e)}
                time.sleep(2 * (attempt + 1))

    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        futs = [pool.submit(work, j) for j in jobs]
        done = 0
        for fut in as_completed(futs):
            i, data = fut.result()
            done += 1
            if "__error__" in data:
                notes.append(f"[块失败] 第 {i} 块：{data['__error__'][:80]}")
            else:
                results[i] = data
            C.log(f"进度 {done}/{len(jobs)}，用时 {time.time() - t0:.0f}s")

    # 组装：重叠区归属前一块，避免重复
    segments: list[dict] = []
    for job in jobs:
        data = results.get(job["index"])
        if not data:
            continue
        raw = data.get("segments") or []
        if not raw:
            text = (data.get("text") or "").strip()
            raw = [{"start": 0.0, "end": job["logical_end"] - job["media_start"],
                    "text": text, "speaker": "1"}] if text else []
        for seg in raw:
            try:
                s = float(seg.get("start", 0.0)) + job["media_start"]
                e = float(seg.get("end", 0.0)) + job["media_start"]
            except (TypeError, ValueError):
                continue
            text = (seg.get("text") or "").strip()
            if not text:
                continue
            spk = str(seg.get("speaker") or "1")
            segments.append({"start": round(s, 2), "end": round(e, 2), "text": text,
                             "speaker": spk, "chunk": job["index"]})

    # 1) 说话人全局对齐：比较重叠区里「同一段音频」在相邻两块中的说话人
    global_of: dict[tuple[int, str], str] = {}
    label_order: list[str] = []
    next_id = 0

    def new_label() -> str:
        nonlocal next_id
        label = LETTERS[next_id] if next_id < len(LETTERS) else f"S{next_id}"
        next_id += 1
        label_order.append(label)
        return label

    for job in jobs:
        i = job["index"]
        boundary = job["logical_start"]
        spks = sorted({s["speaker"] for s in segments if s["chunk"] == i})
        if i == 0:
            for spk in spks:
                global_of[(0, spk)] = new_label()
            continue
        # 用「同一段音频在时间上的重叠」对齐说话人，比文本匹配稳
        prev_segs = [s for s in segments if s["chunk"] == i - 1]
        cur_segs = [s for s in segments if s["chunk"] == i and s["start"] < boundary + 0.5]
        prev_spks = sorted({s["speaker"] for s in prev_segs})
        mapping: dict[str, str] = {}
        for x in spks:
            xs = [s for s in cur_segs if s["speaker"] == x]
            best_y, best_overlap = None, 0.0
            for y in prev_spks:
                ys = [s for s in prev_segs if s["speaker"] == y]
                total = 0.0
                for a in xs:
                    for b in ys:
                        total += max(0.0, min(a["end"], b["end"]) - max(a["start"], b["start"]))
                if total > best_overlap:
                    best_y, best_overlap = y, total
            if best_y is not None and best_overlap >= 1.0:
                mapped = global_of.get((i - 1, best_y))
                if mapped:
                    mapping[x] = mapped
        # 同一块内的不同说话人必须映射到不同的全局标签；
        # 没有被重叠证据覆盖的那个，优先复用已有的标签（通常是少数派/主持人）
        used = set(mapping.values())
        for spk in spks:
            if spk in mapping:
                global_of[(i, spk)] = mapping[spk]
                continue
            free = next((g for g in label_order if g not in used), None)
            label = free if free else new_label()
            global_of[(i, spk)] = label
            used.add(label)

    # 2) 去掉重叠区的重复：块 i 里起点早于边界的句子，若能在块 i-1 找到相似句就丢弃
    dropped = 0
    deduped = []
    for job in jobs:
        i = job["index"]
        boundary = job["logical_start"]
        if i == 0:
            deduped += [s for s in segments if s["chunk"] == 0]
            continue
        prev = [s for s in segments if s["chunk"] == i - 1 and s["end"] > boundary - overlap - 2]
        for s in segments:
            if s["chunk"] != i:
                continue
            if s["start"] < boundary - 0.5 and prev and any(
                    C.similarity(s["text"], p["text"]) >= 0.55 for p in prev):
                dropped += 1
                continue
            deduped.append(s)
    if dropped:
        notes.append(f"重叠区去重 {dropped} 句。")

    deduped.sort(key=lambda s: (s["start"], s["end"]))

    # 3) 兜底去重：起点几乎相同且文本高度相似的相邻句只留一条
    final: list[dict] = []
    for seg in deduped:
        if final and abs(seg["start"] - final[-1]["start"]) < 1.5 \
                and C.similarity(seg["text"], final[-1]["text"]) >= 0.85:
            dropped += 1
            continue
        final.append(seg)

    for seg in final:
        seg["speaker"] = "说话人 " + global_of.get((seg["chunk"], seg["speaker"]), "A")

    # R6：成功路径清理本次分片；失败时函数会抛异常，分片保留便于排查
    if pieces.exists():
        shutil.rmtree(pieces, ignore_errors=True)
        C.log("已清理本次转写分片（raw/ 缓存保留，重转不会重复计费）")
    return final, notes


def local_transcribe(cfg: dict, meta: dict, jd: Path, model: str) -> tuple[list[dict], list[str]]:
    from faster_whisper import WhisperModel  # type: ignore

    asr = cfg["asr"]
    audio = Path(meta["audio"]["path"])
    # P16：本地引擎把热词拼成 initial_prompt；不用 hotwords 参数
    #（它会强制关闭 condition_on_previous_text，中文长音频连贯性会变差）
    hotwords = C.read_hotwords(C.SKILL_ROOT / "hotwords.txt")
    prompt = None
    if hotwords:
        prompt = (asr.get("initial_prompt_prefix", "")
                  or "以下是中文播客，可能涉及以下专有名词：") + "、".join(hotwords[:80])
        C.log(f"本地模式已载入 {len(hotwords)} 个热词作为 initial_prompt")
    m = WhisperModel(model, device="cpu", compute_type="int8")
    segs, _info = m.transcribe(str(audio), language=asr.get("language", "zh"),
                               task="transcribe", beam_size=5,
                               condition_on_previous_text=True,
                               initial_prompt=prompt, hotwords=None,
                               word_timestamps=False, vad_filter=True,
                               vad_parameters={"min_silence_duration_ms": 500})
    out = []
    for s in segs:
        text = (s.text or "").strip()
        if not text:
            continue
        out.append({"start": round(float(s.start), 2), "end": round(float(s.end), 2),
                    "text": text, "speaker": "说话人 A", "chunk": 0})
    return out, ["本地引擎不做说话人分离，全部标记为「说话人 A」。"]


def write_vtt(segments: list[dict], path: Path) -> None:
    lines = ["WEBVTT", ""]
    for i, s in enumerate(segments, 1):
        lines += [str(i), f"{C.fmt_vtt(s['start'])} --> {C.fmt_vtt(s['end'])}",
                  f"{s['speaker']}：{s['text']}", ""]
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--job-dir", required=True)
    ap.add_argument("--config", default=None,
                    help="配置文件；默认自动挑 config.local.toml / config.toml")
    ap.add_argument("--engine", default=None, choices=["cloud", "local"])
    ap.add_argument("--fast", action="store_true")
    ap.add_argument("--model", default=None)
    ap.add_argument("--no-reexec", action="store_true")
    args = ap.parse_args()

    cfg = C.load_config(args.config)
    engine = args.engine or cfg["asr"].get("engine", "cloud")
    jd = C.resolve_job_dir(cfg, args.job_dir)   # R7：统一校验 job 目录
    meta = C.read_json(jd / "metadata.json")

    if engine == "local" and not args.no_reexec:
        try:
            import faster_whisper  # noqa: F401
        except ImportError:
            raw_venv = str(cfg["runtime"].get("python") or "").strip()  # 支持 ~ 开头
            venv_py = Path(raw_venv).expanduser()
            if raw_venv and venv_py.is_file():                      # 留空则用当前 python
                os.execv(venv_py, [venv_py, str(Path(__file__).resolve()),
                                   "--job-dir", str(jd), "--config", args.config,
                                   "--engine", "local", "--no-reexec"]
                         + (["--fast"] if args.fast else [])
                         + (["--model", args.model] if args.model else []))

    if engine == "local":
        model = args.model or (cfg["asr"]["local_fast_model"] if args.fast
                               else cfg["asr"]["local_model"])
        segments, warns = local_transcribe(cfg, meta, jd, model)
        engine_name = f"local:{model}"
    else:
        C.require_asr_config(cfg)   # 模型和接口地址必须自己填过，不能拿默认值去打请求
        model = args.model or cfg["asr"]["cloud_model"]
        segments, warns = cloud_transcribe(cfg, meta, jd, model)
        engine_name = f"cloud:{model}"

    segments.sort(key=lambda s: (s["start"], s["end"]))
    speakers = sorted({s["speaker"] for s in segments})
    transcript = {
        "schema_version": C.SCHEMA_VERSION,
        "job_id": jd.name,
        "engine": engine,
        "model": engine_name,
        "duration": meta.get("duration_seconds"),
        "speakers": speakers,
        "segment_count": len(segments),
        "word_count": sum(len(s["text"]) for s in segments),
        "segments": segments,
    }
    C.write_json(jd / "transcript.json", transcript)
    write_vtt(segments, jd / "transcript.vtt")

    # P6：转写完成后处理下载的音频——默认归档到 <缓存>/音频库/，keep_audio=false 时删除。
    # 只动 owned_by_job=true 的文件，用户自己的本地音频永不处理。
    audio_note = None
    info = meta.get("audio") or {}
    if info.get("owned_by_job") and info.get("path"):
        src = Path(info["path"])
        if src.is_file():
            if cfg["retention"].get("keep_audio", True):
                arch = Path(cfg["retention"].get("audio_archive_dir", "音频库"))
                arch = arch if arch.is_absolute() else C.cache_root(cfg) / arch
                arch.mkdir(parents=True, exist_ok=True)
                dest = arch / f"{jd.name}.{info.get('extension') or src.suffix.lstrip('.') or 'm4a'}"
                shutil.move(str(src), str(dest))
                audio_note = f"音频已归档到 {dest}"
                meta["audio"]["path"] = str(dest)
                meta["audio"]["archived"] = True
                C.write_json(jd / "metadata.json", meta)
            else:
                src.unlink()
                audio_note = "已按 keep_audio=false 删除下载的音频"
                meta["audio"]["path"] = None
                meta["audio"]["archived"] = False
                C.write_json(jd / "metadata.json", meta)
    elif not info.get("owned_by_job"):
        audio_note = "用户提供的本地音频未做任何改动"
    if audio_note:
        C.log(audio_note)
        warns = list(warns) + [audio_note]

    C.update_job(jd / "job.json", {"stages": {"transcribe": {"status": "done",
                                                             "at": C.iso_now(),
                                                             "engine": engine_name}}})
    C.emit(STAGE, job_id=jd.name,
           artifacts={"transcript": str(jd / "transcript.json"),
                      "vtt": str(jd / "transcript.vtt")},
           warnings=warns, engine=engine_name, segments=len(segments),
           speakers=speakers, word_count=transcript["word_count"])


if __name__ == "__main__":
    sys.exit(C.main_with_job(STAGE, main))
