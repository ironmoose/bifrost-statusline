"""Tests for exact Codex rollout selection and desktop status presentation."""

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
DESKTOP_STATUS_PATH = ROOT / "desktop_status.py"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


desktop_status = _load("desktop_status", DESKTOP_STATUS_PATH)

SESSION_ID = "123e4567-e89b-12d3-a456-426614174000"
OTHER_SESSION_ID = "123e4567-e89b-12d3-a456-426614174001"
NOW = 1_700_000_000.0


def _snapshot(**overrides):
    snapshot = {
        "model": {"display_name": "gpt-5-codex"},
        "cwd": "/workspace/demo",
        "context_window": {
            "used_percentage": 42.5,
            "context_window_size": 128_000,
            "total_input_tokens": 53_000,
            "total_output_tokens": 1_400,
        },
        "rate_limits": {
            "five_hour": {"used_percentage": 12.5, "resets_at": NOW + 3600, "stale": False},
            "seven_day": {"used_percentage": 40.0, "resets_at": NOW + 86_400, "stale": False},
        },
    }
    snapshot.update(overrides)
    return snapshot


class FindRolloutTests(unittest.TestCase):
    def setUp(self):
        self.codex_home = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.sessions = self.codex_home / "sessions"

    def _rollout(self, name: str) -> Path:
        path = self.sessions / "2026" / "10" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}\n")
        return path

    def test_finds_rollout_for_exact_session_uuid(self):
        expected = self._rollout(f"rollout-2026-10-18T12-00-00-{SESSION_ID}.jsonl")
        self._rollout(f"rollout-2026-10-18T12-01-00-{OTHER_SESSION_ID}.jsonl")

        self.assertEqual(desktop_status.find_rollout(self.codex_home, SESSION_ID), expected)

    def test_returns_none_when_session_has_no_rollout(self):
        self._rollout(f"rollout-2026-10-18T12-00-00-{OTHER_SESSION_ID}.jsonl")

        self.assertIsNone(desktop_status.find_rollout(self.codex_home, SESSION_ID))

    def test_rejects_non_uuid_session_id(self):
        with self.assertRaises(ValueError):
            desktop_status.find_rollout(self.codex_home, "latest")

    def test_reports_ambiguous_rollouts_for_same_session(self):
        self._rollout(f"rollout-2026-10-18T12-00-00-{SESSION_ID}.jsonl")
        self._rollout(f"rollout-2026-10-18T12-01-00-{SESSION_ID}.jsonl")

        with self.assertRaises(ValueError):
            desktop_status.find_rollout(self.codex_home, SESSION_ID)


class PresentStatusTests(unittest.TestCase):
    def test_returns_ready_status_with_context_and_reset_fields(self):
        result = desktop_status.present_status(_snapshot(), now=NOW, updated_at=NOW - 10)

        self.assertEqual(result["state"], "ready")
        self.assertEqual(result["model"], "gpt-5-codex")
        self.assertEqual(result["context_percent"], 42.5)
        self.assertEqual(result["used_tokens"], 54_400)
        self.assertEqual(result["window_tokens"], 128_000)
        self.assertEqual(len(result["usage"]), 2)
        self.assertTrue(all(isinstance(item["label"], str) for item in result["usage"]))
        self.assertEqual([item["used_percent"] for item in result["usage"]], [12.5, 40.0])
        self.assertTrue(all(isinstance(item["reset_text"], str) for item in result["usage"]))
        self.assertTrue(all(item["stale"] is False for item in result["usage"]))

    def test_keeps_missing_metrics_unknown_instead_of_zero(self):
        snapshot = _snapshot(
            context_window={"used_percentage": None, "total_input_tokens": 0,
                            "total_output_tokens": 0, "context_window_size": None},
            rate_limits={},
        )

        result = desktop_status.present_status(snapshot, now=NOW, updated_at=NOW)

        self.assertIsNone(result["context_percent"])
        self.assertIsNone(result["used_tokens"])
        self.assertIsNone(result["window_tokens"])
        self.assertEqual(result["usage"], [])

    def test_excludes_missing_windows_and_marks_expired_windows_stale(self):
        snapshot = _snapshot(rate_limits={
            "five_hour": {"used_percentage": 100.0, "resets_at": NOW - 1, "stale": True},
        })

        result = desktop_status.present_status(snapshot, now=NOW, updated_at=NOW)

        self.assertEqual(len(result["usage"]), 1)
        self.assertEqual(result["usage"][0]["used_percent"], 100.0)
        self.assertTrue(result["usage"][0]["stale"])
        self.assertIsInstance(result["usage"][0]["reset_text"], str)

    def test_marks_old_rollout_stale_without_discarding_its_metrics(self):
        result = desktop_status.present_status(_snapshot(), now=NOW, updated_at=NOW - 121)

        self.assertEqual(result["state"], "stale")
        self.assertEqual(result["context_percent"], 42.5)
        self.assertIsInstance(result["detail"], str)

    def test_empty_normalized_status_is_waiting_with_unknown_metrics(self):
        result = desktop_status.present_status({}, now=NOW)

        self.assertEqual(result["state"], "waiting")
        self.assertIsNone(result["context_percent"])


class LoadStatusTests(unittest.TestCase):
    def setUp(self):
        self.directory = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.path = self.directory / f"rollout-2026-10-18T12-00-00-{SESSION_ID}.jsonl"

    def test_missing_rollout_is_waiting(self):
        result = desktop_status.load_status(None, now=NOW)

        self.assertEqual(result["state"], "waiting")
        self.assertIsNone(result["context_percent"])

    def test_unreadable_rollout_is_unavailable(self):
        result = desktop_status.load_status(self.path, now=NOW)

        self.assertEqual(result["state"], "unavailable")
        self.assertIsNone(result["context_percent"])

    def test_reads_and_normalizes_a_real_rollout_fixture(self):
        info = {
            "last_token_usage": {"total_tokens": 50, "input_tokens": 45, "output_tokens": 5},
            "model_context_window": 200,
        }
        self.path.write_text(
            json.dumps({"type": "turn_context", "payload": {"model": "gpt-5-codex"}}) + "\n"
            + json.dumps({"type": "event_msg", "payload": {
                "type": "token_count", "info": info,
                "rate_limits": {"primary": {"used_percent": 25, "window_minutes": 300,
                                               "resets_at": NOW + 3600}},
            }}) + "\n"
        )
        result = desktop_status.load_status(self.path, now=NOW)

        self.assertEqual(result["state"], "ready")
        self.assertEqual(result["context_percent"], 25.0)
        self.assertEqual(result["used_tokens"], 50)
        self.assertEqual(result["window_tokens"], 200)
        self.assertEqual(result["model"], "gpt-5-codex")

    def test_recovers_model_before_parser_tail_while_using_recent_token_metrics(self):
        model = "gpt-5-codex-long-history"
        turn_context = json.dumps({"type": "turn_context", "payload": {"model": model}}) + "\n"
        filler = '{"type":"ignored","payload":{}}\n' * 145_000
        info = {
            "last_token_usage": {"total_tokens": 75, "input_tokens": 70, "output_tokens": 5},
            "model_context_window": 300,
        }
        recent_token_count = json.dumps({"type": "event_msg", "payload": {
            "type": "token_count", "info": info, "rate_limits": {},
        }}) + "\n"
        self.path.write_text(turn_context + filler + recent_token_count)

        result = desktop_status.load_status(self.path)

        self.assertGreater(self.path.stat().st_size, 4_500_000)
        self.assertEqual(result["model"], model)
        self.assertEqual(result["context_percent"], 25.0)
        self.assertEqual(result["used_tokens"], 75)
        self.assertEqual(result["window_tokens"], 300)

        updated_model = "gpt-5-codex-new-turn"
        with self.path.open("a") as rollout:
            rollout.write(json.dumps({"type": "turn_context", "payload": {"model": updated_model}}) + "\n")

        updated_result = desktop_status.load_status(self.path)

        self.assertEqual(updated_result["model"], updated_model)
        self.assertEqual(updated_result["context_percent"], 25.0)

    def test_rechecks_model_after_rollout_grows_from_cached_missing_context(self):
        filler = '{"type":"ignored","payload":{}}\n' * 35_000
        info = {
            "last_token_usage": {"total_tokens": 60, "input_tokens": 55, "output_tokens": 5},
            "model_context_window": 300,
        }
        token_event = json.dumps({"type": "event_msg", "payload": {
            "type": "token_count", "info": info, "rate_limits": {},
        }}) + "\n"
        self.path.write_text(filler + token_event)

        first_result = desktop_status.load_status(self.path, now=self.path.stat().st_mtime)

        self.assertEqual(first_result["state"], "ready")
        self.assertEqual(first_result["context_percent"], 20.0)

        updated_model = "gpt-5-codex-after-growth"
        with self.path.open("a") as rollout:
            rollout.write(json.dumps({"type": "turn_context", "payload": {"model": updated_model}}) + "\n")
            rollout.write(filler)
            rollout.write(token_event)

        updated_result = desktop_status.load_status(self.path, now=self.path.stat().st_mtime)

        self.assertEqual(updated_result["model"], updated_model)
        self.assertEqual(updated_result["context_percent"], 20.0)


if __name__ == "__main__":
    unittest.main()
