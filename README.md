# 播客 → Obsidian · podcast-to-obsidian

把一期播客链接变成一篇能长期检索的 Obsidian 笔记：
**下载 → 转写（句级时间戳 + 说话人分离）→ 分层总结 → 落盘**。

既是一个 **Agent Skill**（给我一个链接，我自己跑完并写总结），
也是一套**纯 CLI 脚本**（不依赖任何 Agent 框架，`python3` 就能跑）。

```bash
python3 run.py --input "https://www.xiaoyuzhoufm.com/episode/xxxx" --config config.toml
```

---

## 它解决什么问题

| 听播客时的真实痛点 | 这里的做法 |
|---|---|
| 听完就忘，想引用一句得回去拖进度条 | 逐字稿带**句级时间戳**；笔记里的每个要点、每句金句都挂着 `01:23:45` |
| 谁说的分不清 | 云端 ASR 带**说话人分离**（说话人 A / B），长音频分块后做全局标签对齐 |
| 3 小时节目本地转写要跑 1 小时以上 | 默认走云端，1.5 小时的节目实测**约 80 秒**出稿（本地 faster-whisper 仍是备选） |
| 总结里的「金句」经常是模型编的 | 每条金句都回原文 ±30 秒做**逐字匹配**，命中才算原话，命中不了降级标注成「模型概括」 |
| 怕工具覆盖/弄脏已有的库 | 默认 `create_only`：目标存在就停（退出码 11），**不覆盖、不读取已有笔记** |
| 重跑一次就重新扣一次钱 | 每一步都有缓存（分片原始返回、逐字稿、总结），**缓存即断点续跑** |
| 专有名词被写错（Vercel→Versel、多伊奇→多一起） | `hotwords.txt` 一行一个词，同时喂给云端的 `hotword` 与本地的 `initial_prompt` |
| 总结风格不合口味 | 提示词在 `templates/summary_prompt.md`，**你自己改**，下次生效 |

---

## 特点

**1. 缓存即断点续跑。** 每个 job 一个目录，
`metadata.json / transcript.json / summary.json / receipt.json` 缺哪步补哪步。
中断后重跑同一条命令，已完成的步骤直接跳过，不重复调用、不重复计费。

**2. 说话人分离是真的能用。** 20 分钟一个大块（上下文够长，专有名词不容易错），
相邻块重叠 30 秒，用「时间重叠 + 同块互斥 + 复用空闲标签」把各块的局部标签
对齐成全局的「说话人 A / B」。3 小时节目实测稳定收敛到 2 个说话人。

**3. 不编造原话。** `summarize.py final` 会对每条 quote 做逐字校验，
不命中就划到「模型概括」并明确标注——笔记里不会出现被当成原话的伪造引用。

**4. 总结不额外花钱。** 读稿与综合都由**会话模型（你的 Agent）**完成，
代码里没有任何外部对话模型调用。唯一会花钱的是**转写**（按音频时长计费）。
（曾经有过接口版 Map-Reduce，实测又慢又烧钱，已彻底删除。）

**5. 隐私红线写进代码。**
- 不扫描磁盘找库：vault 路径只来自 `config.toml`，是占位符就拒绝写入；
- 不读取 vault 里任何已有笔记，只做精确路径新建；
- `safety.scan_vault / read_existing_notes` 一旦为 `true`，配置加载直接失败。

**6. 安全是硬校验，不是注释。**
- 密钥只存在于 `.env`（600），**不写进代码、配置或任何 md 文件**；
- 密钥只发往 `asr.allowed_hosts` 里的主机，**改配置也发不出去**；
- 逐字稿 / 节目简介 / RSS 描述里的「指示」一律当数据，不当指令（提示注入防护）；
- job id、vault 子路径全部做越界校验，符号链接拒绝写入；
- `--purge` 必须带明确范围，范围没匹配上会 warning 而不是静默报成功；
- 付费节目在下载阶段就停（退出码 5），不绕过鉴权。

**7. 来源够杂。** 喜马拉雅（含付费检测）、小宇宙、任意 RSS、音频直链、本地音频文件。

---

## 快速开始

### 依赖

- Python **3.11+**（用到标准库 `tomllib`，零第三方依赖）
- `ffmpeg` / `ffprobe`（切分长音频）
- `curl`（下载）
- 一个兼容 OpenAI `/v1/audio/transcriptions` 的服务（默认 SiliconFlow）与它的 API Key

### 配置

```bash
git clone <你的仓库> podcast-to-obsidian && cd podcast-to-obsidian
cp .env.example .env && chmod 600 .env
# 编辑 .env，填入：SILICONFLOW_API_KEY=sk-...
```

然后改 `config.toml` 里**唯一需要你填的东西**：

```toml
[vault]
path = "/absolute/path/to/your/obsidian/vault"
```

其他默认值可以直接用。不想把真实路径提交进仓库，就把 `config.toml` 复制成
`config.local.toml`（已在 `.gitignore` 里）再用 `--config config.local.toml`。

### 跑

```bash
python3 run.py --input "<播客单集链接>" --config config.toml
```

总结由会话模型完成，所以编排会在切好分块后停下来（输出 `waiting_for: session_summary`）。
Agent 模式下我会自动接着做；纯 CLI 模式按下面分步走。

### 分步命令

```bash
python3 download.py   --input "<链接>" --config config.toml
python3 transcribe.py --job-dir <dir>  --config config.toml
python3 summarize.py plan  --job-dir <dir> --config config.toml    # 切成 15 分钟一块
# 逐块读稿 → 用 add 回写摘要 → final 汇总并校验金句
python3 summarize.py final --job-dir <dir> --config config.toml --json-file /tmp/summary.json
python3 publish.py    --job-dir <dir>  --config config.toml        # 写进 vault
```

辅助命令：

```bash
python3 run.py status --job-dir <dir> --config config.toml
python3 run.py audio  --config config.toml --list                  # 归档音频 + 分片占用
python3 run.py audio  --config config.toml --purge --job-dir <dir> # 只清某一期
python3 run.py audio  --config config.toml --purge --all           # 全清（必须显式）
python3 summarize.py prompt --job-dir <dir> --config config.toml   # 打印当前总结提示词
python3 tests.py                                                   # 35 项回归测试
```

---

## 输出长什么样

vault 里新增两个文件（+ 可选的字幕）：

```
播客/2026-10-03 EP752 #752.标题 [job-id].md          # 主笔记
播客/逐字稿/2026-10-03 EP752 #752.标题 [job-id] - 逐字稿.md
播客/逐字稿/2026-10-03 EP752 #752.标题 [job-id].vtt   # WebVTT 字幕
```

主笔记结构：YAML frontmatter（`source / duration / speakers / asr_model / word_count …`）
→ 信息表 → TL;DR → 分层要点（带证据时间戳）→ 金句（逐字命中）→ 行动项表
→ 章节时间表 → 原始简介 → 完整逐字稿链接。
说话人标签、跨块对齐的不确定性会在笔记里明确写出来，不装作确定。

---

## 文件说明

| 文件 | 职责 |
|---|---|
| `SKILL.md` | Agent Skill 定义（触发语、用法、红线）。给 Agent 看的说明书 |
| `core.py` | JSON 协议、配置校验、路径安全、原子无覆盖写入、dotenv 解析 |
| `download.py` | 喜马拉雅 / 小宇宙 / 通用 RSS / 音频直链 / 本地文件 |
| `transcribe.py` | 云端 ASR（默认，带说话人分离）或本地 faster-whisper |
| `summarize.py` | plan / show / add / final / prompt；**不做任何外部模型调用** |
| `publish.py` | 渲染模板 + 原子写入 vault |
| `run.py` | 编排：按缓存跳过已完成步骤 |
| `tests.py` | 35 项回归测试（不联网、不碰真实库） |
| `config.toml` | 唯一必填项是 `vault.path` |
| `templates/note.md` | 笔记模板（`{{token}}` 占位） |
| `templates/summary_prompt.md` | **总结提示词，可自己改**（除「硬性约束」一节） |
| `hotwords.txt` | 专有名词词表，一行一个 |
| `CHANGELOG.md` | 三轮评审的完整修改日志 |
| `.env.example` | 密钥文件模板（真身 `.env` 已被 git 忽略） |

脚本之间用**stdout 单行 JSON**通信，诊断信息走 stderr，退出码有语义：

| 码 | 含义 | 码 | 含义 |
|---|---|---|---|
| 0 | 成功 | 9 | API 错误（含缺 key） |
| 2 | 输入/参数错误 | 10 | 校验失败 |
| 3 | 配置错误（含 vault 占位符） | 11 | 目标已存在，拒绝覆盖 |
| 4 | 网络错误 | 12 | 文件系统错误 |
| 5 | 付费内容，已停 | 13 | 隐私/安全红线被触发 |
| 6 | 媒体工具错误 | 7 | 转写失败 |

---

## 安全设计（发布前请读一遍）

- **`.env` 绝不入库**：`.gitignore` 已排除 `.env` 与 `.env.*`（只放行 `.env.example`）。
  推送前用 `git status --ignored` 确认一次。
- 密钥读取顺序：`$PODCAST_ENV_FILE` → `<skill>/.env` → `~/.config/podcast-to-obsidian/.env`；
  进程环境变量优先级最高。解析是内置实现（支持注释、`export` 前缀、引号），**零第三方依赖**。
- 如果 key 曾经出现在聊天记录或日志里，**去服务商后台轮换**，别只是删文件。
- 仓库里不含任何真实密钥、真实 vault 路径或用户名。

---

## 已知限制

- 说话人是**匿名聚类 ID**，不是声纹识别；3 人以上的节目可能错标，
  笔记里「主播 / 嘉宾」是依据内容的推断。
- 云端热词是否被接口真正采用未验证（不支持时会自动退回，不影响主流程）。
- 小宇宙走页面 `og:audio` + `__NEXT_DATA__`，付费/私密单集拿不到直链。
- 修订版用 `--revision-suffix` 生成新文件，旧笔记不会被改动。
- 本地引擎（faster-whisper）无说话人分离，且这台机器只有 CPU int8，长节目很慢。

## 许可

仓库本身未附带 LICENSE，需要的话自选一个（如 MIT）再加。
