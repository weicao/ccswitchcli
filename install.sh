#!/usr/bin/env bash
set -euo pipefail

REPO="${CCSWITCHCLI_REPO:-weicao/ccswitchcli}"
REF="${CCSWITCHCLI_REF:-main}"
DEST="${HOME}/.local/bin"
LIB="${HOME}/.local/share/ccswitchcli"
BIN="${DEST}/ccswitchcli"
SCRIPT="${LIB}/ccswitch.py"
BASE="https://raw.githubusercontent.com/${REPO}/${REF}"

python_ok() {
  local cmd="$1"
  if ! command -v "${cmd}" >/dev/null 2>&1 && [[ ! -x "${cmd}" ]]; then
    return 1
  fi
  "${cmd}" -c 'import sys; raise SystemExit(0 if sys.version_info[0] == 3 and sys.version_info[1] >= 9 else 1)' >/dev/null 2>&1
}

pick_python() {
  local candidate
  for candidate in \
    python3 \
    python \
    /opt/homebrew/bin/python3 \
    /opt/homebrew/bin/python \
    /usr/local/bin/python3 \
    /usr/local/bin/python \
    python3.13 \
    python3.12 \
    python3.11
  do
    if python_ok "${candidate}"; then
      if command -v "${candidate}" >/dev/null 2>&1; then
        command -v "${candidate}"
      else
        echo "${candidate}"
      fi
      return 0
    fi
  done
  return 1
}

if ! PY="$(pick_python)"; then
  echo "找不到 Python 3.9+。请安装 Python，二进制名可以是 python 或 python3（Homebrew 一般是 /opt/homebrew/bin/python3）。" >&2
  exit 1
fi

mkdir -p "${DEST}" "${LIB}"
tmp="$(mktemp)"
trap 'rm -f "${tmp}"' EXIT

if ! curl -fsSL "${BASE}/ccswitch.py" -o "${tmp}"; then
  echo "下载失败：${BASE}/ccswitch.py" >&2
  exit 1
fi
install -m 755 "${tmp}" "${SCRIPT}"

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

cat > "${BIN}" <<EOF
#!/usr/bin/env bash
set -euo pipefail
SCRIPT="${SCRIPT}"
python_ok() {
  local cmd="\$1"
  if ! command -v "\${cmd}" >/dev/null 2>&1 && [[ ! -x "\${cmd}" ]]; then
    return 1
  fi
  "\${cmd}" -c 'import sys; raise SystemExit(0 if sys.version_info[0] == 3 and sys.version_info[1] >= 9 else 1)' >/dev/null 2>&1
}
for candidate in python3 python /opt/homebrew/bin/python3 /opt/homebrew/bin/python /usr/local/bin/python3 /usr/local/bin/python python3.13 python3.12 python3.11 "${PY}"; do
  if python_ok "\${candidate}"; then
    exec "\${candidate}" "\${SCRIPT}" "\$@"
  fi
done
echo "找不到 Python 3.9+（python 或 python3）。" >&2
exit 1
EOF
chmod +x "${BIN}"

case ":${PATH}:" in
  *":${DEST}:"*) ;;
  *)
    echo "已安装到 ${BIN}"
    echo "请把 ${DEST} 加到 PATH，例如："
    echo "  echo 'export PATH=\"\$HOME/.local/bin:\$PATH\"' >> ~/.zshrc"
    exit 0
    ;;
esac

echo "已安装：$(command -v ccswitchcli || echo "${BIN}")  （Python：${PY}）"
"${BIN}" --help >/dev/null
echo "用 ccswitchcli 查看供应商，用 ccswitchcli openai-official 或 ccswitchcli sub2api 切换。"
