import unittest
from unittest.mock import Mock, patch

from eat.browser_policy import AuthorizedEatBrowser, open_authorized_eat_browser


class BrowserPolicyTests(unittest.TestCase):
    @patch("eat.browser_policy.save_eat_session")
    @patch("eat.browser_policy.PlaywrightLoginUI")
    @patch("eat.browser_policy.has_saved_session", return_value=True)
    def test_saved_session_starts_headless(self, saved, ui, persist):
        pw=Mock(); browser=Mock(); context=Mock(); page=Mock()
        pw.chromium.launch.return_value=browser
        browser.new_context.return_value=context; context.new_page.return_value=page
        ui.return_value.signed_in.return_value=True
        result=open_authorized_eat_browser(pw)
        pw.chromium.launch.assert_called_once_with(headless=True,args=[])
        self.assertFalse(result.interactive); persist.assert_called_once_with(context)

    @patch("eat.browser_policy._minimize_chromium")
    @patch("eat.browser_policy.save_eat_session")
    @patch("eat.browser_policy.PlaywrightLoginUI")
    @patch("eat.browser_policy.has_saved_session", return_value=False)
    def test_user_action_uses_visible_window(self, saved, ui, persist, minimize):
        pw=Mock(); browser=Mock(); context=Mock(); page=Mock()
        pw.chromium.launch.return_value=browser
        browser.new_context.return_value=context; context.new_page.return_value=page
        ui.return_value.signed_in.side_effect=[False,True,True]
        result=open_authorized_eat_browser(pw,timeout_seconds=1,notify=Mock())
        pw.chromium.launch.assert_called_once_with(headless=False,args=["--start-minimized"])
        page.bring_to_front.assert_called_once(); self.assertTrue(result.interactive)
        self.assertGreaterEqual(minimize.call_count,2)

    @patch("eat.browser_policy.save_eat_session")
    def test_close_saves_before_browser_close(self, persist):
        browser,context=Mock(),Mock()
        AuthorizedEatBrowser(browser,context,Mock(),False).close()
        persist.assert_called_once_with(context)
        context.close.assert_called_once();browser.close.assert_called_once()


if __name__ == "__main__": unittest.main()
