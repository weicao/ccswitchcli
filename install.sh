#!/usr/bin/env bash
set -euo pipefail

REPO="${CCSWITCHCLI_REPO:-weicao/ccswitchcli}"
REF="${CCSWITCHCLI_REF:-main}"
DEST="${HOME}/.local/bin"
BIN="${DEST}/ccswitchcli"
BASE="https://raw.githubusercontent.com/${REPO}/${REF}"

pick_python() {
  local candidate
  for candidate in python3.13 python3.12 python3.11 python3; do
    if command -v "${candidate}" >/dev/null 2>&1; then
      echo "${candidate}"
      return 0
    fi
  done
  echo "python3"
}

PY="$(pick_python)"

mkdir -p "${DEST}"
tmp="$(mktemp)"
trap 'rm -f "${tmp}"' EXIT

if ! curl -fsSL "${BASE}/ccswitch.py" -o "${tmp}"; then
  echo "下载失败：${BASE}/ccswitch.py" >&2
  exit 1
fi

{
  echo "#!$(command -v "${PY}")"
  tail -n +2 "${tmp}"
} > "${tmp}.out"
install -m 755 "${tmp}.out" "${BIN}"
rm -f "${tmp}.out"

"${PY}" - <<'PY'
import subprocess, sys
if sys.version_info < (3, 11):
    try:
        import tomli  # noqa: F401
    except ImportError:
        cmd = [sys.executable, "-m", "pip", "install", "--user", "tomli"]
        print("Python 低于 3.11，正在安装 tomli：", " ".join(cmd))
        subprocess.check_call(cmd)
PY

case ":${PATH}:" in
  *":${DEST}:"*) ;;
  *)
    echo "已安装到 ${BIN}"
    echo "请把 ${DEST} 加到 PATH，例如："
    echo "  echo 'export PATH=\"\$HOME/.local/bin:\$PATH\"' >> ~/.zshrc"
    exit 0
    ;;
esac

echo "已安装：$(command -v ccswitchcli || echo "${BIN}")"
"${BIN}" --help >/dev/null
echo "用 ccswitchcli 查看供应商，用 ccswitchcli openai-official 或 ccswitchcli sub2api 切换。"
