#!/usr/bin/env bash
set -euo pipefail

# 自动将 script/Makefile 复制到项目根目录并替换现有 Makefile（先备份）
# 用法: 在仓库任意位置运行或直接从仓库根目录运行:
#   script/auto_setup_joycon.sh

SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SRC_DIR/.." && pwd)"
SRC="$SRC_DIR/Makefile"
DEST="$PROJECT_ROOT/Makefile"

if [ ! -f "$SRC" ]; then
  echo "错误: 源 Makefile 不存在: $SRC" >&2
  exit 1
fi

if [ -f "$DEST" ]; then
  BACKUP="$DEST.bak.$(date +%Y%m%d%H%M%S)"
  echo "检测到已存在的 Makefile，备份到： $BACKUP"
  cp -a -- "$DEST" "$BACKUP"
fi

echo "正在复制 $SRC 到 $DEST"
cp -a -- "$SRC" "$DEST"
echo "复制完成。目标文件: $DEST"

exit 0