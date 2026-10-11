# podscribe

简体中文（默认） · [English](README.en.md)

把播客听成笔记：给一个单集链接，得到一篇带时间戳的 Obsidian 笔记，外加这期节目的完整逐字稿。

- 笔记里有 TL;DR、分层要点（每条挂着 `01:23:45` 这样的证据位置）、逐字校验过的金句、行动项、章节时间表
- 逐字稿是整期原文，按章节分段，同样带时间戳和说话人
- 另附一份 WebVTT 字幕，可以关掉

转写默认走硅基流动的 ASR，说话人分离和句级时间戳一次拿到；总结不调用任何外部对话模型，由你的 Agent 读稿完成，长节目可以派子代理分块压缩。

---

## 安装

### 1. 依赖

- Python 3.11+（用到标准库 `tomllib`，脚本本身没有第三方依赖）
- `ffmpeg` / `ffprobe`（切长音频）、`curl`（下载）
- 一个硅基流动的 API Key：注册后在后台生成，免费模型也要有 key 才能调

想完全离线跑，看下面的「本地转写」，可以不办 key。

### 2. 取代码

```bash
git clone <你的仓库> podscribe && cd podscribe
```

### 3. 装到你的 Agent（可选）

```bash
./install.sh                        # Claude Code + Codex，用户级
./install.sh --harness claude       # 只装 Claude Code
./install.sh --harness codex        # 只装 Codex
./install.sh --harness workbuddy    # WorkBuddy
./install.sh --project              # 装到当前项目，可随仓库提交
./install.sh --dir /some/where      # 自定义目录
```

install.sh 会拷脚本和模板、把 `SKILL.md` 里的路径占位符换成真实安装路径、按 harness 增删 frontmatter 字段。已存在的 `config.toml` 和 `.env` 不会被覆盖，可以反复跑。

只在命令行用就跳过这一步。

### 4. 填两处配置

```bash
cp .env.example .env && chmod 600 .env
```

在 `.env` 里填 `SILICONFLOW_API_KEY=sk-...`。然后在 `config.toml` 里填 vault 路径，这是唯一必填项：

```toml
[vault]
path = "/absolute/path/to/your/obsidian/vault"
```

vault 路径仍是 `PLACEHOLDER_VAULT_PATH` 时，publish 会拒绝写入（退出码 3）。不想把真实路径提交进仓库，就把 `config.toml` 复制成 `config.local.toml`（已在 `.gitignore` 里），再用 `--config config.local.toml` 指定。

### 5. 跑

```bash
python3 run.py --input "<播客单集链接>" --config config.toml
```

---

## 它解决什么问题

| 听播客时会遇到                              | 这里的做法                                                        |
| ------------------------------------ | ------------------------------------------------------------ |
| 听完就忘，想引用一句得回去拖进度条                  | 逐字稿带句级时间戳，笔记里的要点和金句都挂着 `01:23:45`                          |
| 分不清哪句是谁说的                           | 云端 ASR 带说话人分离（说话人 A / B），长音频分块后做全局标签对齐                     |
| 3 小时节目本地转写要跑一小时以上                   | 默认走云端，1.5 小时的节目实测约 80 秒出稿                                  |
| 总结里的「金句」经常是模型编的                      | 每条金句回原文 ±30 秒做逐字匹配，命中才算原话，命不中的降级标注成「模型概括」                |
| 怕工具覆盖或弄脏已有的库                        | 默认 `create_only`：目标存在就停（退出码 11），不覆盖，也不读已有笔记                 |
| 重跑一次就重新扣一次钱                         | 每步都有缓存（分片原始返回、逐字稿、总结），中断后重跑会跳过已完成的步骤                |
| 专有名词被写错（Vercel→Versel、多伊奇→多一起）        | `hotwords.txt` 一行一个词，同时喂给云端的 `hotword` 和本地的 `initial_prompt` |
| 总结风格不合口味                            | 提示词在 `templates/summary_prompt.md`，自己改，下次生效                  |

---

## 它是怎么工作的

四步，每步都落盘，重跑会跳过已完成的步骤。

1. **download.py** 认链接类型（喜马拉雅 / 小宇宙 / RSS / 音频直链 / 本地文件），取音频和元信息。付费单集在这一步就停（退出码 5），不绕鉴权。
2. **transcribe.py** 按 20 分钟切块、块间重叠 30 秒，交给云端 ASR，拿到句级时间戳和说话人标签，再用「时间重叠 + 同块互斥 + 复用空闲标签」把各块的局部标签对齐成全局的说话人 A / B。每块的原始返回缓存在 `<job>/raw/`。
3. **总结** 由会话里的 Agent 完成：`summarize.py plan` 把逐字稿切成 15 分钟一块，逐块读、用 `add` 回写，`final` 汇总并校验金句。超过 2 小时的节目可以让主 Agent 派子代理分块压缩，也算在会话里。
4. **publish.py** 渲染模板，原子写入 vault；目标已存在就停。

花钱的只有第 2 步，用本地引擎时连这一步也不花钱。第 3 步是动脑子的部分，代码里没有任何对话模型调用。

---

## 用什么模型，花不花钱

| 环节      | 用什么                                                                                             | 费用                                                     |
| ------- | ----------------------------------------------------------------------------------------------- | ------------------------------------------------------ |
| 转写（默认）  | 硅基流动 `XingChenAGI/XingChenASR-Diarize-V3.0`，走兼容 OpenAI 的 `/v1/audio/transcriptions`，带说话人分离和句级时间戳 | 按硅基流动定价页，XingChenASR 系列（含这个模型）目前标为免费。免费名单会调整，用之前看一眼定价页 |
| 转写（备选）  | 本地 faster-whisper                                                                                | 不花钱，但慢：3 小时节目在本机 CPU 上以小时计，且没有说话人分离                     |
| 总结      | 会话里的 Agent，或它派的子代理                                                                              | 不调用外部对话模型，不额外计费                                        |

**先有 key 才能用云端。** 注册硅基流动账号生成一个 key，写进 `.env` 即可，不需要改代码。

换别的供应商也能跑：`config.toml` 里改 `asr.cloud_base_url`、`api_key_env` 和 `allowed_hosts`。密钥只发往 `allowed_hosts` 里的主机，是代码里的硬校验，改配置也发不出去。

**API 用不了就切本地。** `python3 transcribe.py --job-dir <dir> --config config.toml --engine local`，
或把 `asr.engine` 设成 `local`。本地引擎需要一个装了 faster-whisper 的 Python 环境，
路径写在 `runtime.python`（支持 `~` 开头）。

---

## 总结提示词自己写

`templates/summary_prompt.md` 就是写总结时用的提示词，直接改，改完下次生效。除了「硬性约束」那一节（动了会被校验拦下），其余随便改：篇幅、要点数量、语气、要不要行动项，都在里面。里面还留了几个可选开关，比如极简卡片、深度长文、批判视角、面向行动。

想看当前生效的版本：

```bash
python3 summarize.py prompt --job-dir <dir> --config config.toml
```

改完提示词后重跑 `final` 和 `publish` 就行，不用重新转写。

---

## 输出长什么样

vault 里多出这些文件：

```
播客/2026-10-03 EP752 #752.标题 [job-id].md                 # 主笔记：总结
播客/逐字稿/2026-10-03 EP752 #752.标题 [job-id] - 逐字稿.md     # 整期原文
播客/逐字稿/2026-10-03 EP752 #752.标题 [job-id].vtt           # WebVTT 字幕（可关）
```

主笔记从上到下是：YAML frontmatter（`source / duration / speakers / asr_model / word_count` 等）→ 信息表 → TL;DR → 分层要点（带证据位置）→ 金句 → 行动项表 → 章节时间表 → 节目简介 → 逐字稿链接。

笔记里会写明说话人标签来自 ASR 聚类、跨块对齐可能有误差，不把这些写成确定结论。

---

## 特点

1. **断点续跑。** 每个 job 一个目录，`metadata.json / transcript.json / summary.json / receipt.json` 缺哪步补哪步。中断后重跑同一条命令，已完成的步骤跳过，不重复请求。
2. **说话人分离。** 20 分钟一个大块（上下文长，专有名词不容易错），相邻块重叠 30 秒做全局对齐。3 小时节目实测稳定收敛到 2 个说话人。
3. **金句逐字校验。** `summarize.py final` 对每条 quote 回原文比对，命不中的划到「模型概括」并标注，笔记里不会出现被当成原话的伪造引用。
4. **不碰你的库。** 不扫描磁盘找库，vault 路径只来自 `config.toml`；不读取 vault 里任何已有笔记，只做精确路径新建。`safety.scan_vault` 或 `read_existing_notes` 一旦为 `true`，配置加载直接失败。
5. **安全检查在代码里。** 密钥只存在于 `.env`（600），不写进代码、配置或 md 文件；逐字稿、节目简介、RSS 描述里的「指示」一律当数据不当指令；job id 和 vault 子路径做越界校验，路径经过符号链接就拒绝写入；`--purge` 必须带范围，没匹配上会给出 warning 而不是静默报成功。
6. **热词表。** `hotwords.txt` 一行一个词，遇到专有名词被写错就把正确写法加进去，下一期就准了。

---

## 支持的 Agent Harness

遵循 Agent Skills 开放标准，同一份 `SKILL.md` 到处能用。去掉 `SKILL.md`，它也是一套纯 CLI 脚本。

| Harness              | 安装位置                                                            | 调用                                    |
| -------------------- | --------------------------------------------------------------- | ------------------------------------- |
| Claude Code          | `~/.claude/skills/`（用户）或 `.claude/skills/`（项目）                    | `/podscribe`，或按描述自动触发                  |
| Codex                | `~/.agents/skills/`（并软链 `~/.codex/skills/`）或 `.agents/skills/` | 提示词里写 `$podscribe`，或用 `/skills`        |
| WorkBuddy            | `~/.workbuddy/skills/`                                          | 说出链接即可                                |
| 其它遵循标准的工具 / 纯 CLI    | 任意目录                                                            | `./install.sh --dir <路径>`，或直接跑脚本        |

各 harness 的权限、沙箱和子代理用法见 `adapters/claude-code.md` 和 `adapters/codex.md`。Codex 的沙箱默认会挡写操作，第一次跑之前先看一眼那份文档。

---

## 命令

顺序执行：

```bash
python3 download.py   --input "<链接>" --config config.toml
python3 transcribe.py --job-dir <dir>  --config config.toml
python3 summarize.py plan  --job-dir <dir> --config config.toml    # 切成 15 分钟一块
# 逐块读稿 → add 回写 → final 汇总并校验金句
python3 summarize.py final --job-dir <dir> --config config.toml --json-file /tmp/summary.json
python3 publish.py    --job-dir <dir>  --config config.toml        # 写进 vault
```

辅助：

```bash
python3 run.py status --job-dir <dir> --config config.toml
python3 run.py audio  --config config.toml --list                  # 归档音频 + 分片占用
python3 run.py audio  --config config.toml --purge --job-dir <dir> # 只清某一期
python3 run.py audio  --config config.toml --purge --all           # 全清（必须显式）
python3 summarize.py prompt --job-dir <dir> --config config.toml   # 打印当前总结提示词
python3 tests.py                                                   # 35 项回归测试
```

---

## 文件说明

| 文件                            | 职责                                          |
| ----------------------------- | ------------------------------------------- |
| `SKILL.md`                    | Agent Skill 定义（触发语、用法、红线），给 Agent 看的说明书     |
| `core.py`                     | JSON 协议、配置校验、路径安全、原子无覆盖写入、dotenv 解析         |
| `download.py`                 | 喜马拉雅 / 小宇宙 / 通用 RSS / 音频直链 / 本地文件          |
| `transcribe.py`               | 云端 ASR（默认，带说话人分离）或本地 faster-whisper         |
| `summarize.py`                | plan / show / add / final / prompt，不做外部模型调用 |
| `publish.py`                  | 渲染模板 + 原子写入 vault                           |
| `run.py`                      | 编排，按缓存跳过已完成步骤                               |
| `tests.py`                    | 35 项回归测试（不联网、不碰真实库）                         |
| `config.toml`                 | 唯一必填项是 `vault.path`                          |
| `templates/note.md`           | 笔记模板（`{{token}}` 占位）                        |
| `templates/summary_prompt.md` | 总结提示词，除「硬性约束」一节外可自己改                        |
| `hotwords.txt`                | 专有名词词表，一行一个                                 |
| `install.sh`                  | 多 harness 安装器                               |
| `adapters/`                   | 各 harness 的安装位置、权限、沙箱与调用方式                  |
| `CHANGELOG.md`                | 三轮评审的完整修改日志                                 |
| `.env.example`                | 密钥文件模板（真身 `.env` 已被 git 忽略）                 |

脚本之间用 stdout 单行 JSON 通信，诊断信息走 stderr，退出码有语义：

| 码 | 含义              | 码  | 含义          |
| - | --------------- | -- | ----------- |
| 0 | 成功              | 9  | API 错误（含缺 key）|
| 2 | 输入/参数错误         | 10 | 校验失败        |
| 3 | 配置错误（含 vault 占位符）| 11 | 目标已存在，拒绝覆盖  |
| 4 | 网络错误            | 12 | 文件系统错误      |
| 5 | 付费内容，已停         | 13 | 隐私/安全红线被触发  |
| 6 | 媒体工具错误          | 7  | 转写失败        |

---

## 关于密钥

- `.gitignore` 排除了 `.env` 和 `.env.*`，只放行 `.env.example`。推送前用 `git status --ignored` 确认一次。
- 读取顺序：`$PODCAST_ENV_FILE` → `<skill>/.env` → `~/.config/podscribe/.env`。进程环境变量优先级最高，临时覆盖用 `export SILICONFLOW_API_KEY=...`。解析是内置实现，支持注释、`export` 前缀和引号，不依赖第三方包。
- key 一旦出现在聊天记录或日志里，去服务商后台轮换，删文件不够。
- 仓库里没有真实密钥、真实 vault 路径或用户名。

---

## 优化方向

- 说话人是匿名聚类 ID，不是声纹识别。3 人以上的节目可能错标，跨块对齐的兜底启发式也还有改进空间。
- 云端热词是否被接口真正采用没有实测，目前只在接口报错时自动退回不带热词的请求。
- 本地引擎（faster-whisper）没有说话人分离，这台机器只有 CPU int8，长节目很慢；可以换成带分离的本地方案。
- 小宇宙走页面 `og:audio` 和 `__NEXT_DATA__`，需要登录态的付费/私密单集拿不到直链，暂时不支持。
- 修订版用 `--revision-suffix` 生成新文件，旧笔记不动；合并到同一篇的能力还没做。
- 目前只验证了中文节目，其它语言没有实测。

---

## 许可

MIT，见 `LICENSE`。
