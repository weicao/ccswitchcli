# CCSwitch CLI

版本 0.1。

[English](README.md)

本地命令行工具，用来操作已经安装的 [CC Switch](https://ccswitch.io)。

支持 Codex、Claude Code（`cc`）和 Grok：列出供应商并切换。

它读取 `~/.cc-switch/cc-switch.db`，改 Codex 的 `~/.codex/config.toml`。切换前会备份原文件。命令输出里不会打印 API Key 或登录 token。

## 安装

一条命令：

```bash
curl -fsSL https://raw.githubusercontent.com/weicao/ccswitchcli/main/install.sh | bash
```

或本地安装：

```bash
install -m 755 ccswitch.py ~/.local/bin/ccswitchcli
```

装好后直接运行 `ccswitchcli`。

## 快速开始

```bash
ccswitchcli
ccswitchcli sub2api
ccswitchcli openai-official
ccswitchcli cc PackyCode
ccswitchcli grok sub2api
ccswitchcli current
ccswitchcli doctor
```

列表里如果显示名和命令不一致，会把切换命令写在前面。切官方请用 `openai-official`。

常用选项：

- `--cc-switch-home PATH`：CC Switch 数据目录，默认 `~/.cc-switch`
- `--codex-home PATH`：Codex 配置目录，默认 `$CODEX_HOME` 或 `~/.codex`
- `--dry-run`：只预览，不写文件
- `--json`：机器可读输出

## 切换时怎么做

命令会先关掉 CC Switch，写好配置，再重新打开。这样托盘会跟命令一致。不需要辅助功能权限。

- 切到 `sub2api`：把 Codex 的 `model_provider` 写成 CC Switch 里保存的第三方配置，并带上对应接口地址。
- 切回 `openai-official`：去掉第三方 `model_provider`，恢复官方路径。
- 不会丢掉当前 Codex 里的项目信任、插件等其它设置。
- 如果 CC Switch 打开了「切换第三方时保留官方登录」，则不覆盖 `auth.json`。

切换后请新开一次 Codex，才会用新接口。

## 测试

```bash
python3 -m unittest discover -s tests -v
```
