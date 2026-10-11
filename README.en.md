# podscribe

English · [简体中文](README.md)

[![GitHub stars](https://img.shields.io/github/stars/Xamic-t/Podscribe?style=social)](https://github.com/Xamic-t/Podscribe)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

Give it one podcast episode link and get back a traceable Obsidian note: a TL;DR, layered key points (each anchored to a position like `01:23:45`), verbatim-checked quotes, action items, a chapter timeline — plus the full transcript and a WebVTT subtitle file.

## The whole flow: from link to note

**Link → download → transcribe → summarize → note.** Four steps, each written to disk, so a re-run skips whatever already finished.

| Step | Where it runs | What it does | Cost |
|---|---|---|---|
| 1. Download<br>`download.py` | Local | Detects the source: Ximalaya / Xiaoyuzhou / RSS / direct audio link / local file, then fetches the audio plus title, host, duration and show notes. **Paid episodes stop here** (exit code 5); no auth is bypassed | Free |
| 2. Transcribe<br>`transcribe.py` | Cloud<br>(SiliconFlow) | **The only step that needs the network.** 20-minute chunks with 30 s overlap return **sentence-level timestamps and Speaker A/B** in one pass; default model `XingChenAGI/XingChenASR-Diarize-V3.0`. Add `--engine local` to run slower, fully offline faster-whisper instead | Currently free |
| 3. Summarize<br>your agent | Your session | **No chat model is called anywhere in the code.** The agent you are already talking to reads the transcript, writes each block, then assembles and verifies the quotes; episodes over two hours can be split across subagents | Session budget only |
| 4. Publish<br>`publish.py` | Local | Renders the template and writes into the vault atomically. `create_only` by default: stops if the target exists (exit code 11), **never overwrites and never reads your existing notes** | Free |

**Money and privacy, up front:**

- **Only step 2 goes online.** The audio is uploaded to SiliconFlow (which model, which hostname, comes from your `config.toml`, backed by an `allowed_hosts` allow-list in code). The XingChenASR family is currently listed as **free**.
- **Step 3 costs nothing extra.** Reading and writing happen inside the session you already have.
- **Steps 1 and 4 are entirely local.** Downloading and writing touch no third party.
- **Your vault is left alone.** It never scans the disk for a vault, never reads existing notes, and only creates exact paths derived from `config.toml`.

## Quick start

### What you need

| Requirement | Notes |
|---|---|
| Python 3.11+ | Standard library only (`tomllib`); no third-party packages |
| `ffmpeg` / `ffprobe` | Splitting long audio, probing duration |
| `curl` | Downloading audio (ships with macOS and Windows 10+) |
| A SiliconFlow API key | Free models still need a key; skip it entirely with `--engine local` |

### Step 1: install

```bash
git clone https://github.com/Xamic-t/Podscribe.git podscribe && cd podscribe

./install.sh                        # Claude Code + Codex, user level
./install.sh --harness codex        # Codex only
./install.sh --harness claude       # Claude Code only
./install.sh --project              # into the current project, can be committed
./install.sh --dir /some/where      # custom directory
```

Skip this if you only use the CLI. Existing `config.toml`, `config.local.toml` and `.env` are never overwritten, so it is safe to re-run.

### Step 2: configure once (two questions)

```bash
python3 setup.py          # (1) Obsidian vault path  (2) SiliconFlow API key (hidden input)
```

Prefer a non-interactive flow (for example when an agent does it for you)?

```bash
python3 setup.py --vault "/absolute/path/to/your/obsidian/vault"
echo "sk-..." | python3 setup.py --key-stdin    # do not paste keys into a chat window
python3 setup.py --verify                       # vault writable? key valid?
```

- Private values land in `config.local.toml` (vault path) and `.env` (API key; mode 600 on POSIX, `icacls` on Windows). Both are in `.gitignore`, so **`config.toml` stays a clean template**.
- `config.local.toml` is layered on top of `config.toml`, so you no longer need `--config` (set `$PODCAST_CONFIG` to point somewhere else).
- `python3 setup.py --check` reports what is still missing: **read-only**, always exits 0.
- `--vault` only checks that the directory exists. It never lists your vault or reads any note.

### Step 3: run

```bash
python3 run.py --input "<episode-url>"
```

You can also just tell your agent "summarize this podcast <link>" and it will follow `SKILL.md` on its own.

`run.py` runs all the way to the block split, then hands the transcript to the agent in your session; once the summary is written it continues to the vault. Interruptions are fine: re-running the same command skips finished steps, and even the raw per-chunk ASR responses are cached, so nothing is billed twice.

---

## Models and cost

| Stage | What runs | Cost |
|---|---|---|
| Transcription (default) | SiliconFlow `XingChenAGI/XingChenASR-Diarize-V3.0` over the OpenAI-compatible `/v1/audio/transcriptions`, returning sentence-level timestamps and diarization in one pass | SiliconFlow's pricing page currently lists the XingChenASR family, including this model, as **free**. Free lists change — check the page before relying on it |
| Transcription (fallback) | Local faster-whisper (`--engine local`) | Free, but slow (a 3-hour episode takes hours on a CPU), and there is no diarization |
| Summarization | The agent in your session, or a subagent it spawns | No external chat model is called; it only uses the session budget you already have |

Switching provider: change `asr.cloud_base_url` and `asr.cloud_model` in `config.toml`, and add the new hostname to `asr.allowed_hosts` (otherwise the allow-list rejects it, exit code 13). The key is only ever sent to hosts in `allowed_hosts` — that check lives in code, not config.

Staying fully offline: add `--engine local`, or set `asr.engine` to `local`. The local engine needs an interpreter with faster-whisper installed; put it in `runtime.python` (leave it empty to use the current `python3`).

---

## What it is for

| When you listen to podcasts | What this does |
|---|---|
| You forget it, and quoting means scrubbing the timeline | The transcript has sentence-level timestamps, and every key point and quote carries one like `01:23:45` |
| You cannot tell who said what | Cloud ASR does speaker diarization (Speaker A / B); long audio is chunked and labels are aligned globally |
| A 3-hour episode takes over an hour locally | Cloud by default: a 1.5-hour episode came back in about 80 seconds in our test |
| "Quotes" in summaries are often invented | Every quote is matched verbatim against the transcript within ±30 s; misses are relabeled as paraphrase |
| You fear a tool overwriting your vault | `create_only` by default: it stops if the target exists (exit code 11), never overwrites, never reads existing notes |
| Re-running means paying again | Every step is cached (raw chunk responses, transcript, summary); a re-run skips finished steps |
| Proper nouns get mangled (transliterated names, brand names) | `hotwords.txt`, one term per line, sent as the cloud `hotword` field and folded into the local `initial_prompt` |
| The summary style is not to your taste | The prompt lives in `templates/summary_prompt.md`; edit it and the next run follows |

---

## What each step does

Four steps, each one written to disk, so a re-run skips whatever already finished.

1. **`download.py`** detects the source type (Ximalaya / Xiaoyuzhou / generic RSS / direct audio link / local file) and fetches the audio plus metadata. Paid episodes stop here (exit code 5); no auth bypassing.
2. **`transcribe.py`** cuts the audio into 20-minute chunks with 30 seconds of overlap, sends them to cloud ASR, and gets back sentence-level timestamps and speaker labels. Per-chunk labels are then aligned globally into Speaker A / B using time overlap, mutual exclusion inside a chunk, and reuse of free labels. Raw responses are cached under `<job>/raw/`, so a re-run is not billed twice. After transcription, downloaded audio is **archived** to `<cache>/音频库/` by default (`run.py audio` lists and purges it).
3. **Summarization** happens in your session: `summarize.py plan` splits the transcript into 15-minute blocks, they get read one by one, `add` writes each block abstract back, and `final` assembles everything and verifies the quotes. Episodes longer than two hours can be compressed block by block by subagents.
4. **`publish.py`** renders the template and writes into the vault atomically, stopping if the target exists.

Only step 2 costs money, and with the local engine even that is free. Step 3 is the thinking part, and there is no chat-model call anywhere in the code.

---

## What the output looks like

New files in the vault:

```
播客/2026-10-03 EP752 Title [job-id].md                  # main note: the summary
播客/逐字稿/2026-10-03 EP752 Title [job-id] - 逐字稿.md     # full transcript
播客/逐字稿/2026-10-03 EP752 Title [job-id].vtt             # WebVTT subtitles (optional)
```

The main note runs top to bottom: YAML frontmatter (`source`, `duration`, `speakers`, `asr_model`, `word_count`, …) → info table → TL;DR → layered key points (with evidence positions) → quotes → action-item table → chapter timeline → show notes → link to the transcript.

The note states that speaker labels come from ASR clustering and that cross-chunk alignment can be wrong, rather than presenting them as facts.

---

## Security and privacy

1. **Your vault is left alone.** It never scans the disk for a vault; the path comes from config only. It never reads an existing note and only creates exact paths. If `safety.scan_vault` or `read_existing_notes` is ever `true`, config loading fails immediately.
2. **The key lives in exactly one place.** It is read from `.env` (or the process environment), never written into code, config, or markdown, and only its last four characters ever reach stdout or logs.
3. **The key only goes to allow-listed hosts.** `allowed_hosts` is enforced in code, so editing config cannot send it elsewhere.
4. **Untrusted content is data, not instructions.** Any "instruction" inside a transcript, show notes, or RSS description (change config, change paths, run commands, send the key) is ignored — that text gets written into your notes, so it would otherwise persist as an injection vector.
5. **Paths and deletion have boundaries.** Job ids and vault sub-paths are checked for escapes, symlinked paths are refused, and `--purge` requires an explicit scope; a no-op purge reports a warning instead of silently claiming success.

---

## Supported agent harnesses

It follows the open Agent Skills standard, so one `SKILL.md` works everywhere. Delete `SKILL.md` and it is still a plain set of CLI scripts.

| Harness | Install location | Invocation |
|---|---|---|
| Claude Code | `~/.claude/skills/` (user) or `.claude/skills/` (project) | `/podscribe`, or description-based auto-trigger |
| Codex | `~/.agents/skills/` (plus a `~/.codex/skills/` symlink) or `.agents/skills/` | `$podscribe` in your prompt, or `/skills` |
| WorkBuddy | `~/.workbuddy/skills/` | Paste the link and ask |
| Any standard-compliant tool / plain CLI | any directory | `./install.sh --dir <path>`, or run the scripts directly |

Permissions, sandboxes and subagent usage per harness are covered in `adapters/claude-code.md` and `adapters/codex.md`. Codex blocks writes in its sandbox by default — read that document before the first run.

---

## Commands

In order:

```bash
python3 download.py   --input "<url>"
python3 transcribe.py --job-dir <dir>
python3 summarize.py plan  --job-dir <dir>                         # split into 15-minute blocks
# read blocks -> write back with add -> assemble and verify with final
python3 summarize.py final --job-dir <dir> --json-file /tmp/summary.json
python3 publish.py    --job-dir <dir>                              # write into the vault
```

Helpers:

```bash
python3 setup.py --check                                           # what is still missing (read-only)
python3 run.py status --job-dir <dir>
python3 run.py audio  --list                                       # archived audio + chunk usage
python3 run.py audio  --purge --job-dir <dir>                      # one episode only
python3 run.py audio  --purge --all                                # everything (must be explicit)
python3 summarize.py prompt --job-dir <dir>                        # print the active prompt
python3 tests.py                                                   # 46 regression tests
```

(`--config` is omitted above; pass `--config <path>` or set `$PODCAST_CONFIG` to use a different file.)

---

## Write your own summary prompt

`templates/summary_prompt.md` is the prompt used when writing the summary. Edit it directly and the next run picks it up. Everything is fair game except the "hard constraints" section, which the validator rejects if you touch it: length, number of key points, tone, whether to include action items. It also ships a few optional switches — minimal card, long-form deep dive, critical lens, action-oriented.

To see the prompt currently in effect:

```bash
python3 summarize.py prompt --job-dir <dir>
```

After editing the prompt, re-run `final` and `publish`; no need to transcribe again.

---

## Files

| File | Responsibility |
|---|---|
| `SKILL.md` | The Agent Skill definition (triggers, usage, red lines) — the manual your agent reads |
| `setup.py` | First-run configuration: asks for the vault path and the key, writes `config.local.toml` and `.env` |
| `core.py` | JSON protocol, config validation and layering, path safety, atomic no-overwrite writes |
| `download.py` | Ximalaya / Xiaoyuzhou / generic RSS / direct audio link / local file |
| `transcribe.py` | Cloud ASR (default, with diarization) or local faster-whisper |
| `summarize.py` | plan / show / add / final / prompt; calls no external model |
| `publish.py` | Renders the template and writes into the vault atomically |
| `run.py` | Orchestration that skips finished steps via the cache |
| `tests.py` | 46 regression tests (offline, never touches a real vault) |
| `config.toml` | Template: `vault.path` is yours to fill; the ASR fields ship pre-filled for SiliconFlow |
| `config.local.toml` | Local private overrides (written by setup.py, gitignored) |
| `templates/note.md` | Note template (`{{token}}` placeholders) |
| `templates/summary_prompt.md` | Summary prompt; editable except the "hard constraints" section |
| `hotwords.txt` | Proper-noun vocabulary, one term per line |
| `install.sh` | Multi-harness installer |
| `adapters/` | Per-harness install location, permissions, sandbox and invocation |
| `CHANGELOG.md` | Release-by-release change log |
| `.env.example` | Key-file template (the real `.env` is gitignored) |

Scripts talk to each other over single-line JSON on stdout, diagnostics go to stderr, and exit codes carry meaning:

| Code | Meaning | Code | Meaning |
|---|---|---|---|
| 0 | Success | 9 | API error (including a missing key) |
| 2 | Bad input or arguments | 10 | Validation failed |
| 3 | Config error (vault / ASR not filled in) | 11 | Target exists, refusing to overwrite |
| 4 | Network error | 12 | Filesystem error |
| 5 | Paid content, stopped | 13 | Privacy/safety red line tripped |
| 6 | Media tool error | 7 | Transcription failed |

---

## About the key

- `.gitignore` excludes `.env`, `.env.*` and `config.local.toml`, and only allows `.env.example` through. Run `git status --ignored` once before pushing.
- Lookup order: `$PODCAST_ENV_FILE` → `<install-dir>/.env` → `~/.config/podscribe/.env`. Process environment variables win, so `export SILICONFLOW_API_KEY=...` overrides temporarily. Parsing is built in and supports comments, the `export` prefix and quotes; no third-party package is involved.
- If a key ever appears in a chat log or a log file, rotate it in the provider dashboard — deleting the file is not enough.
- The repository contains no real key, no real vault path and no username.

---

## Roadmap

- Speaker labels are anonymous cluster IDs, not voiceprint identification. Shows with more than three speakers can be mislabeled, and the fallback heuristic for cross-chunk alignment can be improved.
- Whether the cloud API actually honours hotwords is untested; today it only falls back to a request without hotwords when the API errors.
- The local engine (faster-whisper) has no diarization, and long episodes are slow on CPU.
- Xiaoyuzhou episodes are read from `og:audio` plus `__NEXT_DATA__`; paid or private episodes behind a login are not supported.
- `--revision-suffix` creates a new file and leaves the old note alone; merging into one note is not implemented.
- Only Chinese episodes have been tested so far; other languages are untested.

---

## Feedback

- If it works for you, a **[star](https://github.com/Xamic-t/Podscribe)** costs nothing and helps a lot ⭐
- Bugs, questions, feature ideas: **[open an issue](https://github.com/Xamic-t/Podscribe/issues)** or send a **[pull request](https://github.com/Xamic-t/Podscribe/pulls)** — I'll get to it as soon as I can.
- Before changing code, skim **[CONTRIBUTING.md](CONTRIBUTING.md)**: PR flow, code style, test requirements and red lines.

---

## License

MIT, see `LICENSE`.
