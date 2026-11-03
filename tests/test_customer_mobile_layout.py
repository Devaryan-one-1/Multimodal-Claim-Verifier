"""Customer menu authorization and native-navigation behavior; no live services."""
from html.parser import HTMLParser
import math
from unittest.mock import MagicMock, patch
import unittest

import test_customer_beta as beta_fixtures
import test_customer_ui_refinements as ui_fixtures
import test_customer_account_security as security_fixtures
from test_customer_beta import APP, StopPage, RerunPage, load_module
from pilot import private_media, store


class CustomerSettingsTests(unittest.TestCase):
    setUp = beta_fixtures.CustomerAuthTests.setUp

    def test_settings_receives_current_authorized_workspace_without_clearing_drafts(self):
        self.auth.require_access()
        second = "b"*32
        self.memberships.return_value.append({"id": second, "name": "Second workspace"})
        self.st.session_state.update(customer_workspace=second, draft_customer_a_claim="Keep A draft")
        body = MagicMock()
        access = self.auth.require_access(settings_body=body)
        body.assert_called_once_with(access)
        self.assertEqual(access.workspace, second)
        self.assertEqual(self.st.session_state["draft_customer_a_claim"], "Keep A draft")
        self.assertEqual(self.st.session_state[self.auth.SESSION]["refresh_token"], "rotated-refresh")

    def test_settings_is_never_rendered_before_authentication_and_membership(self):
        saved = dict(self.st.session_state)
        cases = ("no_session", "invalid_session", "uninvited", "membership_error")
        for case in cases:
            with self.subTest(case=case):
                self.st.session_state.clear()
                self.st.session_state.update(saved)
                self.st.events.clear()
                self.client.auth.get_user.side_effect = RuntimeError("Unavailable") if case == "invalid_session" else None
                self.memberships.side_effect = RuntimeError("Unavailable") if case == "membership_error" else None
                self.memberships.return_value = [] if case == "uninvited" else [{"id": "a"*32, "name": "Fixture"}]
                if case == "no_session":
                    self.st.session_state.clear()
                body = MagicMock()
                with self.assertRaises(StopPage):
                    self.auth.require_access(settings_body=body)
                body.assert_not_called()
                self.assertFalse(any(name == "popover" for name, _, _ in self.st.events))

    def test_identity_change_clears_old_customer_state_before_settings_callback(self):
        self.st.session_state.update(customer_identity="old-user", customer_workspace="b"*32,
                                     draft_old_claim="Private old draft")
        def body(access):
            self.assertNotIn("draft_old_claim", self.st.session_state)
            self.assertEqual(access.workspace, "a"*32)
            self.assertEqual(self.st.session_state.customer_identity, access.principal)
        self.auth.require_access(settings_body=body)


class CustomerNavigationTests(unittest.TestCase):
    setUp = ui_fixtures.CustomerInterfaceTests.setUp

    def test_page_uses_saved_connection_without_rendering_a_second_key_form(self):
        for area, target in (("Reviews", "reviews_page"), ("Documents", "documents_page"), ("History", "history_page")):
            with self.subTest(area=area), patch.object(self.ui, "navigation", return_value=area), \
                    patch.object(self.ui, "live_updates"), patch.object(self.ui, "connection_panel") as settings, \
                    patch.object(self.ui, target) as page:
                self.ui.run(self.access)
                settings.assert_not_called()
                self.assertEqual(page.call_args.args[:2], (self.access, "customer_"+self.access.workspace+"_"))
                if area != "History":
                    self.assertTrue(page.call_args.kwargs["connected"])

    def test_revoked_connection_metadata_stops_before_customer_content(self):
        self.metadata.side_effect = PermissionError("Revoked")
        with patch.object(self.ui, "reviews_page") as page, self.assertRaises(RerunPage):
            self.ui.run(self.access)
        page.assert_not_called()

    def test_wrong_workspace_stops_before_reading_connection_metadata(self):
        self.st.session_state.customer_workspace = "b"*32
        with self.assertRaises(RerunPage):
            self.ui.run(self.access)
        self.metadata.assert_not_called()

    def test_navigation_keeps_existing_workspace_widget_key(self):
        responsive = load_module(self.stack, "_responsive_mobile", APP / "pilot/responsive.py")
        self.st.radios["Navigation"] = "History"
        self.st.session_state["draft_customer_claim"] = "Keep this draft"
        self.assertEqual(responsive.navigation("customer_a_"), "History")
        radios = [event for event in self.st.events if event[0] == "radio"]
        self.assertEqual(len(radios), 1)
        self.assertEqual(radios[0][1][1], ["Reviews", "Documents", "History"])
        self.assertEqual(radios[0][2]["key"], "customer_a_area")
        self.assertEqual(self.st.session_state["draft_customer_claim"], "Keep this draft")


class PreviewMarkup(HTMLParser):
    def __init__(self, markup):
        super().__init__()
        self.tags = []
        self.feed(markup)

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))


class CustomerPreviewTests(unittest.TestCase):
    setUp = security_fixtures.PrivateDeliveryTests.setUp
    tearDown = security_fixtures.PrivateDeliveryTests.tearDown
    view = security_fixtures.PrivateDeliveryTests.view

    def test_zoom_is_inline_keyboard_reachable_and_caption_cannot_inject_markup(self):
        view = self.view()
        caption = 'Page "1" <img src=x onerror=bad>'
        private_media.image(view, self.a, b"PNG-FIXTURE", width=800, caption=caption, zoom=175)
        markup = next(args[0] for name, args, _ in view.events if name == "html")
        parsed = PreviewMarkup(markup)
        self.assertEqual([tag for tag, _ in parsed.tags], ["div", "img"])
        region, image = [attrs for _, attrs in parsed.tags]
        self.assertEqual((region["role"], region["tabindex"], region["aria-label"]), ("region", "0", caption))
        self.assertIn("overflow-x:auto", region["style"])
        self.assertIn("width:175%", image["style"])
        self.assertIn("max-width:none", image["style"])
        self.assertTrue(image["src"].startswith("data:image/png;base64,"))
        self.assertNotIn("/media/", markup)

    def test_zoom_rejects_invalid_values_without_rendering_private_bytes(self):
        for zoom in (74, 201, math.nan, math.inf, True, '100;bad'):
            with self.subTest(zoom=zoom):
                view = self.view()
                with self.assertRaises(ValueError):
                    private_media.image(view, self.a, b"PRIVATE", width=800, caption="Page", zoom=zoom)
                self.assertFalse(any(name == "html" for name, _, _ in view.events))

    def test_zoom_still_checks_live_membership_before_rendering(self):
        view = self.view()
        with store.db() as c:
            c.execute("DELETE FROM members WHERE principal=?", (self.a.principal,))
        with self.assertRaises(PermissionError):
            private_media.image(view, self.a, b"PRIVATE", width=800, caption="Page", zoom=200)
        self.assertFalse(any(name == "html" for name, _, _ in view.events))

if __name__ == "__main__":
    unittest.main()
