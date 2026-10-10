# 适配 Claude Code

Claude Code 的 skill 遵循 Agent Skills 开放标准，本仓库根目录就是标准结构，
**不用改任何文件**，拷过去即可。

## 安装

```bash
# 个人级（所有项目可用）
mkdir -p ~/.claude/skills
cp -R /path/to/podscribe ~/.claude/skills/podscribe

# 或项目级（只对当前仓库生效，可提交进团队仓库）
mkdir -p .claude/skills
cp -R /path/to/podscribe .claude/skills/podscribe
```

更省事：仓库根目录的 `install.sh --harness claude` 会做同样的事，并顺手把
`SKILL.md` 里的 `@SKILL_DIR@` 替换成真实路径。

装完后删掉不需要的东西（可选）：`rm -rf ~/.claude/skills/podscribe/.git`

## 配置（和别的 harness 一样）

```bash
cd ~/.claude/skills/podscribe
cp .env.example .env && chmod 600 .env     # 填 SILICONFLOW_API_KEY
$EDITOR config.toml                        # 只改 [vault].path
```

## 调用

- 隐式：直接说「总结这期播客 <链接>」，Claude 按 description 自己匹配；
- 显式：`/podscribe`；
- 查看是否被识别：`/skills`。

## Claude Code 特有的注意点

**1. 权限。** 首次运行 `python3 run.py …` 会触发 Bash 授权。
`SKILL.md` 的 frontmatter 已经声明了 `allowed-tools: Bash, Read, Write, Edit, Glob, Grep`，
但 Claude Code 的策略仍可能逐条询问。想少打扰，把下面这段加进项目的
`.claude/settings.json`（或 `~/.claude/settings.json`）：

```json
{
  "permissions": {
    "allow": [
      "Bash(python3 *run.py*)",
      "Bash(python3 *download.py*)",
      "Bash(python3 *transcribe.py*)",
      "Bash(python3 *summarize.py*)",
      "Bash(python3 *publish.py*)"
    ]
  }
}
```

**2. 工作目录。** Claude Code 在你的项目目录里启动，但脚本全部用绝对路径调用，
缓存写到 `runtime.cache_root`（默认 `~/.cache/podscribe`），不受 cwd 影响。

**3. 长节目用子代理。** 超过 2 小时的节目，主上下文读完所有 block 会很挤。
让 Claude 派 `general-purpose` 子代理逐块压缩：把 `templates/summary_prompt.md`
一起交给它，并要求它用下面这条命令回写（子代理有 Bash，能直接跑）：

```bash
python3 <SKILL>/summarize.py add --job-dir <dir> --config <SKILL>/config.toml \
  --block 3 --json-file /tmp/block-3.json
```

**4. 环境变量而非 .env 也行。** Claude Code 里临时覆盖最方便：
`SILICONFLOW_API_KEY=sk-xxx python3 …`（优先级高于 `.env`）。

**5. 卸载。**

```bash
rm -rf ~/.claude/skills/podscribe
```
