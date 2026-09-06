"""Exercise config preservation across common TOML layouts, plus the
--prefix installation flow that lays down a Codex-launchable copy of
Bifrost (bin/bifrost, share/bifrost/{bifrost,codex_status,statusline}.py).
"""
import importlib.util
import os
import subprocess
import sys
import tempfile
from pathlib import Path
import tomllib
import unittest

ROOT = Path(__file__).resolve().parents[1]
INSTALLER_PATH = ROOT / "install-codex.py"

spec = importlib.util.spec_from_file_location("install_codex", INSTALLER_PATH)
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


def _run_installer(args, env):
    return subprocess.run(
        [sys.executable, str(INSTALLER_PATH), *args],
        capture_output=True, text=True, timeout=10, env=env,
    )


class ConfigureTests(unittest.TestCase):
    def test_preserves_unrelated_values_and_is_idempotent(self):
        for raw in [
            "",
            '[tui]',
            '[tui.model_availability_nux]\nexample = 1\n',
            'approval_policy = "never"\n[tui] # footer\ntheme = "dark"\n'
            'status_line = [\n "model", # existing\n "git-branch",\n]\n'
            '[mcp_servers.example]\nurl = "https://example.com/mcp"\n',
        ]:
            with self.subTest(raw=raw):
                expected = tomllib.loads(raw)
                expected.setdefault("tui", {})["status_line"] = installer.ITEMS
                result = installer.configure(raw)
                self.assertEqual(tomllib.loads(result), expected)
                self.assertEqual(installer.configure(result), result)

    def test_invalid_config_is_rejected(self):
        with self.assertRaises(ValueError):
            installer.configure('[tui]\nstatus_line = [')

    def test_dotted_status_line_is_rejected_without_rewrite(self):
        with self.assertRaises(ValueError):
            installer.configure('tui.status_line = ["model"]\n')


class PrefixInstallTests(unittest.TestCase):
    """Default `install-codex.py --prefix PATH` lays down a self-contained
    bin/share tree instead of touching the user's Codex config."""

    def setUp(self):
        self.prefix = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.home = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.codex_home = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.env = dict(os.environ, HOME=str(self.home), CODEX_HOME=str(self.codex_home))
        self.sentinel = self.prefix / "unrelated.txt"
        self.sentinel.write_text("keep me\n")
        self.result = _run_installer(["--prefix", str(self.prefix)], self.env)

    def test_creates_bin_launcher_and_share_modules(self):
        self.assertEqual(self.result.returncode, 0, self.result.stderr)
        launcher = self.prefix / "bin" / "bifrost"
        self.assertTrue(launcher.exists())
        self.assertTrue(os.access(launcher, os.X_OK))
        share = self.prefix / "share" / "bifrost"
        for name in ("bifrost.py", "codex_status.py", "statusline.py"):
            with self.subTest(name=name):
                self.assertTrue((share / name).exists())

    def test_default_install_does_not_touch_codex_config(self):
        self.assertEqual(self.result.returncode, 0, self.result.stderr)
        self.assertFalse((self.codex_home / "config.toml").exists())

    def test_preserves_unrelated_prefix_files(self):
        self.assertEqual(self.result.returncode, 0, self.result.stderr)
        self.assertEqual(self.sentinel.read_text(), "keep me\n")

    def test_launcher_runs_status_for_missing_state(self):
        self.assertEqual(self.result.returncode, 0, self.result.stderr)
        launcher = self.prefix / "bin" / "bifrost"
        missing_state = self.prefix / "no-such-state"
        result = subprocess.run(
            [str(launcher), "status", "--state", str(missing_state)],
            capture_output=True, text=True, timeout=5,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("waiting", result.stdout.lower())

    def test_repeat_install_is_byte_identical(self):
        self.assertEqual(self.result.returncode, 0, self.result.stderr)
        share = self.prefix / "share" / "bifrost"
        before = {name: (share / name).read_bytes() for name in ("bifrost.py", "codex_status.py", "statusline.py")}
        before_launcher = (self.prefix / "bin" / "bifrost").read_bytes()

        repeat = _run_installer(["--prefix", str(self.prefix)], self.env)

        self.assertEqual(repeat.returncode, 0, repeat.stderr)
        for name, content in before.items():
            with self.subTest(name=name):
                self.assertEqual((share / name).read_bytes(), content)
        self.assertEqual((self.prefix / "bin" / "bifrost").read_bytes(), before_launcher)


class NativeFooterCompatTests(unittest.TestCase):
    """--native-footer keeps installing the old Codex config.toml preset,
    unchanged, for anyone not opting into the --prefix launcher."""

    def test_native_footer_flag_retains_config_preset_behavior(self):
        home = Path(self.enterContext(tempfile.TemporaryDirectory()))
        config_path = home / "custom-config.toml"
        env = dict(os.environ, HOME=str(home))

        result = _run_installer(["--native-footer", "--config", str(config_path)], env)

        self.assertEqual(result.returncode, 0, result.stderr)
        expected = installer.configure("")
        self.assertEqual(config_path.read_text(), expected)


if __name__ == "__main__":
    unittest.main()
