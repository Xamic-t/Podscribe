# podscribe

简体中文（默认） · [English](README.en.md)

[![GitHub stars](https://img.shields.io/github/stars/Xamic-t/Podscribe?style=social)](https://github.com/Xamic-t/Podscribe)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

给一个播客单集链接，拿回一篇能追溯的 Obsidian 笔记：TL;DR、分层要点（每条挂着 `01:23:45` 这样的证据位置）、逐字校验过的金句、行动项、章节时间表，外加整期逐字稿和一份 WebVTT 字幕。

## 全流程：从链接到笔记

**链接 → 下载 → 转写 → 总结 → 落盘成笔记**，四步，每步都落盘，重跑会跳过已完成的步骤。

| 步骤 | 在哪跑 | 做什么 | 花钱吗 |
|---|---|---|---|
| ① 下载<br>`download.py` | 本地 | 识别来源：喜马拉雅 / 小宇宙 / RSS / 音频直链 / 本地音频；取音频与标题、主播、时长、节目简介。**付费单集在这一步就停**（退出码 5），不绕鉴权 | 免费 |
| ② 转写<br>`transcribe.py` | 云端<br>（硅基流动） | **唯一需要联网的一步。** 20 分钟切一块 + 块间重叠 30 秒，一次拿到**句级时间戳 + 说话人 A/B**；默认模型 `XingChenAGI/XingChenASR-Diarize-V3.0`。加 `--engine local` 可换成完全本地的 faster-whisper | 目前免费 |
| ③ 总结<br>你的 Agent | 你的会话 | **不调用任何外部对话模型。** 由正在跟你对话的 Agent 读逐字稿、分块写要点、汇总并逐字校验金句；超过 2 小时的节目可以派子 Agent 分块压缩 | 只吃会话额度 |
| ④ 落盘<br>`publish.py` | 本地 | 渲染模板 → 原子写入 vault。默认 `create_only`：目标已存在就停（退出码 11），**绝不覆盖、也不读你已有的笔记** | 免费 |

**钱和隐私，先说清楚：**

- **只有 ② 会上网。** 音频会上传到硅基流动（用哪个模型、发到哪个域名来自你的 `config.toml`，代码里另有 `allowed_hosts` 白名单兜底）。XingChenASR 系列按定价页目前标为**免费**。
- **③ 不产生额外费用。** 读稿和写终稿都在你正在用的会话里完成。
- **①④ 是纯本地。** 下载和写盘不经过任何第三方。
- **不碰你的库。** 不扫描磁盘找库、不读已有笔记，只按 `config.toml` 里的路径精确新建。

## 快速开始

### 需要什么

| 需要 | 说明 |
|---|---|
| Python 3.11+ | 只用标准库（`tomllib`），没有第三方依赖 |
| `ffmpeg` / `ffprobe` | 切长音频、探时长 |
| `curl` | 下载音频（macOS 与 Windows 10+ 自带） |
| 硅基流动 API Key | 免费模型也要 key；不想用云端就加 `--engine local`，可以不办 |

### 第 1 步：装

```bash
git clone https://github.com/Xamic-t/Podscribe.git podscribe && cd podscribe

./install.sh                        # 装进 Claude Code + Codex（用户级）
./install.sh --harness codex        # 只装 Codex
./install.sh --harness claude       # 只装 Claude Code
./install.sh --project              # 装进当前项目，可随仓库提交
./install.sh --dir /some/where      # 自定义目录
```

只在命令行用就跳过这一步。已存在的 `config.toml`、`config.local.toml`、`.env` 都不会被覆盖，可以反复跑。

### 第 2 步：配一次（只问两样）

```bash
python3 setup.py          # ① Obsidian 库路径  ② 硅基流动 API Key（输入不回显）
```

不想开交互（比如让 Agent 代劳）就分步来：

```bash
python3 setup.py --vault "/absolute/path/to/your/obsidian/vault"
echo "sk-..." | python3 setup.py --key-stdin    # 不要把 key 贴进聊天窗口
python3 setup.py --verify                       # 库可写？key 有效？
```

- 私人信息落在 `config.local.toml`（库路径）和 `.env`（密钥；POSIX 下 600，Windows 上会尝试 `icacls` 收紧）。两个文件都在 `.gitignore` 里，**`config.toml` 保持模板原样**。
- `config.local.toml` 会自动叠加在 `config.toml` 之上，所以命令里**不用再传 `--config`**（也可以设 `$PODCAST_CONFIG` 指定别的文件）。
- 随时 `python3 setup.py --check` 看还缺什么：**只体检、不改文件**，退出码恒为 0。
- `--vault` 只确认目录存在，不会列你的库、也不会读里面的笔记。

### 第 3 步：跑

```bash
python3 run.py --input "<播客单集链接>"
```

也能直接跟 Agent 说「总结这期播客 <链接>」，它会照 `SKILL.md` 自己执行。

`run.py` 会一路跑到切好分块，然后把逐字稿交给会话里的 Agent；Agent 写完总结再继续落盘。中途断了不要紧：重跑同一条命令会跳过已完成的步骤，连转写分块的原始返回都缓存了，不会重复计费。

---

## 费用与模型

| 环节 | 用什么 | 费用 |
|---|---|---|
| 转写（默认） | 硅基流动 `XingChenAGI/XingChenASR-Diarize-V3.0`，走兼容 OpenAI 的 `/v1/audio/transcriptions`，一次返回句级时间戳 + 说话人分离 | 按硅基流动定价页，XingChenASR 系列（含这个模型）目前标为**免费**；免费名单会调整，用之前看一眼定价页 |
| 转写（备选） | 本地 faster-whisper（`--engine local`） | 不花钱，但慢（3 小时节目在本机 CPU 上以小时计），且没有说话人分离 |
| 总结 | 会话里的 Agent，或它派的子代理 | 不调用外部对话模型，只消耗你当前的会话额度 |

换别的 ASR 供应商：改 `config.toml` 的 `asr.cloud_base_url` 与 `asr.cloud_model`，把新域名加进 `asr.allowed_hosts`（否则会被白名单拒掉，退出码 13）。密钥只发往 `allowed_hosts` 里的主机，这是代码里的硬校验。

完全不用云端：加 `--engine local`，或把 `asr.engine` 设成 `local`。本地引擎需要一个装了 faster-whisper 的解释器，路径写在 `runtime.python`（留空 = 用当前 `python3`）。

---

## 它解决什么问题

| 听播客时会遇到 | 这里的做法 |
|---|---|
| 听完就忘，想引用一句得回去拖进度条 | 逐字稿带句级时间戳，笔记里的要点和金句都挂着 `01:23:45` |
| 分不清哪句是谁说的 | 云端 ASR 带说话人分离（说话人 A / B），长音频分块后做全局标签对齐 |
| 3 小时节目本地转写要跑一小时以上 | 默认走云端，1.5 小时的节目实测约 80 秒出稿 |
| 总结里的「金句」经常是模型编的 | 每条金句回原文 ±30 秒做逐字匹配，命中才算原话，命不中的降级标注成「模型概括」 |
| 怕工具覆盖或弄脏已有的库 | 默认 `create_only`：目标存在就停（退出码 11），不覆盖，也不读已有笔记 |
| 重跑一次就重新扣一次钱 | 每步都有缓存（分片原始返回、逐字稿、总结），中断后重跑会跳过已完成的步骤 |
| 专有名词被写错（音译名、英文品牌名） | `hotwords.txt` 一行一个词，云端作为 `hotword` 提交，本地拼进 `initial_prompt` |
| 总结风格不合口味 | 提示词在 `templates/summary_prompt.md`，自己改，下次生效 |

---

## 每一步在做什么

四步，每步都落盘，重跑会跳过已完成的步骤。

1. **`download.py`** 认链接类型（喜马拉雅 / 小宇宙 / 通用 RSS / 音频直链 / 本地文件），取音频和元信息。付费单集在这一步就停（退出码 5），不绕鉴权。
2. **`transcribe.py`** 按 20 分钟切块、块间重叠 30 秒，交给云端 ASR，拿到句级时间戳和说话人标签，再用「时间重叠 + 同块互斥 + 复用空闲标签」把各块的局部标签对齐成全局的说话人 A / B。每块的原始返回缓存在 `<job>/raw/`，重跑不重复计费。转写完成后，下载的音频默认**归档**到 `<缓存>/音频库/`（可用 `run.py audio` 查看和清理）。
3. **总结** 由会话里的 Agent 完成：`summarize.py plan` 把逐字稿切成 15 分钟一块，逐块读、用 `add` 回写，`final` 汇总并校验金句。超过 2 小时的节目可以让主 Agent 派子代理分块压缩。
4. **`publish.py`** 渲染模板，原子写入 vault；目标已存在就停。

花钱的只有第 2 步，用本地引擎时连这一步也不花钱。第 3 步是动脑子的部分，代码里没有任何对话模型调用。

---

## 输出长什么样

vault 里多出这些文件：

```
播客/2026-10-03 EP752 标题 [job-id].md                    # 主笔记：总结
播客/逐字稿/2026-10-03 EP752 标题 [job-id] - 逐字稿.md      # 整期原文
播客/逐字稿/2026-10-03 EP752 标题 [job-id].vtt              # WebVTT 字幕（可关）
```

主笔记从上到下是：YAML frontmatter（`source / duration / speakers / asr_model / word_count` 等）→ 信息表 → TL;DR → 分层要点（带证据位置）→ 金句 → 行动项表 → 章节时间表 → 节目简介 → 逐字稿链接。

笔记里会写明说话人标签来自 ASR 聚类、跨块对齐可能有误差，不把这些写成确定结论。

---

## 安全与隐私

1. **不碰你的库。** 不扫描磁盘找库，vault 路径只来自配置；不读取 vault 里任何已有笔记，只做精确路径新建。`safety.scan_vault` 或 `read_existing_notes` 一旦为 `true`，配置加载直接失败。
2. **密钥只在一个地方。** 只从 `.env`（或进程环境变量）读取，不写进代码、配置或 md 文件；stdout 与日志里永远只出现尾 4 位。
3. **密钥只发往白名单主机。** `allowed_hosts` 是代码里的硬校验，改配置也发不出去。
4. **不可信内容只当数据。** 逐字稿、节目简介、RSS 描述里出现的任何「指示」（要求改配置、改路径、执行命令、发密钥）一律忽略——这些内容会被写进笔记，等于把注入文本持久化了。
5. **路径与删除都有边界。** job id 与 vault 子路径做越界校验，路径经过符号链接就拒绝写入；`--purge` 必须显式带范围，没匹配上会给出 warning 而不是静默报成功。

---

## 支持的 Agent Harness

遵循 Agent Skills 开放标准，同一份 `SKILL.md` 到处能用。去掉 `SKILL.md`，它也是一套纯 CLI 脚本。

| Harness | 安装位置 | 调用 |
|---|---|---|
| Claude Code | `~/.claude/skills/`（用户）或 `.claude/skills/`（项目） | `/podscribe`，或按描述自动触发 |
| Codex | `~/.agents/skills/`（并软链 `~/.codex/skills/`）或 `.agents/skills/` | 提示词里写 `$podscribe`，或用 `/skills` |
| WorkBuddy | `~/.workbuddy/skills/` | 说出链接即可 |
| 其它遵循标准的工具 / 纯 CLI | 任意目录 | `./install.sh --dir <路径>`，或直接跑脚本 |

各 harness 的权限、沙箱和子代理用法见 `adapters/claude-code.md` 与 `adapters/codex.md`。Codex 的沙箱默认会挡写操作，第一次跑之前先看一眼那份文档。

---

## 命令

顺序执行：

```bash
python3 download.py   --input "<链接>"
python3 transcribe.py --job-dir <dir>
python3 summarize.py plan  --job-dir <dir>                         # 切成 15 分钟一块
# 逐块读稿 → add 回写 → final 汇总并校验金句
python3 summarize.py final --job-dir <dir> --json-file /tmp/summary.json
python3 publish.py    --job-dir <dir>                              # 写进 vault
```

辅助：

```bash
python3 setup.py --check                                           # 还缺什么配置（只读）
python3 run.py status --job-dir <dir>
python3 run.py audio  --list                                       # 归档音频 + 分片占用
python3 run.py audio  --purge --job-dir <dir>                      # 只清某一期
python3 run.py audio  --purge --all                                # 全清（必须显式）
python3 summarize.py prompt --job-dir <dir>                        # 打印当前总结提示词
python3 tests.py                                                   # 46 项回归测试
```

（上面的命令都省略了 `--config`；要指定别的配置文件时加 `--config <路径>`，或设 `$PODCAST_CONFIG`。）

---

## 总结提示词自己写

`templates/summary_prompt.md` 就是写总结时用的提示词，直接改，改完下次生效。除了「硬性约束」那一节（动了会被校验拦下），其余随便改：篇幅、要点数量、语气、要不要行动项，都在里面。里面还留了几个可选开关，比如极简卡片、深度长文、批判视角、面向行动。

想看当前生效的版本：

```bash
python3 summarize.py prompt --job-dir <dir>
```

改完提示词后重跑 `final` 和 `publish` 就行，不用重新转写。

---

## 文件说明

| 文件 | 职责 |
|---|---|
| `SKILL.md` | Agent Skill 定义（触发语、用法、红线），给 Agent 看的说明书 |
| `setup.py` | 首次配置：问库路径 + 密钥，写 `config.local.toml` 与 `.env` |
| `core.py` | JSON 协议、配置校验与叠加、路径安全、原子无覆盖写入 |
| `download.py` | 喜马拉雅 / 小宇宙 / 通用 RSS / 音频直链 / 本地文件 |
| `transcribe.py` | 云端 ASR（默认，带说话人分离）或本地 faster-whisper |
| `summarize.py` | plan / show / add / final / prompt，不做外部模型调用 |
| `publish.py` | 渲染模板 + 原子写入 vault |
| `run.py` | 编排，按缓存跳过已完成步骤 |
| `tests.py` | 46 项回归测试（不联网、不碰真实库） |
| `config.toml` | 模板：`vault.path` 需要你填；ASR 已预填硅基流动 |
| `config.local.toml` | 本机私有覆盖（setup.py 生成，已 gitignore） |
| `templates/note.md` | 笔记模板（`{{token}}` 占位） |
| `templates/summary_prompt.md` | 总结提示词，除「硬性约束」一节外可自己改 |
| `hotwords.txt` | 专有名词词表，一行一个 |
| `install.sh` | 多 harness 安装器 |
| `adapters/` | 各 harness 的安装位置、权限、沙箱与调用方式 |
| `CHANGELOG.md` | 逐版修改日志 |
| `.env.example` | 密钥文件模板（真身 `.env` 已被 git 忽略） |

脚本之间用 stdout 单行 JSON 通信，诊断信息走 stderr，退出码有语义：

| 码 | 含义 | 码 | 含义 |
|---|---|---|---|
| 0 | 成功 | 9 | API 错误（含缺 key） |
| 2 | 输入/参数错误 | 10 | 校验失败 |
| 3 | 配置错误（vault / ASR 没填） | 11 | 目标已存在，拒绝覆盖 |
| 4 | 网络错误 | 12 | 文件系统错误 |
| 5 | 付费内容，已停 | 13 | 隐私/安全红线被触发 |
| 6 | 媒体工具错误 | 7 | 转写失败 |

---

## 关于密钥

- `.gitignore` 排除了 `.env`、`.env.*` 与 `config.local.toml`，只放行 `.env.example`。推送前用 `git status --ignored` 确认一次。
- 读取顺序：`$PODCAST_ENV_FILE` → `<安装目录>/.env` → `~/.config/podscribe/.env`。进程环境变量优先级最高，临时覆盖用 `export SILICONFLOW_API_KEY=...`。解析是内置实现，支持注释、`export` 前缀和引号，不依赖第三方包。
- key 一旦出现在聊天记录或日志里，去服务商后台轮换，删文件不够。
- 仓库里没有真实密钥、真实 vault 路径或用户名。

---

## 优化方向

- 说话人是匿名聚类 ID，不是声纹识别。3 人以上的节目可能错标，跨块对齐的兜底启发式也还有改进空间。
- 云端热词是否被接口真正采用没有实测，目前只在接口报错时自动退回不带热词的请求。
- 本地引擎（faster-whisper）没有说话人分离；长节目在纯 CPU 上很慢。
- 小宇宙走页面 `og:audio` 和 `__NEXT_DATA__`，需要登录态的付费/私密单集拿不到直链，暂时不支持。
- 修订版用 `--revision-suffix` 生成新文件，旧笔记不动；合并到同一篇的能力还没做。
- 目前只验证了中文节目，其它语言没有实测。

---

## 反馈

- 觉得好用的话，点个 **[Star](https://github.com/Xamic-t/Podscribe)** 就行，免费，但对我很有用 ⭐
- 遇到问题、有想法、想加功能：**[提 Issue](https://github.com/Xamic-t/Podscribe/issues)** 或者直接 **[开 PR](https://github.com/Xamic-t/Podscribe/pulls)**，我看到会尽快处理。
- 要改代码，先扫一眼 **[CONTRIBUTING.md](CONTRIBUTING.md)**：PR 流程、代码风格、测试要求、红线都在里面。

---

## 许可

MIT，见 `LICENSE`。
