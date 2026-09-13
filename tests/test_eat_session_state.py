import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from eat.session_state import has_saved_session, new_eat_context, save_eat_session


class Browser:
    def __init__(self): self.options = None
    def new_context(self, **options):
        self.options = options
        return options


class Context:
    def storage_state(self, *, path, indexed_db=False):
        Path(path).write_text(json.dumps({"cookies": [{"name": "secret"}], "origins": []}))


class SessionStateTests(unittest.TestCase):
    def test_invalid_state_is_not_loaded(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "state.json"
            path.write_text("not-json")
            with patch.dict(os.environ, {"EAT_SESSION_STATE_PATH": str(path)}):
                browser = Browser()
                new_eat_context(browser, locale="ru-RU")
                self.assertNotIn("storage_state", browser.options)

    def test_valid_state_is_reused_without_reading_values(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "state.json"
            path.write_text(json.dumps({"cookies": [], "origins": []}))
            with patch.dict(os.environ, {"EAT_SESSION_STATE_PATH": str(path)}):
                browser = Browser()
                new_eat_context(browser, locale="ru-RU")
                self.assertEqual(browser.options["storage_state"], str(path))
                self.assertTrue(has_saved_session())

    def test_saved_state_is_private_and_atomic(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "private" / "state.json"
            with patch.dict(os.environ, {"EAT_SESSION_STATE_PATH": str(path)}):
                save_eat_session(Context())
                self.assertTrue(path.exists())
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)
                self.assertEqual(path.parent.stat().st_mode & 0o777, 0o700)
                self.assertFalse(path.with_name(f".{path.name}.tmp").exists())

    def test_manual_save_command_dispatch(self):
        import main
        with patch("sys.argv", ["main.py", "--eat-save-session"]), \
             patch("eat.session_state.run_manual_session_save", return_value=0) as run:
            self.assertEqual(main.main(), 0)
        run.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
