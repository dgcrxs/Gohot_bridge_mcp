#!/bin/bash
# Launch the installer from source (used when no .app bundle is available).
# The repository root is the parent of this script's directory.
# Drag & drop: any dropped folder arrives in "$@"; the installer treats a bare
# path exactly like --project <path> and opens the GUI prefilled with it.
set -u
cd "$(dirname "$0")/.." || exit 2

PY="$(command -v python3 || true)"
if [ -z "$PY" ]; then
  echo "没有找到 python3。请先安装 Python 3.10 或更新版本：https://www.python.org/downloads/"
  read -r -n 1 -p "按任意键关闭…"
  echo
  exit 2
fi

"$PY" -m installer "$@"
code=$?
if [ "$code" -ne 0 ]; then
  echo "安装器退出码：$code"
  read -r -n 1 -p "按任意键关闭…"
  echo
fi
exit "$code"
