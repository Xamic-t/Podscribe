#!/usr/bin/env bash
# 把 podcast-to-obsidian 装到各个 Agent Harness 的 skills 目录。
#
#   ./install.sh                          # 装到 Claude Code + Codex（用户级）
#   ./install.sh --harness claude         # 只装 Claude Code
#   ./install.sh --harness codex          # 只装 Codex
#   ./install.sh --harness workbuddy      # 装到 WorkBuddy（会补 agent_created 标记）
#   ./install.sh --project                # 装到当前项目（.claude/skills、.agents/skills）
#   ./install.sh --dir /some/where        # 自定义单个目标目录
#
# 幂等：重复执行只会覆盖脚本与模板；已存在的 config.toml / .env 一律保留。
set -euo pipefail

NAME="podcast-to-obsidian"
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HARNESS="all"
SCOPE="user"
CUSTOM_DIR=""

# 技能本体：只拷这些，不拷 .git / __pycache__ / .env / README / install.sh
FILES=(SKILL.md VERSION CHANGELOG.md config.toml hotwords.txt .env.example
       core.py download.py transcribe.py summarize.py publish.py run.py tests.py)

usage() {
  sed -n '2,12p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
  exit 0
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --harness) HARNESS="${2:-}"; shift 2 ;;
    --project) SCOPE="project"; shift ;;
    --dir)     CUSTOM_DIR="${2:-}"; shift 2 ;;
    -h|--help) usage ;;
    *) echo "未知参数：$1（用 --help 看用法）" >&2; exit 2 ;;
  esac
done

case "$HARNESS" in
  all|claude|codex|workbuddy) ;;
  *) echo "--harness 只能是 all / claude / codex / workbuddy" >&2; exit 2 ;;
esac

# ── 复制本体 ────────────────────────────────────────────────
install_to() {
  local dest="$1" label="$2" marker="$3"
  echo "→ ${label}：${dest}"
  mkdir -p "$dest/templates" "$dest/adapters"

  for f in "${FILES[@]}"; do
    [[ -f "$SRC/$f" ]] || continue
    if [[ "$f" == "config.toml" && -f "$dest/$f" ]]; then
      echo "    · 保留已有的 config.toml（不覆盖你的 vault 路径）"
      continue
    fi
    cp "$SRC/$f" "$dest/$f"
  done
  cp "$SRC"/templates/*.md "$dest/templates/" 2>/dev/null || true
  cp "$SRC"/adapters/*.md "$dest/adapters/" 2>/dev/null || true
  [[ -f "$dest/.env" ]] || cp "$SRC/.env.example" "$dest/.env.example"

  # 把 SKILL.md 里的 @SKILL_DIR@ 换成真实安装路径
  python3 - "$dest/SKILL.md" "$dest" <<'PY'
import pathlib, sys
p = pathlib.Path(sys.argv[1])
p.write_text(p.read_text(encoding="utf-8").replace("@SKILL_DIR@", sys.argv[2]),
             encoding="utf-8")
PY

  # harness 差异：只保留标准允许的 frontmatter 字段，WorkBuddy 才补 agent_created
  python3 - "$dest/SKILL.md" "$marker" <<'PY'
import pathlib, re, sys
p = pathlib.Path(sys.argv[1])
t = p.read_text(encoding="utf-8")
t = re.sub(r"^agent_created:.*\n", "", t, flags=re.M)
if sys.argv[2] == "1" and "agent_created:" not in t:
    t = t.replace("allowed-tools:", "agent_created: true\nallowed-tools:", 1)
p.write_text(t, encoding="utf-8")
PY

  # 残留的 __pycache__ 不带过去
  rm -rf "$dest/__pycache__"
}

link_codex_alias() {
  local real="$1"
  local alias="$HOME/.codex/skills/$NAME"
  mkdir -p "$HOME/.codex/skills"
  if [[ -e "$alias" || -L "$alias" ]]; then
    echo "    · ~/.codex/skills/$NAME 已存在，跳过软链"
  else
    ln -s "$real" "$alias"
    echo "    · 软链 $alias → $real"
  fi
}

echo "podcast-to-obsidian · 安装到 ${HARNESS}（${SCOPE} 级）"
echo "源目录：$SRC"
echo

# 只有 WorkBuddy 需要 agent_created 标记（它的 SkillManage 靠这个识别自建 skill）
MARKER=0
[[ "$HARNESS" == "workbuddy" ]] && MARKER=1

if [[ -n "$CUSTOM_DIR" ]]; then
  install_to "$CUSTOM_DIR" "自定义目录" "$MARKER"
else
  if [[ "$SCOPE" == "project" ]]; then
    [[ "$HARNESS" == "all" || "$HARNESS" == "claude" ]] &&
      install_to "$PWD/.claude/skills/$NAME" "Claude Code（项目）" "$MARKER"
    [[ "$HARNESS" == "all" || "$HARNESS" == "codex" ]] &&
      install_to "$PWD/.agents/skills/$NAME" "Codex（项目）" "$MARKER"
    [[ "$HARNESS" == "workbuddy" ]] &&
      install_to "$PWD/.workbuddy/skills/$NAME" "WorkBuddy（项目）" "$MARKER"
  else
    [[ "$HARNESS" == "all" || "$HARNESS" == "claude" ]] &&
      install_to "$HOME/.claude/skills/$NAME" "Claude Code（用户）" "$MARKER"
    [[ "$HARNESS" == "all" || "$HARNESS" == "codex" ]] && {
      install_to "$HOME/.agents/skills/$NAME" "Codex（用户）" "$MARKER"
      link_codex_alias "$HOME/.agents/skills/$NAME"
    }
    [[ "$HARNESS" == "workbuddy" ]] &&
      install_to "$HOME/.workbuddy/skills/$NAME" "WorkBuddy（用户）" "$MARKER"
  fi
fi

cat <<NOTE

装好了。接下来两件事（每个 harness 各做一次）：

  1. 填密钥：
     cp <安装目录>/.env.example <安装目录>/.env && chmod 600 <安装目录>/.env
     # 然后填 SILICONFLOW_API_KEY=sk-...

  2. 填 vault 路径：编辑 <安装目录>/config.toml 的 [vault].path
     （仍为 PLACEHOLDER_VAULT_PATH 时，publish 会拒绝写入）

各 harness 的权限、沙箱、调用方式见 adapters/claude-code.md 与 adapters/codex.md。
NOTE
