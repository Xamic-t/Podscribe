# 适配 Codex（OpenAI Codex CLI）

Codex 同样按 Agent Skills 标准读 `SKILL.md`，所以本仓库不用改结构。
差别只在**放哪儿**和**沙箱**。

## 安装

用户级路径有两个口径，都给上最稳（Codex 扫描时会跟随符号链接）：

```bash
mkdir -p ~/.agents/skills ~/.codex/skills
cp -R /path/to/podscribe ~/.agents/skills/podscribe
ln -sfn ~/.agents/skills/podscribe ~/.codex/skills/podscribe
```

项目级（随仓库分发给团队）：

```bash
mkdir -p .agents/skills          # 或 .codex/skills
cp -R /path/to/podscribe .agents/skills/podscribe
```

`install.sh --harness codex` 会自动完成上面这套（含软链）。

装完记得删掉 `.git`：`rm -rf ~/.agents/skills/podscribe/.git`

## 开启 skills（旧版本需要）

部分版本把 skills 放在实验开关后面。在 `~/.codex/config.toml` 加：

```toml
[features]
skills = true
```

改完重启 Codex。也可以只对单次运行开启：`codex --enable skills`。
想临时停用这个 skill 而不删除：

```toml
[[skills.config]]
path = "/Users/<你>/.agents/skills/podscribe/SKILL.md"
enabled = false
```

## 配置

```bash
cd ~/.agents/skills/podscribe
python3 setup.py --check                   # 先看还缺什么（只读）
python3 setup.py                           # 交互式问：库路径 + API key（不回显）
# 写进 config.local.toml 与 .env，两者都不进版本库；config.toml 保持模板原样
```

## 调用

- 提示词里写 `$podscribe`；
- 或在 TUI 里 `/skills` 浏览并插入；
- 也可以直接说需求，让 Codex 按 description 隐式匹配。

## Codex 特有的注意点

**1. 沙箱会挡住写操作。** Codex 默认在沙箱里跑（macOS 上是 seatbelt），
写 `~/.cache/podscribe` 和写你的 vault 都可能被拦。
三种解法任选：

- 运行时按需批准写入（最安全）；
- `codex --full-auto "用 podscribe 总结 <链接>"`；
- 在 `~/.codex/config.toml` 里放宽：`sandbox_mode = "workspace-write"` 或
  给 `sandbox_permissions` 加上缓存目录与 vault 目录的写权限。

**2. frontmatter 的字数限制更紧。** Codex 要求 `name` ≤ 100、`description` ≤ 500
（Claude Code 分别是 64 / 1024）。本仓库的 `name` 19 字符、`description` 一百多字符，
两边都满足。你自己改 description 时别超 500。

**3. 多余的 frontmatter 键会被忽略。** `allowed-tools` 是 Claude Code 的扩展，
Codex 直接忽略，不会报错——所以共用一份 `SKILL.md` 是安全的。

**4. 长节目。** Codex 也有子代理能力，但最简单的做法是让它**顺序读块**：
`summarize.py plan` 之后按 `blocks/block-0001.md` … 依次读、依次 `add` 回写。
一次性把几十万字符塞进上下文不划算。

**5. 非交互执行。**

```bash
codex exec --full-auto "用 \$podscribe 总结 https://www.xiaoyuzhoufm.com/episode/xxxx"
```

注意：非交互模式下落盘前的确认没人点，建议先 `--render-only` 跑一遍确认内容，
确认无误再让它写进 vault。

**6. 卸载。**

```bash
rm -rf ~/.agents/skills/podscribe ~/.codex/skills/podscribe
```
