# podscribe

English · [简体中文](README.md)

Turn a podcast episode into notes: give it a link, get a timestamped Obsidian note plus the full transcript of the episode.

- The note carries a TL;DR, layered key points (each anchored to a position like `01:23:45`), verbatim-checked quotes, action items, and a chapter timeline.
- The transcript is the whole episode, split by chapter, with the same timestamps and speaker labels.
- A WebVTT subtitle file comes along; you can turn it off.

Transcription goes to SiliconFlow's ASR by default, which returns speaker diarization and sentence-level timestamps in one pass. Summarization calls no external chat model: your agent reads the transcript and writes it, and long episodes can be handed to subagents block by block.

---

## Install

### 1. Requirements

- Python 3.11+ (the scripts use the standard library `tomllib` and have no third-party dependencies)
- `ffmpeg` / `ffprobe` (splitting long audio) and `curl` (downloading)
- A SiliconFlow API key: generated in the dashboard after sign-up. Free models still need a key.

If you want to stay fully offline, see "Local transcription" below and skip the key entirely.

### 2. Get the code

```bash
git clone <your-repo> podscribe && cd podscribe
```

### 3. Install it into your agent (optional)

```bash
./install.sh                        # Claude Code + Codex, user level
./install.sh --harness claude       # Claude Code only
./install.sh --harness codex        # Codex only
./install.sh --harness workbuddy    # WorkBuddy
./install.sh --project              # into the current project, can be committed
./install.sh --dir /some/where      # custom directory
```

`install.sh` copies the scripts and templates, replaces the path placeholder in `SKILL.md` with the real install path, and adds or removes frontmatter fields per harness. Existing `config.toml` and `.env` files are never overwritten, so it is safe to run repeatedly.

Skip this step if you only use the command line.

### 4. Fill in three things

The shipped `config.toml` has three placeholders, and **nothing runs until you fill them**. This is deliberate: which model you use and whose credits it spends is your call.

```bash
cp .env.example .env && chmod 600 .env
```

First, put `SILICONFLOW_API_KEY=sk-...` in `.env` (generate it in the SiliconFlow dashboard after sign-up; free models still need a key).

Second, the vault path in `config.toml`:

```toml
[vault]
path = "/absolute/path/to/your/obsidian/vault"
```

Third, the ASR model and endpoint:

```toml
[asr]
cloud_model = "XingChenAGI/XingChenASR-Diarize-V3.0"   # pick one from the pricing page; this one is listed as free
cloud_base_url = "https://api.siliconflow.cn/v1"
```

Missing any of the three stops the run with exit code 3: `VAULT_PLACEHOLDER` for the vault, `ASR_NOT_CONFIGURED` for the ASR fields, and the message tells you what to put where. If you would rather skip the cloud, add `--engine local` to use local faster-whisper, and those two ASR fields are not needed.

If you would rather not commit the real path, copy `config.toml` to `config.local.toml` (already in `.gitignore`) and pass `--config config.local.toml`.

### 5. Run it

```bash
python3 run.py --input "<episode-url>" --config config.toml
```

---

## What it is for

| When you listen to podcasts                | What this does                                                                                   |
| ------------------------------------------ | ------------------------------------------------------------------------------------------------ |
| You forget it, and quoting means scrubbing the timeline | The transcript has sentence-level timestamps, and every key point and quote carries one like `01:23:45` |
| You cannot tell who said what              | Cloud ASR does speaker diarization (Speaker A / B); long audio is chunked and the labels are aligned globally |
| A 3-hour episode takes over an hour locally | Cloud by default: a 1.5-hour episode came back in about 80 seconds in our test                     |
| "Quotes" in summaries are often invented   | Every quote is matched verbatim against the transcript within ±30 s; misses are relabeled as paraphrase |
| You fear a tool overwriting your vault     | `create_only` by default: it stops if the target exists (exit code 11), never overwrites, never reads existing notes |
| Re-running means paying again              | Every step is cached (raw chunk responses, transcript, summary); re-running skips finished steps   |
| Proper nouns get mangled (`Vercel` → `Versel`) | `hotwords.txt`, one term per line, fed to both the cloud `hotword` field and the local `initial_prompt` |
| The summary style is not to your taste     | The prompt lives in `templates/summary_prompt.md`; edit it and the next run follows                 |

---

## How it works

Four steps, each one written to disk, so a re-run skips whatever already finished.

1. **download.py** detects the source type (Ximalaya / Xiaoyuzhou / RSS / direct audio link / local file) and fetches the audio plus metadata. Paid episodes stop here (exit code 5); no auth bypassing.
2. **transcribe.py** cuts the audio into 20-minute chunks with 30 seconds of overlap, sends them to cloud ASR, and gets back sentence-level timestamps and speaker labels. Per-chunk labels are then aligned globally into Speaker A / B using time overlap, mutual exclusion within a chunk, and reuse of free labels. Raw responses are cached under `<job>/raw/`.
3. **Summarization** is done by the agent in your session: `summarize.py plan` splits the transcript into 15-minute blocks, they get read one by one, `add` writes the block abstract back, and `final` assembles everything and verifies quotes. Episodes longer than two hours can be compressed block by block by subagents, which also counts as part of the session.
4. **publish.py** renders the template and writes into the vault atomically, stopping if the target exists.

Only step 2 costs money, and with the local engine even that is free. Step 3 is the thinking part, and there is no chat-model call anywhere in the code.

---

## Which models, and what they cost

| Stage                | What runs                                                                                                                                        | Cost                                                                                                    |
| -------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------- |
| Transcription (default) | SiliconFlow `XingChenAGI/XingChenASR-Diarize-V3.0`, over the OpenAI-compatible `/v1/audio/transcriptions`, with diarization and sentence-level timestamps | SiliconFlow's pricing page lists the XingChenASR family, including this model, as free. The free list changes, so check the pricing page before you rely on it |
| Transcription (fallback) | Local faster-whisper                                                                                                                             | Free, but slow: a 3-hour episode takes hours on a laptop CPU, and there is no diarization                  |
| Summarization        | The agent in your session, or a subagent it spawns                                                                                                 | No external chat model is called, so no extra billing                                                      |

**You need a key before the cloud path works.** Sign up at SiliconFlow, generate a key, put it in `.env`. No code changes needed. The model and endpoint in `config.toml` must be filled in too, otherwise transcription stops before it starts (`ASR_NOT_CONFIGURED`, exit code 3).

Another provider works too: change `asr.cloud_base_url`, `api_key_env`, and `allowed_hosts` in `config.toml`. The key is only sent to hosts in `allowed_hosts` — that is enforced in code, so editing config cannot send it elsewhere.

**If the API is unavailable, switch to local:** `python3 transcribe.py --job-dir <dir> --config config.toml --engine local`, or set `asr.engine` to `local`. The local engine needs a Python environment with faster-whisper installed; the interpreter path goes in `runtime.python` (a leading `~` is expanded).

---

## Write your own summary prompt

`templates/summary_prompt.md` is the prompt used when writing the summary. Edit it directly and the next run picks it up. Everything is fair game except the "hard constraints" section, which the validator rejects if you touch it: length, number of key points, tone, whether to include action items. It also ships a few optional switches — minimal card, long-form deep dive, critical lens, action-oriented.

To see the prompt currently in effect:

```bash
python3 summarize.py prompt --job-dir <dir> --config config.toml
```

After editing, re-run `final` and `publish`. No need to transcribe again.

---

## What you get

These files appear in your vault:

```
播客/2026-10-03 EP752 #752.标题 [job-id].md                 # main note: the summary
播客/逐字稿/2026-10-03 EP752 #752.标题 [job-id] - 逐字稿.md     # full transcript
播客/逐字稿/2026-10-03 EP752 #752.标题 [job-id].vtt           # WebVTT subtitle (optional)
```

The main note runs top to bottom: YAML frontmatter (`source`, `duration`, `speakers`, `asr_model`, `word_count`, …) → info table → TL;DR → layered key points (with evidence positions) → quotes → action item table → chapter timeline → episode description → link to the transcript.

The note states that speaker labels come from ASR clustering and that cross-chunk alignment can drift. It does not present them as established facts.

---

## Features

1. **Resume anywhere.** One directory per job: `metadata.json / transcript.json / summary.json / receipt.json`, and whichever is missing gets built. Re-run the same command after an interruption and finished steps are skipped, with no repeated requests.
2. **Speaker diarization.** 20-minute chunks (long context, so proper nouns survive), 30 seconds of overlap between neighbours for global alignment. On a 3-hour episode the alignment settled on 2 speakers.
3. **Quotes verified verbatim.** `summarize.py final` checks every quote against the transcript; misses are moved to a paraphrase section and labeled, so a fabricated line never appears as a quote.
4. **Your vault is untouched.** No disk scanning to find the vault; the path comes only from `config.toml`. No existing note is ever read, only new files are created at exact paths. If `safety.scan_vault` or `read_existing_notes` is `true`, config loading fails outright.
5. **Safety checks live in code.** The key exists only in `.env` (mode 600), never in code, config, or markdown. Instructions found inside transcripts, episode descriptions, or RSS feeds are treated as data, not commands. Job ids and vault subpaths are checked for traversal, and a path through a symlink is refused. `--purge` requires an explicit scope, and when nothing matches you get a warning instead of a silent success.
6. **Hotword list.** One term per line in `hotwords.txt`. When a proper noun comes back wrong, add the correct spelling and the next episode gets it right.

---

## Supported agent harnesses

This follows the open Agent Skills standard, so one `SKILL.md` works everywhere. Strip out `SKILL.md` and it is a plain CLI tool.

| Harness                     | Where it installs                                                            | How you invoke it                        |
| --------------------------- | ---------------------------------------------------------------------------- | ---------------------------------------- |
| Claude Code                 | `~/.claude/skills/` (user) or `.claude/skills/` (project)                    | `/podscribe`, or triggered by description |
| Codex                       | `~/.agents/skills/` (plus a `~/.codex/skills/` symlink) or `.agents/skills/` | `$podscribe` in your prompt, or `/skills` |
| WorkBuddy                   | `~/.workbuddy/skills/`                                                       | Paste the link and ask                    |
| Other standard tools / CLI   | Any directory                                                                | `./install.sh --dir <path>`, or run the scripts directly |

Permissions, sandboxing, and subagent usage per harness are in `adapters/claude-code.md` and `adapters/codex.md`. Codex's sandbox blocks writes by default, so read that one before your first run.

---

## Commands

In order:

```bash
python3 download.py   --input "<url>" --config config.toml
python3 transcribe.py --job-dir <dir>  --config config.toml
python3 summarize.py plan  --job-dir <dir> --config config.toml    # split into 15-minute blocks
# read blocks → write back with add → assemble and verify with final
python3 summarize.py final --job-dir <dir> --config config.toml --json-file /tmp/summary.json
python3 publish.py    --job-dir <dir>  --config config.toml        # write into the vault
```

Helpers:

```bash
python3 run.py status --job-dir <dir> --config config.toml
python3 run.py audio  --config config.toml --list                  # archived audio + chunk usage
python3 run.py audio  --config config.toml --purge --job-dir <dir> # one episode only
python3 run.py audio  --config config.toml --purge --all           # everything (must be explicit)
python3 summarize.py prompt --job-dir <dir> --config config.toml   # print the active prompt
python3 tests.py                                                   # 35 regression tests
```

---

## Files

| File                            | Role                                                              |
| ------------------------------- | ----------------------------------------------------------------- |
| `SKILL.md`                      | Agent Skill definition (triggers, usage, red lines): the manual for the agent |
| `core.py`                       | JSON protocol, config validation, path safety, atomic non-overwriting writes, dotenv parsing |
| `download.py`                   | Ximalaya / Xiaoyuzhou / generic RSS / direct link / local file    |
| `transcribe.py`                 | Cloud ASR (default, with diarization) or local faster-whisper      |
| `summarize.py`                  | plan / show / add / final / prompt; makes no external model calls  |
| `publish.py`                    | Renders the template and writes into the vault atomically          |
| `run.py`                        | Orchestration; skips steps already cached                          |
| `tests.py`                      | 35 regression tests (no network, no real vault)                    |
| `config.toml`                   | Three required fields: `vault.path`, `asr.cloud_model`, `asr.cloud_base_url` |
| `templates/note.md`             | Note template (`{{token}}` placeholders)                           |
| `templates/summary_prompt.md`   | The summary prompt; editable except the "hard constraints" section |
| `hotwords.txt`                  | Proper noun list, one per line                                     |
| `install.sh`                    | Multi-harness installer                                            |
| `adapters/`                     | Install paths, permissions, sandboxing, invocation per harness      |
| `CHANGELOG.md`                  | Full log of three review rounds                                    |
| `.env.example`                  | Template for the secrets file (the real `.env` is git-ignored)      |

The scripts talk over single-line JSON on stdout, with diagnostics on stderr. Exit codes carry meaning:

| Code | Meaning                        | Code | Meaning                    |
| ---- | ------------------------------ | ---- | -------------------------- |
| 0    | Success                        | 9    | API error (missing key too) |
| 2    | Bad input / arguments          | 10   | Validation failed          |
| 3    | Config error (vault or ASR unset) | 11 | Target exists, refusing to overwrite |
| 4    | Network error                  | 12   | Filesystem error           |
| 5    | Paid content, stopped          | 13   | Privacy / safety line hit  |
| 6    | Media tool error               | 7    | Transcription failed       |

---

## About the key

- `.gitignore` excludes `.env` and `.env.*`, allowing only `.env.example`. Confirm with `git status --ignored` before you push.
- Lookup order: `$PODCAST_ENV_FILE` → `<skill>/.env` → `~/.config/podscribe/.env`. Process environment variables win, so `export SILICONFLOW_API_KEY=...` overrides temporarily. Parsing is built in and supports comments, the `export` prefix, and quotes; no third-party package is involved.
- If a key ever lands in a chat log or terminal output, rotate it in the provider dashboard. Deleting the file is not enough.
- This repository contains no real keys, real vault paths, or usernames.

---

## Room to improve

- Speaker labels are anonymous cluster ids, not voiceprints. Shows with more than two speakers can be mislabeled, and the fallback heuristic for cross-chunk alignment still has room to get better.
- Whether the cloud service actually honors the hotword field is untested; today the code only falls back to a request without hotwords when the API rejects it.
- The local engine (faster-whisper) has no diarization and this machine only has CPU int8, so long episodes are slow. A local option with diarization would be better.
- Xiaoyuzhou is read from the page's `og:audio` and `__NEXT_DATA__`, so paid or private episodes that need a login have no reachable direct link. Not supported for now.
- A revision created with `--revision-suffix` becomes a new file and leaves the old note alone; merging into one note is not implemented.
- Only Chinese episodes have been tested so far. Other languages are unverified.

---

## License

MIT. See `LICENSE`.

A few documents (`adapters/*.md`, `CHANGELOG.md`) are Chinese only at the moment.
