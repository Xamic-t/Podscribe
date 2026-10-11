# 贡献指南

谢谢你想改进 podscribe。这篇写清了提 PR 前需要知道的全部；英文版在文末（[Contributing (English)](#contributing-english)）。

## 欢迎什么

- **Bug 修复** —— 尤其是转写对齐、下载源失效、路径/权限相关的边界问题
- **新来源适配** —— 新的播客平台、RSS 变体、字幕源
- **总结提示词** —— `templates/summary_prompt.md` 里的风格、篇幅、结构
- **文档** —— 错别字、说不清的地方、英文版与中文版不一致
- **性能与可靠性** —— 长音频切块、重试策略、缓存命中

不确定某个改动要不要做？先开个 Issue 说想法，比写完再被打回省事。

## 环境

```bash
python3 setup.py --check     # 看本机还缺什么（只读，不改文件）
python3 tests.py             # 回归测试：46 项，必须全绿
```

- Python **3.11+**（用到标准库 `tomllib`）
- `ffmpeg` / `ffprobe`、`curl`
- **不要引入第三方 Python 包** —— 这个项目刻意保持零依赖。确实需要就先开 Issue 讨论

## PR 流程

1. Fork，从 `main` 拉一个分支，名字用 `fix/…`、`feat/…`、`docs/…`
2. 改代码，**同时补测试**（见下一节）
3. 本地跑 `python3 tests.py`，必须全绿
4. 提交，信息格式：

   ```
   <type>(<scope>): <中文描述>
   ```

   `type` 取 `feat` / `fix` / `docs` / `refactor` / `test` / `chore`；`scope` 可省略，例如
   `fix(transcribe): 重叠区去重在块边界上漏掉一句`。
5. 开 PR，描述里写清三件事：**改了什么 / 为什么 / 怎么验证**（贴命令和真实输出）
6. 我会尽快 review。小改动（文档、明显 bug）直接合；涉及行为变更的会先讨论

## 测试要求

- `python3 tests.py` 必须全绿，**不能联网、不能碰真实的 Obsidian 库**
- 新增或修改行为都要有对应用例；修 bug 请先写一个能复现的用例
- 需要外部命令（`ffmpeg` / `curl`）的场景，在测试里替换掉，不要真的调用
- **不要在测试里写形如 `sk-` + 长串的假密钥**：仓库的脱敏复扫会把它当成真密钥，导致推送被拦（用 `podscribe-test-key-not-a-secret-0001` 这类形状）

## 代码风格

沿用现有约定，PR 里保持一致：

**进程协议**

- stdout **只输出一行 JSON**（用 `core.emit()` / `core.fail()`）；给人看的提示走 stderr（`core.log()`）
- 退出码有固定语义，别自己发明：`0` 成功 / `2` 输入错误 / `3` 配置错误 / `4` 网络 / `5` 付费内容 / `6` 媒体工具 / `7` 转写失败 / `9` API / `10` 校验 / `11` 目标已存在 / `12` 文件系统 / `13` 隐私红线
- 报错一律 `raise C.Fail(stage, "CODE", "一句人话", 退出码)`，不要裸抛异常

**文件与路径**

- 一律用 `pathlib.Path`，不要字符串拼路径
- 写进 vault 的目标必须先过 `core.safe_target()`
- 写文件用 `core.exclusive_write()` / `core.exclusive_write_bytes()`（原子 + 不覆盖）
- job 目录必须过 `core.job_dir()` / `core.resolve_job_dir()`

**网络与外部命令**

- 下载 URL 走 `core.check_url()`，并且 curl 命令里 URL 前必须有 `--` 终止符
- 读远端用 `core.http_bytes()`（自带大小上限）
- 外部命令路径用 `core.tool_path()` 解析（找不到会回落到 PATH，跨平台）

**密钥**

- 只通过 `core.api_key()` 读取；任何情况下不打印、不写日志、不写进文件（`.env` 除外）
- 需要回显时只允许尾 4 位

**Python 本身**

- `from __future__ import annotations` + 类型注解；f-string；4 空格缩进；双引号
- 注释与用户可见文案用中文，标识符用英文
- 不要新增模块级可变状态

## 红线（碰到会直接拒）

1. 提交 `.env`、`config.local.toml`，或任何真实密钥、真实 vault 路径、真实音频、个人笔记
2. 绕过付费 / 私密内容的鉴权
3. 读取 vault 里已有的笔记（`safety.scan_vault` 与 `read_existing_notes` 必须恒为 `false`）
4. 让密钥发往 `asr.allowed_hosts` 之外的主机
5. 把真实网络请求塞进测试

推送前可以先自查一遍：

```bash
git diff --cached | grep -nE "sk-[A-Za-z0-9]{10,}|/Users/[a-z]|C:\\Users"   # 应无输出
git status --short | grep -E "\.env$|config\.local\.toml"                      # 应无输出
```

## 许可证

MIT（见 `LICENSE`）。提交 PR 即表示你同意以 MIT 许可发布你的贡献。

---

## Contributing (English)

Thanks for wanting to improve podscribe. Here is everything you need before opening a PR.

**What is welcome:** bug fixes (audio alignment, dead download sources, path/permission edges), new sources (platforms, RSS variants, subtitle sources), summary-prompt improvements (`templates/summary_prompt.md`), docs, and reliability/performance work on long episodes. If you are unsure whether a change fits, open an issue first — cheaper than writing it and being turned down.

**Environment:** Python 3.11+ (standard library `tomllib`), plus `ffmpeg` / `ffprobe` and `curl`. **No third-party Python packages** — this project is deliberately dependency-free; open an issue if you believe one is unavoidable. Run `python3 setup.py --check` to see what your machine is missing and `python3 tests.py` for the 46-test regression suite.

**PR flow:** fork, branch from `main` as `fix/…`, `feat/…` or `docs/…`, make the change **with tests**, get `python3 tests.py` fully green, then commit as `<type>(<scope>): <description>` (`feat` / `fix` / `docs` / `refactor` / `test` / `chore`) and open a PR whose description covers **what changed, why, and how you verified it** (paste the commands and the real output). Small, clearly-correct changes get merged quickly; behavioural changes get discussed first.

**Tests:** must be green, and must never touch the network or a real Obsidian vault. Cover new behaviour; for a bug fix, start with a case that reproduces it. Stub out external commands (`ffmpeg`, `curl`) rather than invoking them. Do **not** use key-shaped fixtures such as `sk-` followed by a long string — the repo's pre-push secret scan treats those as real keys and will block the push (use something like `podscribe-test-key-not-a-secret-0001`).

**Code style:** stdout is **one line of JSON** (`core.emit()` / `core.fail()`), human-facing output goes to stderr (`core.log()`), and exit codes have fixed meanings (0 ok, 2 bad input, 3 config, 4 network, 5 paid, 6 media tool, 7 transcription, 9 API, 10 validation, 11 target exists, 12 filesystem, 13 privacy). Raise errors via `C.Fail(stage, "CODE", "human sentence", exit_code)`. Use `pathlib` everywhere, run vault targets through `core.safe_target()`, write files with `core.exclusive_write()` / `exclusive_write_bytes()`, resolve job directories with `core.job_dir()` / `core.resolve_job_dir()`, validate download URLs with `core.check_url()` (and always put `--` before the URL in curl commands), read remote bodies with `core.http_bytes()` (it enforces a size cap), and resolve external binaries with `core.tool_path()`. Read secrets only through `core.api_key()`; never print, log, or persist them (except in `.env`), and only ever echo the last four characters. Python: `from __future__ import annotations` with type hints, f-strings, 4-space indent, double quotes, no new module-level mutable state. Chinese is used for comments and user-facing strings; identifiers stay English.

**Red lines (a PR touching these is rejected):** committing `.env`, `config.local.toml`, real keys, real vault paths, real audio or personal notes; bypassing paid/private content; reading existing vault notes (`safety.scan_vault` and `read_existing_notes` must stay `false`); sending the key to a host outside `asr.allowed_hosts`; and putting real network calls into tests.

**License:** MIT. By opening a PR you agree your contribution is released under it.
