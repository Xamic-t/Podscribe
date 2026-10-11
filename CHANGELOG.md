# 修改日志

## [2.6.0] · 2026-10-11 · 即装即用：`setup.py` 只问两样 + 跨平台

装上之后不用再开编辑器改文件。`python3 setup.py` 只问两样：Obsidian 库路径、
硅基流动 API Key（`getpass` 隐藏输入）。写进 `config.local.toml` 与 `.env`，
两者都在 `.gitignore` 里，`config.toml` 保持模板原样。

| 改动 | 说明 |
|---|---|
| 新增 `setup.py` | `--check` 只读体检（退出码恒为 0）/ 交互式 / `--vault` / `--key-stdin` / `--verify`（打 `/v1/models` 验 key，网络不通时只提示不判死） |
| 配置叠加 | 同目录的 `config.local.toml` 自动叠加在 `config.toml` 之上；`--config` 由必填变可选（也支持 `$PODCAST_CONFIG`）。显式传参行为不变 |
| 密钥不回显 | key 只落在 `.env`，stdout/日志只出现尾 4 位；POSIX 下 600，Windows 下尝试 `icacls /inheritance:r` |
| 预填 ASR | `cloud_model` / `cloud_base_url` 预填硅基流动的 XingChenASR（**撤销 2.5.0「必须自己填」的决定**，改为开箱可用）。密钥仍然不进仓库，且只发往 `allowed_hosts` 白名单主机 |
| 跨平台 | `os.devnull` 取代硬编码 `/dev/null`；`ffmpeg` / `ffprobe` / `python` 默认留空 → 走 PATH 与当前解释器；`_line()` / `log()` 在 Windows 代码页下自动降级为 `\uXXXX` 转义而不是抛 `UnicodeEncodeError` |
| 模板安全 | `config.toml` 的 `[vault].path` 回到占位符；新增回归测试，禁止真实路径 / 用户名再次混进模板 |
| `install.sh` | 拷贝清单补 `setup.py`；`--dir` 拒绝 `/`、`$HOME`、`/usr` 等危险目标与源目录自身 |
| 文档 | 两份 README 的「填三处配置」改为「配一次：`setup.py`」；`--config` 从示例里去掉；adapters 同步 |

本地迁移：作者的 `config.toml` 里原来的真实库路径已迁到 `config.local.toml`
（同步脚本不拷这个文件），所以本地跑法不变，仓库里的模板从此是干净的。

测试 38 → **46**：新增首次配置全流程、密钥不回显、`.env` 权限、
`config.local.toml` 叠加语义、模板不含私人路径等用例。

验证（在临时目录里模拟的「全新安装」）：

```
刚装好  --check → needs_setup=True, missing=['vault_path','api_key']   （ASR 已预填）
非 tty  交互模式 → NOT_A_TTY（退出码 2），提示改用 --vault / --key-stdin
--vault  → 写入 config.local.toml，config.toml 不动
--key-stdin → 写入 .env（600），stdout 只有尾 4 位
再 --check → needs_setup=False, missing=[]
--verify  → 库可写；沙箱内无外网时返回「网络不通，暂不判断」而不是崩
```

## [2.5.0] · 2026-10-11 · ASR 配置必须自己填（留空 + 硬校验）

发布版 `config.toml` 以前带着默认的模型和接口地址，clone 下来就能跑，
使用者根本不知道自己正在用哪个模型、花的是谁的钱。现在改成**必须自己填**。

| 改动 | 说明 |
|---|---|
| 发布版留空 | `asr.cloud_model = "FILL_ME_ASR_MODEL"`、`asr.cloud_base_url = "FILL_ME_BASE_URL"`；`core.DEFAULTS` 同步改成占位符 |
| 新增 `core.require_asr_config()` | 占位符（含空串、`FILL_ME*`、`PLACEHOLDER*`）一律拦下，抛 `ASR_NOT_CONFIGURED`（退出码 3），报错里写清该填什么、以及 `--engine local` 这条退路 |
| 调用点 | `transcribe.py` 走云端转写前检查；本地引擎不受影响。`run.py` 通过既有的 `raise_from` 透传退出码 3 |
| 必填项从 1 个变 3 个 | `vault.path` + `asr.cloud_model` + `asr.cloud_base_url`；两份 README 的安装步骤改成「填三处配置」并说明两种报错 |
| 同步脚本 | `sync-podscribe-repo.sh` 除 vault 路径外，也把这两项还原成占位符（源目录里是填好的真实值） |

验证：
- 占位符配置跑 `transcribe.py` → `ASR_NOT_CONFIGURED`，退出码 3；
- 同样配置跑 `run.py` → 退出码 3（透传正常）；
- 新增 3 项测试（占位符拒、空串拒、填了放行），测试总数 38，全绿。

顺带修的：用户填了真实 vault 路径后，`test_run_nonzero_when_publish_fails` 失效了
（publish 不再撞 `VAULT_PLACEHOLDER`）。测试改成自己显式把 vault 设成占位符，
不再依赖本机配置填了什么。

## [2.4.2] · 2026-10-11 · 英文 README + 中英切换

| 改动 | 说明 |
|---|---|
| 新增 `README.en.md` | 与中文版同结构、同内容逐节对照的英文版（代码、命令、路径、表格数值一律保持原样） |
| 顶部加语言切换 | 中文版首行 `简体中文（默认） · [English](README.en.md)`；英文版首行 `English · [简体中文](README.md)` |
| 默认仍是中文 | `README.md` 保持中文，英文版走 `README.en.md`（GitHub 上常见的副文件名） |

翻译时保留的限定：ASR 免费那句仍带「免费名单会调整，以定价页为准」；
「实测」照写实测，没有把未验证的项目（云端热词是否被采用、其它语言）写成已验证。
示例里的中文目录名（`播客/`、`逐字稿/`）保持原样，那是工具真实写出的路径。

## [2.4.1] · 2026-10-10 · README 重写 + 补 LICENSE

用 [Humanizer-zh](https://github.com/op7418/Humanizer-zh)（已装到 `~/.workbuddy/skills/humanizer-zh`
与 `~/.claude/skills/humanizer-zh`）的编辑规则重写 README，去掉 AI 味：删掉「不是 X 而是 Y」
式的假对比、宣传式拔高、装饰性粗体，保留全部事实、限定、代码、命令与路径。

| 改动 | 说明 |
|---|---|
| 安装提到最前 | 依赖 → 取代码 → `./install.sh` → 填 `.env` 与 `vault.path` → 一条命令开跑 |
| 补上「怎么工作的」 | 四步流水线，说清哪一步花钱、哪一步由 Agent 做 |
| 补上「用什么模型」 | 表格列出云端 ASR / 本地 faster-whisper / 总结三者的费用；写明**要先申请 API key** |
| 强调 ASR 免费 | 硅基流动定价页把 XingChenASR 系列（含 `XingChenASR-Diarize-V3.0`）标为免费；同时注明免费名单会调整、以官网为准 |
| 补上本地方案 | `--engine local` 走 faster-whisper，不花钱但慢、无说话人分离 |
| 补上总结由谁做 | 会话里的 Agent 完成，超 2 小时可派子代理分块压缩，不调用外部对话模型 |
| 补上提示词可自定义 | `templates/summary_prompt.md` 除「硬性约束」一节外可自己改，附可选开关清单 |
| 补上输出包含原文 | 主笔记是总结，另存整期逐字稿与 VTT 字幕 |
| 「已知限制」改名为「优化方向」 | 语气从「做不到」改成「还可以做」，内容不变 |
| 新增 `LICENSE` | MIT，版权署名写成 `podscribe contributors`，方便替换成你自己的名字 |

## [2.4.0] · 2026-10-10 · 更名为 podscribe

`podcast-to-obsidian` 是说明书不是产品名，正式更名为 **podscribe**
（podcast + scribe：把听到的写成文字）。GitHub 同名未被占用。

| 改了什么 | 说明 |
|---|---|
| skill 名 | `SKILL.md` 的 `name: podscribe`（决定 Claude Code 的 `/podscribe` 命令与 Codex 的 `$podscribe`） |
| 安装目录 | `install.sh` 的 `NAME`，装出来是 `~/.claude/skills/podscribe`、`~/.agents/skills/podscribe` 等 |
| 文档 | README 标题与全部路径、两份 `adapters/*.md` 的安装路径与调用示例 |
| env 备份位置 | 新增 `~/.config/podscribe/.env`；旧的 `~/.config/podcast-to-obsidian/{.env,env}` 仍作为兼容项保留 |

**故意没改的**：`runtime.cache_root` 仍是 `~/.cache/podcast-to-obsidian`。
job 目录里存着音频与逐字稿的绝对路径，改缓存根目录会让已转写的历史 job 全部失效。
想换就在 `config.toml` 里改，并自己把目录搬过去。

## [2.3.1] · 2026-10-10 · 多 harness 适配（Claude Code / Codex）

| 改动 | 说明 |
|---|---|
| `SKILL.md` frontmatter 标准化 | 只保留规范的 `name` / `description` / `allowed-tools`，去掉 WorkBuddy 专属的 `agent_created`（Claude Code 历史上对未知键报过 unexpected key；Codex 会忽略多余键）。`description` 控制在 200 字符内，同时满足 Codex（≤500）与 Claude Code（≤1024） |
| 路径占位符 | `SKILL.md` 里的 `SKILL=` 改为 `@SKILL_DIR@`，由 `install.sh` 替换成真实安装路径；手动拷贝的场景给了排查提示 |
| `install.sh` | 新增：`--harness claude/codex/workbuddy/all`、`--project`、`--dir`。拷贝本体 + 替换路径 + 按 harness 增删 frontmatter；**不覆盖已存在的 `config.toml` 与 `.env`**，可重复执行 |
| Codex 双位置 | 装到 `~/.agents/skills/`（文档口径）并软链 `~/.codex/skills/`（社区口径），Codex 扫描时跟随符号链接 |
| `adapters/claude-code.md` | 安装位置、`/skills` 调用、Bash 授权白名单写法、长节目用 `general-purpose` 子代理 |
| `adapters/codex.md` | 安装位置、`[features] skills = true` 开关、`$skill-name` 调用、**沙箱会挡写 vault 的三种解法**、`name`≤100 / `description`≤500 限制、`codex exec --full-auto` |
| 措辞 | 「子 Agent」改成中性的「子代理」，并说明没有子代理能力的宿主可顺序读块 |

验证（临时 HOME 实跑）：
- `install.sh` 装出 Claude Code + Codex 两份，软链正确，`.env` 未被带走；
- 安装副本里 `SKILL=` 已替换成绝对路径，无残留占位符，frontmatter 无 `agent_created`；
- 从安装副本直接 `python3 tests.py` → 35 项全绿（证明自包含）；
- 改过 `config.toml` 后重跑安装，配置被保留（幂等）。

## [2.3.0] · 2026-10-10 · 第三轮复核修复（N1–N7）

第三轮复核在 2.2.0 基础上提出 7 项收尾问题。全部处理，测试从 28 项扩到 **35 项，全绿**。

### 会误删 / 会静默成功（立刻修）

| # | 问题 | 改动 | 验证 |
|---|---|---|---|
| N1 | `run.py audio --purge --job-dir /tmp/copy/xm-1` **静默只取最后一段**，会去清缓存里的同名 job（越界路径不报错） | 改用 `C.resolve_job_dir(cfg, ..., must_exist=False)`：越界抛 `JOB_NOT_IN_CACHE`（退出码 2） | 实测退出码 2，归档音频未被误删 |
| N2 | job 目录不存在时没有明确错误码，后半程才撞 `KeyError` / `FileNotFoundError` | `resolve_job_dir()` 新增 `must_exist` 参数（默认 `True`），不存在抛 `JOB_NOT_FOUND`（退出码 2）。清理类命令显式传 `False` | 实测 `status --job-dir xm-404` → `JOB_NOT_FOUND`，退出码 2 |
| N6 | `audio --list` 会显示 `pieces` 残留，但**没有任何命令能清掉它** | 新增 `purge_pieces()` 并入 `audio_purge`（同一套范围约束）；没匹配到任何文件时给出 warning，不静默报成功 | 实测残留 `pieces/` 被清且不影响别的一期；`--all` 清全部 |

### 命名 / 链接 / 变量

| # | 问题 | 改动 |
|---|---|---|
| N3 | 保留名（音频归档目录 `音频库`）可以当 job id 用；`./name` 与 `name` 走不同分支 | 新增 `core.reserved_job_names()`，`job_dir()` 拒绝保留名（`RESERVED_JOB_ID`）；`resolve_job_dir` 先剥掉 `./` 前缀与结尾斜杠再判定 |
| N4 | VTT 用「Markdown 相对链接」指向 `.vtt`，在 Obsidian 里会被当成笔记名解析，打不开 | 改成 wikilink 附件链接 `[[播客/逐字稿/xxx.vtt\|WebVTT 字幕]]`，删掉不再需要的 `urllib.parse` 导入 |
| N5 | `publish.render()` 里 `chapters` 先存渲染好的表格字符串、循环里又被赋成列表 | 循环内改用 `chapter_list`，两个含义不再共用一个名字 |

### 清理

| # | 问题 | 改动 |
|---|---|---|
| N7 | `tests.py` 未使用的 `import os` | 删除 |

### 文档

- SKILL.md：`--job-dir` 规则补上 `JOB_NOT_FOUND` / `RESERVED_JOB_ID` 与 `./name` 等价说明。
- SKILL.md：音频归档一节写明 `--purge` 会连同 `pieces/` 残留一起清，范围写错会给 warning。

### 测试（28 → 35）

新增 7 项（新类 `Round3Fixes`）：
- purge 的越界 `--job-dir` 被拒（N1）
- job 目录不存在报 `JOB_NOT_FOUND` 且退出码 2（N2）
- 保留名 `音频库` / `./音频库` / 绝对路径三种写法都被拒（N3）
- `./xm-9` 与 `xm-9` 与绝对路径解析结果一致（N3）
- `--purge --job-dir` 只清该期 `pieces`，`--all` 清全部（N6）
- 清理范围没匹配到时返回 `warnings`（N6）
- 渲染产物用 wikilink 引 VTT，且章节变量未被段落循环污染（N4 / N5）

运行：`python3 tests.py` → `Ran 35 tests ... OK`

### 发布准备（同一版本内的脱敏）

准备推 GitHub 时扫出 3 处机器专属路径（含用户名），一并处理：

- `config.toml` / `core.py` DEFAULTS 的 `runtime.python` 改成 `~` 开头；
  `transcribe.py` 读取时补 `expanduser()`（原来只认绝对路径）。
- `SKILL.md` 示例里的 `PY=` 原来是本机的绝对路径，改成 `PY=python3`。
- 新增 `core.tool_path()`：配置里的 `ffmpeg` / `ffprobe` 绝对路径不存在时，
  回落到 PATH 里的同名命令（换台机器不会因为 Homebrew 前缀不同就报 TOOL_MISSING）。
- `.gitignore` 补 `*.pyc`、`config.local.toml`（填了真实 vault 路径的私有副本）、
  `.DS_Store`、编辑器目录；`.env` 与 `.env.*` 仍被忽略，只放行 `.env.example`。
- 新增 `README.md`：面向 GitHub 读者，讲清解决什么问题、特点、用法、安全设计与限制。

推送前复扫：仓库内不含密钥、真实 vault 路径或用户名（`config.toml` 的 `vault.path`
仍是占位符）。

## [2.2.0] · 2026-10-08 · 第二轮评审修复（R1–R13）

第二轮复查确认 20/21 项已修好，新提出 13 项。全部处理，测试从 20 项扩到 **28 项，全绿**。

### 会丢数据 / 会让上层误判（立刻修）

| # | 问题 | 改动 | 验证 |
|---|---|---|---|
| R1 | `run.py audio --purge` 不给范围时**清空整个归档**（代码在两种用法之外默认全清） | 无 `--all` 且无 `--job-dir` 时抛 `NEED_SCOPE`（退出码 2）；归档目录强制落在缓存根之内，否则 `ARCHIVE_OUT_OF_CACHE` | 实测退出码 2、文件保留 |
| R2 | publish 失败时 run.py 打印 `ok:false` 但**退出码是 0**，抵消了第一轮的 P7 | 改为 `raise_from(..., hint=...)` 抛出，退出码透传（VAULT_PLACEHOLDER → 3） | 实测退出码 3 + hint |
| R3 | `summarize add --json-file -` 把 stdin 读两次，第二次拿到空串 | payload 只读一次，存成变量再分发 | 新增 stdin 用例 |

### 安全 / 误删 / 磁盘

| # | 问题 | 改动 | 验证 |
|---|---|---|---|
| R4 | RSS 的 DOCTYPE 守卫只看前 2000 字符，可用长注释绕过并让实体展开 | 改为全量扫描（10 MB 扫一遍很便宜） | 用例：2500 字符前导 + DOCTYPE → `UNSAFE_XML` |
| R5 | `--purge --job-dir xm-111` 用前缀匹配，会连带删掉 `xm-1111` | 改为文件名主干精确匹配（`f.stem != job_id`），并跳过目录 | 用例：两者共存时只删前者 |
| R6 | `pieces/` 每次转写新建、从不清理，同一期音频在缓存里存两份 | 成功路径 `shutil.rmtree(pieces)`；失败保留便于排查；`audio --list` 顺带显示残留占用 | 实测重跑后 pieces 消失 |

### 一致性与文档

| # | 问题 | 改动 |
|---|---|---|
| R7 | `--job-dir` 静默只取路径最后一段（`/tmp/copy/xm-1` 被当成缓存里的同名目录） | 新增 `core.resolve_job_dir()`：接受裸 job id 或缓存根内绝对路径，其它位置报错（退出码 2）；transcribe / summarize / publish / run 四个脚本共用 |
| R8 | publish 调用 core 的私有函数 `_target_exists` | 加公开别名 `target_exists = _target_exists` |
| R9 | `schema_version = "abc"` 走兜底分支，报成 `UNEXPECTED`(12) | 非整数抛 `CONFIG_VERSION_INVALID`（退出码 3） |
| R10 | 相似度预筛 `长度比 < 0.3 → 0` 有隐含前提 | 注释写明：调用方阈值须 > 0.46（当前 0.55 / 0.85 满足） |
| R11 | 归档遇到同名会覆盖 | 明确定义为「只保留最新一份」并写进 SKILL.md（同一期音频内容相同，无信息损失） |
| R12 | SKILL.md 仍写「优先用 python-dotenv」；示例只有 `--json` | 改为「内置解析、零依赖」，补 `--json-file` 用法与 `--job-dir` 规则 |
| R13 | `tests.py` 未用的 `import json`；transcribe 里 `errors` 混装去重计数 | 清理；统一改名 `notes`，块失败项加 `[块失败]` 前缀 |

### 附带发现并修掉的

- `publish.render()` 对缺失字段（`key_points` / `chapters`）会抛 `KeyError`，
  经兜底分支报成 `UNEXPECTED`。改为 `.get(..., [])` 容错，让真正的配置错误
  （如 vault 未填）能正常浮出来。
- `run.py audio --list` 增加 `pieces` 占用统计，方便发现历史残留。

### 测试（20 → 28）

新增 8 项：
- `purge` 无范围抛 `NEED_SCOPE`
- `purge` 精确匹配（不误伤同类名）
- 归档目录越出缓存根被拒
- `resolve_job_dir` 接受/拒绝的三种输入
- RSS「长注释 + DOCTYPE」绕过样本被拒
- `schema_version` 非数字的错误码
- `add --json-file -` 读 stdin
- publish 失败时 `run.py` 退出码非 0

运行：`python3 tests.py`

## [2.1.0] · 2026-10-08 · 按外部代码审查做点对点修复

审查对象为 2.0.0（6 脚本结构）。共处理 21 项问题 + 死代码清理。
基线测试从 12 项扩到 **20 项，全部通过**。

### 第一批：安全

| # | 问题 | 改动 | 位置 |
|---|---|---|---|
| P1 | 小宇宙 eid 来自远端且未清洗，可构造 `../../` 逃出缓存目录写文件 | 新增 `_SANE_ID` 校验，不合规则用 URL 哈希兜底 | download.py |
| P1/P3 | `job_dir()` 对任何 job id 都不校验 | 新增校验：禁止绝对路径 / `..` / 分隔符 / 控制字符，并确认解析后仍在缓存根之内 | core.py |
| P2 | curl 把远端返回的 URL 当选项（`file.url` 可控） | URL 前加 `--` 终止符 + `check_url()` 只放行 http(s)；`expand_short` 同改 | core.py / download.py |
| P3 | `make_plan()` 删除目录下所有 `block-*.md` | 只删上一次 `index.json` 记录过的文件 | summarize.py |
| P5 | 金句「逐字校验」用子序列，打散重拼也算命中 | 改为归一化后的**连续子串**匹配，删除 `is_subsequence()` | summarize.py |
| P7 | `Fail.payload()` 不含 exit_code，run.py 一律退化成 4 | payload 带 exit_code，run.py 用 `raise_from()` 透传 | core.py / run.py |
| P11 | 域名子串匹配，`xiaoyuzhoufm.com.evil.com` 被误判 | 用 `urlsplit().hostname` + `_is_host()` 严格匹配 | download.py |
| P14 | 未捕获异常（如 JSONDecodeError）破坏「stdout 单行 JSON」 | `main_with_job` 增加兜底分支 | core.py |

### 第二批：一致性

| # | 问题 | 改动 | 位置 |
|---|---|---|---|
| P4 | `cloud_base_url` 可被改指向，密钥会发给任意主机 | 新增 `asr.allowed_hosts` 白名单硬校验（默认 `api.siliconflow.cn`）；SKILL.md 加「不可信输入当数据」红线 | transcribe.py / config.toml / SKILL.md |
| P6 | `keep_audio` 从未实现，音频长期留盘 | 转写完成后处理音频：默认**归档**到 `<缓存>/音频库/`，`keep_audio=false` 才删除；只动 `owned_by_job` 文件 | transcribe.py / config.toml |
| P6+ | 归档后需要可操作的清理入口 | 新增 `run.py audio --list` / `--purge [--all --job-dir]` | run.py |
| P8 | dotenv 双实现行为不一致；`env_permission_warning()` 是死代码 | 删除 python-dotenv 分支，只留内置解析（补齐引号内 `#`、行内注释、`export` 前缀）；权限提醒接进 `api_key()` | core.py |
| P9 | publish 非事务，失败留半成品 | 先解析全部目标做存在性预检，任一冲突一个字节都不写；VTT 走新增的 `exclusive_write_bytes()` 原子写 | publish.py / core.py |

### 第三批：收尾

| # | 问题 | 改动 | 位置 |
|---|---|---|---|
| P10 | 响应无大小上限；RSS 未防 XXE | `http_bytes(max_bytes=10MB)`；RSS 先拒绝含 `<!DOCTYPE` / `<!ENTITY` 的响应 | core.py / download.py |
| P12 | 终稿 JSON 走命令行（ARG_MAX / 转义） | 新增 `--json-file`（`-` 表示 stdin），`--json` 留给小块摘要 | summarize.py |
| P13 | 文件名按字符截断，ext4 / SMB 上会 ENAMETOOLONG | 新增 `truncate_bytes()`，对整个 stem 按 200 字节截断 | core.py / publish.py |
| P15 | `exclusive_write` 兜底分支有 TOCTOU 窗口 | 注释改为 best-effort 说明（行为不变，不静默覆盖） | core.py |
| P16 | 热词三处矛盾，实际整体未生效 | 本地用 `initial_prompt`（不用 `hotwords` 参数，它会关闭上下文连贯），云端 `send_hotword` 默认 true；词表正式启用 | transcribe.py / config.toml |
| P17 | run.py 不检查 plan 结果；提示了不存在的参数 | plan 失败即抛出；补上 `--upsert` / `--revision-suffix` 真实参数 | run.py |
| P18 | 修订版双链指向错误；VTT 链接只转义空格 | 双链带上后缀；链接用 `urllib.parse.quote` 全量转义 | publish.py |
| P19 | frontmatter 空值写成 `""`；超长音频静默截断；errors 混装 | `yaml_escape(None)` → `null`；超 200 块时给出 warning | core.py / transcribe.py |
| P20 | 说话人兜底启发式未说明 | 写入 SKILL.md「已知限制」，说明 3 人以上可能错标 | SKILL.md |
| P21 | `config.schema_version` 无人读取；DEFAULTS 嵌套分支不可达 | 版本不符直接报错；简化 DEFAULTS 合并逻辑 | core.py |
| — | 死代码 | 删 `import re`、`per_chunk_speakers` / `local_map`、`urllib.error/request`、重复 `import hashlib`、未用的 `EXIT_MODEL_MISSING` | 多文件 |
| — | 性能 | `similarity()` 加长度比预筛，跳过明显不相似的比较 | core.py |

### 测试

新增 8 个用例，共 20 个：

- `job_dir` 拒绝越界（P1/P3）
- `curl_cmd` 的 `--` 位置 + `check_url` 拒绝非 http(s)（P2）
- `verify_quote` 拒绝打散的句子（P5）
- `Fail.payload()` 含 exit_code（P7）
- `_parse_dotenv` 处理引号内 `#` 与行内注释（P8）
- `truncate_bytes` 对中文按字节截断（P13）

运行：`python3 tests.py`

### 保留不动的资产

- `exclusive_write` / `exclusive_write_bytes` 的「先写 partial 再 link」无覆盖语义
- `safe_target` 的路径片段校验与符号链接检测
- `safety.scan_vault` / `read_existing_notes` 强制 false 护栏
- 缓存即断点续跑（metadata → transcript → summary → receipt）
- `raw/` 分块原始返回缓存（避免重复计费）
- 密钥只从 env / .env 读取，绝不进代码、日志、笔记
- stdout 单行 JSON + stderr 诊断

---

## [2.0.0] · 2026-10-08 · 重构为 6 文件结构

- 36 个文件 → 11 个：`core / download / transcribe / summarize / publish / run`
- 转写改用 `XingChenASR-Diarize-V3.0`：句级时间戳 + 说话人分离，20 分钟大块上传
- 跨块说话人对齐：时间重叠证据 + 同块互斥 + 复用空闲标签
- 支持喜马拉雅 / 小宇宙 / 通用 RSS / 音频直链 / 本地文件
- 新增 `publish_strategy` 与 `--revision-suffix`
- 删除接口版 Map-Reduce（又慢又花钱），改为会话模型 + 可选子 Agent 压缩
- 新增可编辑总结提示词 `templates/summary_prompt.md`
- 密钥改到标准 `.env`（600）+ `.gitignore`

## [1.0.0] · 2026-10-06 · 初版

- 13 脚本 + 28 单测，喜马拉雅单集 → 本地 faster-whisper → 会话总结 → Obsidian
- 隐私红线：不扫库、不读已有笔记、不覆盖
