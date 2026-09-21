#!/usr/bin/env bash
set -euo pipefail

REPO="${CCSWITCHCLI_REPO:-weicao/ccswitchcli}"
REF="${CCSWITCHCLI_REF:-main}"
DEST="${HOME}/.local/bin"
BIN="${DEST}/ccswitchcli"
BASE="https://raw.githubusercontent.com/${REPO}/${REF}"

mkdir -p "${DEST}"
tmp="$(mktemp)"
trap 'rm -f "${tmp}"' EXIT

if ! curl -fsSL "${BASE}/ccswitch.py" -o "${tmp}"; then
  echo "下载失败：${BASE}/ccswitch.py" >&2
  exit 1
fi

install -m 755 "${tmp}" "${BIN}"

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
ccswitchcli --help >/dev/null
echo "用 ccswitchcli 查看供应商，用 ccswitchcli openai-official 或 ccswitchcli sub2api 切换。"
