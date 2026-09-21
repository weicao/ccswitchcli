#!/usr/bin/env python3
"""CLI for operating a local CC Switch install.

This talks to ~/.cc-switch (SQLite + settings.json) and writes Codex live
files under ~/.codex. It never prints API keys or login tokens.
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    import tomllib
except ModuleNotFoundError:  # Python < 3.11
    try:
        import tomli as tomllib  # type: ignore
    except ModuleNotFoundError as exc:
        raise SystemExit(
            "当前 Python 低于 3.11。请安装 tomli：python3 -m pip install tomli"
        ) from exc


APP_TYPE = "codex"
APP_ALIASES = {
    "cc": "claude",
    "claude": "claude",
    "codex": "codex",
    "grok": "grokbuild",
    "grokbuild": "grokbuild",
}
APP_LABELS = {
    "claude": "Claude Code",
    "codex": "Codex",
    "grokbuild": "Grok",
}
CURRENT_SETTING_KEYS = {
    "claude": "currentProviderClaude",
    "codex": "currentProviderCodex",
}
GROK_OWNED_KEYS = {"models", "model"}
OFFICIAL_ALIASES = {
    "openai-official",
    "openai",
    "official",
    "openaiofficial",
    "openai-offical",
    "openaioffical",
}
PROVIDER_OWNED_KEYS = {
    "model",
    "model_provider",
    "model_providers",
    "disable_response_storage",
}
BARE_KEY_RE = re.compile(r"^[A-Za-z0-9_-]+$")
NAME_NORM_RE = re.compile(r"[^a-z0-9]+")


class CcswitchError(Exception):
    """Expected user-facing error."""


def now_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def default_cc_switch_home() -> Path:
    return Path(os.environ.get("CC_SWITCH_HOME", "~/.cc-switch")).expanduser()


def default_codex_home() -> Path:
    return Path(os.environ.get("CODEX_HOME", "~/.codex")).expanduser()


def default_claude_home() -> Path:
    return Path(os.environ.get("CLAUDE_HOME", "~/.claude")).expanduser()


def default_grok_home() -> Path:
    return Path(os.environ.get("GROK_HOME", "~/.grok")).expanduser()


def resolve_app(value: str | None, default: str = APP_TYPE) -> str:
    if not value or value == "all":
        return default
    key = value.strip().lower()
    if key in APP_ALIASES:
        return APP_ALIASES[key]
    raise CcswitchError(f"不支持的应用 {value!r}。可用：cc / claude、codex、grok。")


def default_state_dir() -> Path:
    return Path(os.environ.get("CCSWITCH_STATE_DIR", "~/.ccswitch-cli")).expanduser()


def normalize_name(value: str) -> str:
    return NAME_NORM_RE.sub("", value.strip().lower())


def atomic_write_bytes(path: Path, data: bytes, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp_path, mode)
        os.replace(tmp_path, path)
    finally:
        if tmp_path.exists():
            tmp_path.unlink()


def atomic_write_text(path: Path, text: str, mode: int = 0o600) -> None:
    atomic_write_bytes(path, text.encode("utf-8"), mode=mode)


def backup_file(path: Path, backup_dir: Path) -> Path | None:
    if not path.exists():
        return None
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup = backup_dir / f"{path.name}.{now_stamp()}.bak"
    shutil.copy2(path, backup)
    os.chmod(backup, 0o600)
    return backup


def toml_key(key: str) -> str:
    return key if BARE_KEY_RE.fullmatch(key) else json.dumps(key, ensure_ascii=False)


def toml_path(parts: list[str]) -> str:
    return ".".join(toml_key(part) for part in parts)


def toml_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, int) and not isinstance(value, bool):
        return str(value)
    if isinstance(value, float):
        return repr(value)
    if isinstance(value, list):
        return "[" + ", ".join(toml_value(item) for item in value) + "]"
    if isinstance(value, dict):
        entries = ", ".join(f"{toml_key(str(k))} = {toml_value(v)}" for k, v in value.items())
        return "{" + entries + "}"
    raise CcswitchError(f"无法写入 TOML 类型：{type(value).__name__}")


def render_toml(data: dict[str, Any]) -> str:
    lines: list[str] = []

    def render_table(table: dict[str, Any], path: list[str]) -> None:
        scalars = [(key, value) for key, value in table.items() if not isinstance(value, dict)]
        children = [(key, value) for key, value in table.items() if isinstance(value, dict)]
        if path:
            if lines and lines[-1] != "":
                lines.append("")
            lines.append(f"[{toml_path(path)}]")
        for key, value in scalars:
            lines.append(f"{toml_key(str(key))} = {toml_value(value)}")
        for key, child in children:
            render_table(child, path + [str(key)])

    render_table(data, [])
    return "\n".join(lines).rstrip() + "\n" if data or lines else ""


def parse_toml(text: str, source: str) -> dict[str, Any]:
    if not text.strip():
        return {}
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise CcswitchError(f"无法解析 TOML（{source}）：{exc}") from exc
    if not isinstance(data, dict):
        raise CcswitchError(f"TOML 根类型无效：{source}")
    return data


def load_json(path: Path, missing: dict[str, Any] | None = None) -> dict[str, Any]:
    if not path.exists():
        if missing is not None:
            return copy.deepcopy(missing)
        raise CcswitchError(f"找不到文件：{path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CcswitchError(f"无法读取 JSON {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise CcswitchError(f"JSON 根类型无效：{path}")
    return data


def open_db(db_path: Path, readonly: bool) -> sqlite3.Connection:
    if not db_path.exists():
        raise CcswitchError(f"找不到 CC Switch 数据库：{db_path}")
    uri = f"file:{db_path}?mode={'ro' if readonly else 'rw'}"
    try:
        con = sqlite3.connect(uri, uri=True, timeout=10)
    except sqlite3.Error as exc:
        raise CcswitchError(f"无法打开 CC Switch 数据库 {db_path}: {exc}") from exc
    con.row_factory = sqlite3.Row
    return con


def parse_settings_config(raw: str, name: str) -> dict[str, Any]:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise CcswitchError(f"供应商 {name} 的配置不是合法 JSON：{exc}") from exc
    if not isinstance(data, dict):
        raise CcswitchError(f"供应商 {name} 的配置格式无效")
    return data


def provider_api_key(auth: Any) -> str | None:
    if not isinstance(auth, dict):
        return None
    value = auth.get("OPENAI_API_KEY")
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def extract_base_url_from_provider(provider: dict[str, Any]) -> str | None:
    env = provider.get("env") or {}
    url = env.get("ANTHROPIC_BASE_URL")
    if isinstance(url, str) and url.strip():
        return url.strip()
    cfg = provider.get("config") or {}
    model = cfg.get("model")
    if isinstance(model, dict):
        for value in model.values():
            if isinstance(value, dict):
                nested = value.get("base_url")
                if isinstance(nested, str) and nested.strip():
                    return nested.strip()
    return extract_base_url(cfg)


def extract_base_url(config: dict[str, Any]) -> str | None:
    providers = config.get("model_providers")
    if not isinstance(providers, dict):
        return None
    custom = providers.get("custom")
    if isinstance(custom, dict):
        url = custom.get("base_url")
        if isinstance(url, str) and url:
            return url
    for value in providers.values():
        if isinstance(value, dict) and isinstance(value.get("base_url"), str):
            return value["base_url"]
    return None


def is_official(provider: dict[str, Any]) -> bool:
    if provider.get("category") == "official":
        return True
    return normalize_name(provider["name"]) in OFFICIAL_ALIASES


def load_providers(con: sqlite3.Connection, app_type: str = APP_TYPE) -> list[dict[str, Any]]:
    rows = con.execute(
        """
        SELECT id, name, website_url, category, is_current, settings_config
        FROM providers
        WHERE app_type = ?
        ORDER BY COALESCE(sort_index, 9999), name
        """,
        (app_type,),
    ).fetchall()
    providers: list[dict[str, Any]] = []
    for row in rows:
        settings = parse_settings_config(row["settings_config"], row["name"])
        config = parse_toml(settings.get("config") or "", f"provider {row['name']}")
        providers.append(
            {
                "id": row["id"],
                "name": row["name"],
                "website_url": row["website_url"],
                "category": row["category"],
                "is_current": bool(row["is_current"]),
                "app_type": app_type,
                "auth": settings.get("auth") if isinstance(settings.get("auth"), dict) else {},
                "env": settings.get("env") if isinstance(settings.get("env"), dict) else {},
                "payload": settings,
                "config": config,
                "config_text": settings.get("config") or "",
            }
        )
    return providers


def resolve_provider(providers: list[dict[str, Any]], query: str) -> dict[str, Any]:
    needle = query.strip()
    if not needle:
        raise CcswitchError("请提供供应商名称，例如 sub2api 或 openai-official。")
    by_id = [p for p in providers if p["id"] == needle]
    if len(by_id) == 1:
        return by_id[0]
    exact = [p for p in providers if p["name"] == needle]
    if len(exact) == 1:
        return exact[0]
    norm = normalize_name(needle)
    if norm in OFFICIAL_ALIASES:
        official = [p for p in providers if is_official(p)]
        if len(official) == 1:
            return official[0]
        if len(official) > 1:
            names = ", ".join(p["name"] for p in official)
            raise CcswitchError(f"匹配到多个官方供应商：{names}")
    fuzzy = [p for p in providers if normalize_name(p["name"]) == norm or norm in normalize_name(p["name"])]
    if len(fuzzy) == 1:
        return fuzzy[0]
    if len(fuzzy) > 1:
        names = ", ".join(p["name"] for p in fuzzy)
        raise CcswitchError(f"供应商名称不唯一：{query!r} 匹配到 {names}")
    available = ", ".join(p["name"] for p in providers) or "无"
    hint = ""
    if "official" in norm or "openai" in norm:
        hint = " 可以写成：ccswitchcli openai-official"
    raise CcswitchError(f"找不到供应商 {query!r}。可用项：{available}。{hint}".rstrip())


def current_provider(
    providers: list[dict[str, Any]],
    settings: dict[str, Any],
    app_type: str = APP_TYPE,
) -> dict[str, Any] | None:
    current_id = settings.get(CURRENT_SETTING_KEYS.get(app_type, ""))
    if isinstance(current_id, str) and current_id:
        for provider in providers:
            if provider["id"] == current_id:
                return provider
    marked = [p for p in providers if p["is_current"]]
    if len(marked) == 1:
        return marked[0]
    return None


def switch_command(provider: dict[str, Any]) -> str:
    if is_official(provider):
        return "openai-official"
    return provider["name"]


def public_row(provider: dict[str, Any], active_id: str | None) -> dict[str, Any]:
    row = {
        "id": provider["id"],
        "name": provider["name"],
        "switch": switch_command(provider),
        "category": provider.get("category") or ("official" if is_official(provider) else "third_party"),
        "current": provider["id"] == active_id,
        "official": is_official(provider),
    }
    base_url = extract_base_url_from_provider(provider)
    if base_url:
        row["base_url"] = base_url
    website = provider.get("website_url")
    if website:
        row["website_url"] = website
    model = provider["config"].get("model")
    if isinstance(model, str) and model:
        row["model"] = model
    return row


def read_live_config(codex_home: Path) -> tuple[dict[str, Any], bool]:
    path = codex_home / "config.toml"
    if not path.exists():
        return {}, False
    return parse_toml(path.read_text(encoding="utf-8"), str(path)), True


def overlay_provider_config(live: dict[str, Any], provider: dict[str, Any]) -> dict[str, Any]:
    updated = copy.deepcopy(live)
    snapshot = provider["config"]
    if is_official(provider):
        for key in PROVIDER_OWNED_KEYS:
            updated.pop(key, None)
        return updated
    for key in PROVIDER_OWNED_KEYS:
        if key in snapshot:
            updated[key] = copy.deepcopy(snapshot[key])
        else:
            updated.pop(key, None)
    api_key = provider_api_key(provider.get("auth"))
    providers = updated.get("model_providers")
    if api_key and isinstance(providers, dict):
        for section in providers.values():
            if not isinstance(section, dict):
                continue
            if section.get("experimental_bearer_token"):
                continue
            if section.get("requires_openai_auth") is False:
                section["experimental_bearer_token"] = api_key
    return updated


def should_write_auth(provider: dict[str, Any], settings: dict[str, Any]) -> bool:
    if is_official(provider):
        return True
    return not bool(settings.get("preserveCodexOfficialAuthOnSwitch"))


def summarize_switch(provider: dict[str, Any], write_auth: bool, config_path: Path) -> dict[str, Any]:
    row = public_row(provider, provider["id"])
    row.update(
        {
            "write_auth_json": write_auth,
            "config_path": str(config_path),
            "model_provider": None if is_official(provider) else provider["config"].get("model_provider") or "custom",
        }
    )
    return row


def print_rows(rows: list[dict[str, Any]]) -> None:
    if not rows:
        print("CC Switch 里还没有 Codex 供应商。")
        return
    for row in rows:
        marker = "*" if row.get("current") else " "
        extra = f" | {row['base_url']}" if row.get("base_url") else ""
        model = f" | model:{row['model']}" if row.get("model") else ""
        label = row["name"]
        if row.get("switch") and row["switch"] != row["name"]:
            label = f"{row['switch']}  {row['name']}"
        print(f"{marker} {label} ({row['category']}){extra}{model}")


def command_list(args: argparse.Namespace) -> int:
    db = args.cc_switch_home / "cc-switch.db"
    settings = load_json(args.cc_switch_home / "settings.json", missing={})
    with open_db(db, readonly=True) as con:
        providers = load_providers(con, args.app)
    current = current_provider(providers, settings)
    active_id = current["id"] if current else None
    rows = [public_row(p, active_id) for p in providers]
    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
    else:
        print_rows(rows)
    return 0


def command_current(args: argparse.Namespace) -> int:
    db = args.cc_switch_home / "cc-switch.db"
    settings = load_json(args.cc_switch_home / "settings.json", missing={})
    with open_db(db, readonly=True) as con:
        providers = load_providers(con, args.app)
    current = current_provider(providers, settings)
    live, live_exists = read_live_config(args.codex_home)
    live_provider = live.get("model_provider")
    result: dict[str, Any] = {
        "app": args.app,
        "config_path": str(args.codex_home / "config.toml"),
        "config_exists": live_exists,
        "live_model_provider": live_provider,
        "preserve_official_auth": bool(settings.get("preserveCodexOfficialAuthOnSwitch")),
    }
    if current:
        result["provider"] = public_row(current, current["id"])
    else:
        result["provider"] = None
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    if current:
        print(f"当前 CC Switch Codex 供应商：{current['name']}")
        if result["provider"].get("base_url"):
            print(f"base_url：{result['provider']['base_url']}")
    else:
        print("当前没有标记为启用的 Codex 供应商。")
    print(f"Codex 配置：{result['config_path']}")
    print(f"live model_provider：{live_provider or '（默认官方）'}")
    return 0


def apply_switch(
    *,
    cc_switch_home: Path,
    codex_home: Path,
    backup_dir: Path,
    provider: dict[str, Any],
    settings_path: Path,
    settings: dict[str, Any],
    dry_run: bool,
) -> dict[str, Any]:
    live, _ = read_live_config(codex_home)
    updated = overlay_provider_config(live, provider)
    config_path = codex_home / "config.toml"
    auth_path = codex_home / "auth.json"
    write_auth = should_write_auth(provider, settings)
    summary = summarize_switch(provider, write_auth, config_path)
    if dry_run:
        summary["dry_run"] = True
        return summary

    backups = {
        "config": backup_file(config_path, backup_dir),
        "auth": backup_file(auth_path, backup_dir) if write_auth else None,
        "settings": backup_file(settings_path, backup_dir),
    }
    summary["backups"] = {key: str(path) for key, path in backups.items() if path}

    try:
        rendered = render_toml(updated) if updated else ""
        atomic_write_text(config_path, rendered if rendered else "\n")
        if write_auth:
            auth = provider.get("auth") or {}
            payload = json.dumps(auth, ensure_ascii=False, indent=2) + "\n"
            atomic_write_text(auth_path, payload)
        settings["currentProviderCodex"] = provider["id"]
        atomic_write_text(settings_path, json.dumps(settings, ensure_ascii=False, indent=2) + "\n")
        db_path = cc_switch_home / "cc-switch.db"
        with open_db(db_path, readonly=False) as con:
            con.execute("BEGIN")
            con.execute("UPDATE providers SET is_current = 0 WHERE app_type = ?", (APP_TYPE,))
            con.execute(
                "UPDATE providers SET is_current = 1 WHERE app_type = ? AND id = ?",
                (APP_TYPE, provider["id"]),
            )
            con.commit()
    except Exception:
        for key, path in (("config", config_path), ("auth", auth_path), ("settings", settings_path)):
            backup = backups.get(key)
            if backup and backup.exists():
                shutil.copy2(backup, path)
        raise
    return summary


def is_real_cc_switch_home(path: Path) -> bool:
    try:
        return path.expanduser().resolve() == default_cc_switch_home().resolve()
    except OSError:
        return False


def cc_switch_running() -> bool:
    return subprocess.run(["pgrep", "-x", "cc-switch"], capture_output=True).returncode == 0


def stop_cc_switch_app() -> bool:
    if not cc_switch_running():
        return False
    subprocess.run(["killall", "cc-switch"], check=False, capture_output=True)
    for _ in range(50):
        if not cc_switch_running():
            return True
        time.sleep(0.1)
    subprocess.run(["killall", "-9", "cc-switch"], check=False, capture_output=True)
    time.sleep(0.3)
    if cc_switch_running():
        raise CcswitchError("没法关掉正在运行的 CC Switch，未改配置。")
    return True


def start_cc_switch_app() -> None:
    if not Path("/Applications/CC Switch.app").exists():
        return
    opened = subprocess.run(["open", "-a", "CC Switch"], capture_output=True, text=True)
    if opened.returncode != 0:
        err = (opened.stderr or opened.stdout or "").strip()
        raise CcswitchError(f"配置已切换，但重新打开 CC Switch 失败：{err or opened.returncode}")
    for _ in range(40):
        if cc_switch_running():
            return
        time.sleep(0.1)


def wait_until_current(cc_switch_home: Path, provider_id: str, timeout: float = 8.0) -> bool:
    settings_path = cc_switch_home / "settings.json"
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            settings = load_json(settings_path, missing={})
            with open_db(cc_switch_home / "cc-switch.db", readonly=True) as con:
                providers = load_providers(con)
            current = current_provider(providers, settings)
            if current and current["id"] == provider_id:
                return True
        except CcswitchError:
            pass
        time.sleep(0.25)
    return False


def command_switch(args: argparse.Namespace) -> int:
    db = args.cc_switch_home / "cc-switch.db"
    settings_path = args.cc_switch_home / "settings.json"
    settings = load_json(settings_path, missing={})
    with open_db(db, readonly=True) as con:
        providers = load_providers(con, args.app)
    name = " ".join(args.name) if isinstance(args.name, list) else args.name
    provider = resolve_provider(providers, name)
    bounce_app = (
        not args.dry_run
        and not args.files_only
        and is_real_cc_switch_home(args.cc_switch_home)
    )
    try:
        if bounce_app:
            stop_cc_switch_app()
        summary = apply_switch(
            cc_switch_home=args.cc_switch_home,
            codex_home=args.codex_home,
            backup_dir=args.state_dir / "backups",
            provider=provider,
            settings_path=settings_path,
            settings=settings,
            dry_run=args.dry_run,
        )
        if bounce_app:
            start_cc_switch_app()
            if not wait_until_current(args.cc_switch_home, provider["id"]):
                raise CcswitchError(
                    f"配置已写成 {provider['name']}，但 CC Switch 重新打开后没有保持这个供应商。"
                )
            summary["app"] = "reloaded"
    except Exception:
        if bounce_app and not cc_switch_running():
            try:
                start_cc_switch_app()
            except CcswitchError:
                pass
        raise
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0
    verb = "将切换到" if args.dry_run else "已切换到"
    print(f"{verb}：{provider['name']}")
    if summary.get("base_url"):
        print(f"base_url：{summary['base_url']}")
    print(f"model_provider：{summary['model_provider'] or '（默认官方）'}")
    print("auth.json：" + ("会写入该供应商登录信息" if summary["write_auth_json"] else "保留当前官方登录，不覆盖"))
    if args.dry_run:
        print("这是预览，没有改任何文件。")
    elif summary.get("backups"):
        print("已备份：" + ", ".join(summary["backups"].values()))
    if summary.get("app") == "reloaded":
        print("已重新打开 CC Switch，托盘会显示当前供应商。")
        print("Codex 需要新开一次才会用新接口。")
    return 0


def doctor_report(args: argparse.Namespace) -> dict[str, Any]:
    report: dict[str, Any] = {
        "cc_switch_home": str(args.cc_switch_home),
        "codex_home": str(args.codex_home),
        "checks": [],
    }

    def check(name: str, ok: bool, detail: str) -> None:
        report["checks"].append({"name": name, "ok": ok, "detail": detail})

    db = args.cc_switch_home / "cc-switch.db"
    check("cc_switch_db", db.exists(), str(db))
    settings_path = args.cc_switch_home / "settings.json"
    check("cc_switch_settings", settings_path.exists(), str(settings_path))
    if not db.exists():
        report["ok"] = False
        return report
    try:
        settings = load_json(settings_path, missing={})
        with open_db(db, readonly=True) as con:
            providers = load_providers(con, args.app)
        check("codex_providers", bool(providers), f"{len(providers)} 个")
        current = current_provider(providers, settings)
        check("current_marked", current is not None, current["name"] if current else "未标记")
        live, exists = read_live_config(args.codex_home)
        check("codex_config", True, "可读取" if exists else "尚不存在")
        live_provider = live.get("model_provider")
        if current and is_official(current):
            check("live_matches", live_provider in (None, "", "openai"), f"live={live_provider or 'default'}")
        elif current:
            expected = current["config"].get("model_provider") or "custom"
            check("live_matches", live_provider == expected, f"live={live_provider} expected={expected}")
        auth_path = args.codex_home / "auth.json"
        check("auth_file", auth_path.exists(), str(auth_path))
    except CcswitchError as exc:
        check("load", False, str(exc))
    report["ok"] = all(item["ok"] for item in report["checks"])
    return report


def command_doctor(args: argparse.Namespace) -> int:
    report = doctor_report(args)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        for item in report["checks"]:
            print(("PASS" if item["ok"] else "FAIL") + f" {item['name']}: {item['detail']}")
        print("doctor 结果：" + ("通过" if report["ok"] else "需要处理"))
    return 0 if report["ok"] else 1


def add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--cc-switch-home", type=Path, default=default_cc_switch_home(), help="CC Switch 数据目录")
    parser.add_argument("--codex-home", type=Path, default=default_codex_home(), help="Codex 配置目录")
    parser.add_argument("--state-dir", type=Path, default=default_state_dir(), help="CLI 备份目录")
    parser.add_argument("--app", default=APP_TYPE, help="应用类型，默认 codex")
    parser.add_argument("--files-only", action="store_true", help="只改配置文件，不点 CC Switch 托盘")


KNOWN_COMMANDS = {
    "list",
    "ls",
    "provider",
    "providers",
    "current",
    "status",
    "switch",
    "use",
    "doctor",
}
VALUE_OPTIONS = {"--cc-switch-home", "--codex-home", "--state-dir", "--app"}
FLAG_OPTIONS = {"--json", "--dry-run", "--files-only", "-h", "--help"}


def normalize_argv(argv: list[str] | None) -> list[str]:
    args = list(sys.argv[1:] if argv is None else argv)
    index = 0
    while index < len(args):
        token = args[index]
        if token in VALUE_OPTIONS:
            index += 2
            continue
        if token.startswith("--") and "=" in token:
            index += 1
            continue
        if token in FLAG_OPTIONS or token.startswith("-"):
            index += 1
            continue
        if token in KNOWN_COMMANDS:
            return args
        return args[:index] + ["switch"] + args[index:]
    if any(token in {"-h", "--help"} for token in args):
        return args
    return args + ["list"]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ccswitchcli",
        description="操作本地 CC Switch：列出并切换 Codex 供应商（例如 sub2api / OpenAI Official）。",
    )
    add_common(parser)
    sub = parser.add_subparsers(dest="command", required=True)

    list_parser = sub.add_parser("list", aliases=["ls", "provider", "providers"], help="列出 CC Switch 中的 Codex 供应商")
    list_parser.add_argument("--json", action="store_true")
    list_parser.set_defaults(func=command_list)

    current = sub.add_parser("current", aliases=["status"], help="查看当前 Codex 供应商")
    current.add_argument("--json", action="store_true")
    current.set_defaults(func=command_current)

    switch = sub.add_parser("switch", aliases=["use"], help="切换 Codex 供应商")
    switch.add_argument("name", nargs="+", help="供应商名称，例如 sub2api 或 openai-official")
    switch.add_argument("--dry-run", action="store_true")
    switch.add_argument("--json", action="store_true")
    switch.set_defaults(func=command_switch)

    doctor = sub.add_parser("doctor", help="检查 CC Switch 与 Codex 配置是否一致")
    doctor.add_argument("--json", action="store_true")
    doctor.set_defaults(func=command_doctor)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(normalize_argv(argv))
    try:
        return int(args.func(args))
    except CcswitchError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2
    except sqlite3.Error as exc:
        print(f"错误：数据库操作失败：{exc}", file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"错误：文件操作失败：{exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
