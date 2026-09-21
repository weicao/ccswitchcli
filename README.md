# CCSwitch CLI

[中文文档](README_ZH.md)

Command-line tool for a local [CC Switch](https://ccswitch.io) install.

It lists Codex providers and switches between them, for example `sub2api` and `OpenAI Official`.

The CLI reads `~/.cc-switch/cc-switch.db` and updates Codex `~/.codex/config.toml`. It backs up files before writing. Command output never prints API keys or login tokens.

## Install

One command:

```bash
curl -fsSL https://raw.githubusercontent.com/weicao/ccswitchcli/main/install.sh | bash
```

Or install from a local checkout:

```bash
install -m 755 ccswitch.py ~/.local/bin/ccswitchcli
```

Then run `ccswitchcli`.

## Quick start

```bash
ccswitchcli
ccswitchcli sub2api
ccswitchcli openai-official
ccswitchcli current
ccswitchcli doctor
```

The list prints the switch command first when it differs from the CC Switch display name. Use `openai-official` for OpenAI Official.

Common options:

- `--cc-switch-home PATH`: CC Switch data directory (default `~/.cc-switch`)
- `--codex-home PATH`: Codex config directory (default `$CODEX_HOME` or `~/.codex`)
- `--dry-run`: preview a switch without writing files
- `--json`: machine-readable output

## How switching works

The command stops CC Switch, writes the provider into Codex config, then starts CC Switch again so the tray matches. Accessibility permission is not required.

- Switch to `sub2api`: set Codex `model_provider` from the saved third-party provider, including its API endpoint.
- Switch to `openai-official`: remove the third-party `model_provider` and restore the official path.
- Other Codex settings (project trust, plugins) are kept.
- If CC Switch has “Keep official login when switching third-party providers” enabled, `auth.json` is not overwritten.

Restart Codex after switching so it picks up the new provider.

## Tests

```bash
python3 -m unittest discover -s tests -v
```
