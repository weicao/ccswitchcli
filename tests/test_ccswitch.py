import contextlib
import io
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import ccswitch


OFFICIAL_ID = "official-id"
SUB2API_ID = "sub2api-id"

SUB2API_TOML = """\
model = "gpt-6-astra"
model_provider = "custom"
disable_response_storage = true

[model_providers.custom]
name = "custom"
base_url = "https://sub2api.example/v1"
wire_api = "responses"
requires_openai_auth = false
"""

LIVE_TOML = """\
model_reasoning_effort = "xhigh"

[projects."/tmp/demo"]
trust_level = "trusted"

[plugins."computer-use@openai-bundled"]
enabled = true
"""


class CcswitchCliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.cc_home = self.root / "cc-switch"
        self.codex = self.root / "codex"
        self.claude = self.root / "claude"
        self.grok = self.root / "grok"
        self.state = self.root / "state"
        self.cc_home.mkdir()
        self.codex.mkdir()
        self.claude.mkdir()
        self.grok.mkdir()
        self.db = self.cc_home / "cc-switch.db"
        self._seed_db()
        self._write_settings(OFFICIAL_ID, preserve=False)
        (self.codex / "config.toml").write_text(LIVE_TOML)

    def tearDown(self):
        self.tmp.cleanup()

    def _seed_db(self):
        con = sqlite3.connect(self.db)
        con.execute(
            """
            CREATE TABLE providers (
                id TEXT NOT NULL,
                app_type TEXT NOT NULL,
                name TEXT NOT NULL,
                settings_config TEXT NOT NULL,
                website_url TEXT,
                category TEXT,
                sort_index INTEGER,
                is_current BOOLEAN NOT NULL DEFAULT 0,
                PRIMARY KEY (id, app_type)
            )
            """
        )
        official = {
            "auth": {"auth_mode": "chatgpt", "OPENAI_API_KEY": None, "tokens": {"access_token": "tok-official"}},
            "config": "",
        }
        sub2api = {
            "auth": {"OPENAI_API_KEY": "sk-test-sub2api", "auth_mode": "apikey"},
            "config": SUB2API_TOML,
        }
        con.execute(
            "INSERT INTO providers VALUES (?,?,?,?,?,?,?,?)",
            (OFFICIAL_ID, "codex", "OpenAI Official", json.dumps(official), "https://chatgpt.com/codex", "official", 1, 1),
        )
        con.execute(
            "INSERT INTO providers VALUES (?,?,?,?,?,?,?,?)",
            (SUB2API_ID, "codex", "sub2api", json.dumps(sub2api), "https://sub2api.example", None, 2, 0),
        )
        claude = {"env": {"ANTHROPIC_BASE_URL": "https://www.packyapi.com", "ANTHROPIC_AUTH_TOKEN": "sk-claude-test"}}
        grok = {
            "config": """\
[models]
default = "grok-4.6"

[model."grok-4.6"]
model = "grok-4.6"
base_url = "https://grok.example/v1"
name = "sub2api"
api_key = "sk-grok-test"
"""
        }
        con.execute(
            "INSERT INTO providers VALUES (?,?,?,?,?,?,?,?)",
            ("claude-1", "claude", "PackyCode", json.dumps(claude), "https://www.packyapi.com", "third_party", 1, 1),
        )
        con.execute(
            "INSERT INTO providers VALUES (?,?,?,?,?,?,?,?)",
            ("grok-1", "grokbuild", "sub2api", json.dumps(grok), None, "custom", 1, 1),
        )
        con.commit()
        con.close()

    def _write_settings(self, current_id, preserve=False):
        payload = {
            "currentProviderCodex": current_id,
            "preserveCodexOfficialAuthOnSwitch": preserve,
        }
        (self.cc_home / "settings.json").write_text(json.dumps(payload, indent=2))

    def run_cli(self, *args):
        out = io.StringIO()
        err = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = ccswitch.main(
                [
                    "--cc-switch-home",
                    str(self.cc_home),
                    "--codex-home",
                    str(self.codex),
                    "--claude-home",
                    str(self.claude),
                    "--grok-home",
                    str(self.grok),
                    "--state-dir",
                    str(self.state),
                    *args,
                ]
            )
        return code, out.getvalue(), err.getvalue()

    def test_version_and_help(self):
        code, out, err = self.run_cli("version")
        self.assertEqual(code, 0, err)
        self.assertIn("0.1", out)
        code, out, err = self.run_cli("help")
        self.assertEqual(code, 0, err)
        self.assertIn("ccswitchcli 0.1", out)
        self.assertIn("ccswitchcli APP PROVIDER", out)

    def test_list_shows_both_providers(self):
        code, out, err = self.run_cli("list")
        self.assertEqual(code, 0, err)
        self.assertIn("OpenAI Official", out)
        self.assertIn("openai-official", out)
        self.assertIn("sub2api", out)
        self.assertIn("https://sub2api.example/v1", out)

    def test_switch_sub2api_merges_routing_and_keeps_live_settings(self):
        code, out, err = self.run_cli("switch", "sub2api")
        self.assertEqual(code, 0, err)
        data = ccswitch.tomllib.loads((self.codex / "config.toml").read_text())
        self.assertEqual(data["model_provider"], "custom")
        self.assertEqual(data["model"], "gpt-6-astra")
        self.assertEqual(data["model_providers"]["custom"]["base_url"], "https://sub2api.example/v1")
        self.assertEqual(data["model_providers"]["custom"]["experimental_bearer_token"], "sk-test-sub2api")
        self.assertEqual(data["projects"]["/tmp/demo"]["trust_level"], "trusted")
        self.assertTrue(data["plugins"]["computer-use@openai-bundled"]["enabled"])
        self.assertEqual(data["model_reasoning_effort"], "xhigh")
        auth = json.loads((self.codex / "auth.json").read_text())
        self.assertEqual(auth["OPENAI_API_KEY"], "sk-test-sub2api")
        settings = json.loads((self.cc_home / "settings.json").read_text())
        self.assertEqual(settings["currentProviderCodex"], SUB2API_ID)
        con = sqlite3.connect(self.db)
        rows = dict(con.execute("SELECT name, is_current FROM providers WHERE app_type='codex'"))
        con.close()
        self.assertEqual(rows["sub2api"], 1)
        self.assertEqual(rows["OpenAI Official"], 0)
        self.assertNotIn("sk-test-sub2api", out)

    def test_switch_openai_official_clears_custom_provider(self):
        self.run_cli("switch", "sub2api")
        code, out, err = self.run_cli("switch", "openai-official")
        self.assertEqual(code, 0, err)
        data = ccswitch.tomllib.loads((self.codex / "config.toml").read_text())
        self.assertNotIn("model_provider", data)
        self.assertNotIn("model_providers", data)
        self.assertEqual(data["projects"]["/tmp/demo"]["trust_level"], "trusted")
        auth = json.loads((self.codex / "auth.json").read_text())
        self.assertEqual(auth["tokens"]["access_token"], "tok-official")
        self.assertIn("OpenAI Official", out)

    def test_preserve_official_auth_skips_auth_json(self):
        self._write_settings(OFFICIAL_ID, preserve=True)
        (self.codex / "auth.json").write_text(json.dumps({"auth_mode": "chatgpt", "tokens": {"access_token": "keep-me"}}))
        code, out, err = self.run_cli("switch", "sub2api")
        self.assertEqual(code, 0, err)
        auth = json.loads((self.codex / "auth.json").read_text())
        self.assertEqual(auth["tokens"]["access_token"], "keep-me")
        data = ccswitch.tomllib.loads((self.codex / "config.toml").read_text())
        self.assertEqual(data["model_providers"]["custom"]["experimental_bearer_token"], "sk-test-sub2api")
        self.assertIn("保留当前官方登录", out)

    def test_dry_run_does_not_write(self):
        before = (self.codex / "config.toml").read_text()
        code, out, err = self.run_cli("switch", "sub2api", "--dry-run")
        self.assertEqual(code, 0, err)
        self.assertEqual((self.codex / "config.toml").read_text(), before)
        self.assertFalse((self.codex / "auth.json").exists())
        self.assertIn("预览", out)
        con = sqlite3.connect(self.db)
        current = con.execute("SELECT name FROM providers WHERE is_current=1").fetchone()[0]
        con.close()
        self.assertEqual(current, "OpenAI Official")

    def test_openai_official_accepts_spaced_name_and_typo(self):
        code, out, err = self.run_cli("OpenAI", "Official", "--dry-run")
        self.assertEqual(code, 0, err)
        self.assertIn("OpenAI Official", out)
        code, out, err = self.run_cli("openai-offical", "--dry-run")
        self.assertEqual(code, 0, err)
        self.assertIn("OpenAI Official", out)

    def test_list_groups_claude_and_grok(self):
        code, out, err = self.run_cli()
        self.assertEqual(code, 0, err)
        self.assertIn("[Claude Code]", out)
        self.assertIn("[Grok]", out)
        self.assertIn("PackyCode", out)

    def test_switch_claude_writes_settings_env(self):
        (self.claude / "settings.json").write_text(json.dumps({"theme": "dark", "env": {"KEEP": "yes"}}))
        code, out, err = self.run_cli("cc", "PackyCode")
        self.assertEqual(code, 0, err)
        data = json.loads((self.claude / "settings.json").read_text())
        self.assertEqual(data["theme"], "dark")
        self.assertEqual(data["env"]["KEEP"], "yes")
        self.assertEqual(data["env"]["ANTHROPIC_BASE_URL"], "https://www.packyapi.com")
        self.assertEqual(data["env"]["ANTHROPIC_AUTH_TOKEN"], "sk-claude-test")
        self.assertNotIn("sk-claude-test", out)

    def test_switch_grok_merges_model_and_keeps_ui(self):
        (self.grok / "config.toml").write_text('[ui]\nyolo = true\n')
        code, out, err = self.run_cli("grok", "sub2api")
        self.assertEqual(code, 0, err)
        data = ccswitch.tomllib.loads((self.grok / "config.toml").read_text())
        self.assertTrue(data["ui"]["yolo"])
        self.assertEqual(data["model"]["grok-4.6"]["base_url"], "https://grok.example/v1")
        self.assertNotIn("sk-grok-test", out)

    def test_switch_away_saves_live_codex_login(self):
        (self.codex / "auth.json").write_text(
            json.dumps({"auth_mode": "chatgpt", "tokens": {"access_token": "tok-new-login"}})
        )
        code, out, err = self.run_cli("switch", "sub2api")
        self.assertEqual(code, 0, err)
        con = sqlite3.connect(self.db)
        saved = json.loads(con.execute("SELECT settings_config FROM providers WHERE id=?", (OFFICIAL_ID,)).fetchone()[0])
        con.close()
        self.assertEqual(saved["auth"]["tokens"]["access_token"], "tok-new-login")
        code, out, err = self.run_cli("switch", "openai-official")
        self.assertEqual(code, 0, err)
        live = json.loads((self.codex / "auth.json").read_text())
        self.assertEqual(live["tokens"]["access_token"], "tok-new-login")
        self.assertNotIn("tok-new-login", out)

    def test_unknown_provider_is_rejected(self):
        code, out, err = self.run_cli("switch", "not-a-provider")
        self.assertEqual(code, 2)
        self.assertIn("找不到供应商", err)

    def test_bare_name_is_a_switch_command(self):
        code, out, err = self.run_cli("sub2api", "--dry-run")
        self.assertEqual(code, 0, err)
        self.assertIn("将切换到：sub2api", out)
        self.assertFalse((self.codex / "auth.json").exists())

    def test_no_args_lists_providers(self):
        code, out, err = self.run_cli()
        self.assertEqual(code, 0, err)
        self.assertIn("OpenAI Official", out)
        self.assertIn("sub2api", out)

    def test_fake_home_switch_does_not_use_tray(self):
        code, out, err = self.run_cli("switch", "sub2api")
        self.assertEqual(code, 0, err)
        self.assertNotIn("托盘", out)
        self.assertNotIn("辅助功能", err)


if __name__ == "__main__":
    unittest.main()
