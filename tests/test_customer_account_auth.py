"""Account confirmation/recovery/session regressions; no real auth requests."""
import time
import types
import unittest
from unittest.mock import patch

import test_customer_beta as auth_fixtures


class AccountAuthTests(unittest.TestCase):
    setUp = auth_fixtures.CustomerAuthTests.setUp

    def reset_inputs(self):
        self.st.inputs.update({"Email for reset": "fixture@example.com", "Reset code": "123456",
                               "New password": "long-fixture-password", "Repeat new password": "long-fixture-password"})
        self.st.pressed.add("Set new password")
        self.client.auth.verify_otp.return_value = types.SimpleNamespace(session=self.session)

    def test_confirmation_resend_is_explicit_and_does_not_sign_in(self):
        self.st.session_state.clear()
        self.st.radios["Account action"] = "Resend confirmation"
        self.st.inputs["Email"] = "fixture@example.com"
        self.st.pressed.add("Resend confirmation")
        self.auth.login_screen("https://fixture.supabase.co", "sb_publishable_fixture")
        self.client.auth.resend.assert_called_once_with({"type": "signup", "email": "fixture@example.com",
            "options": {"email_redirect_to": "http://localhost:8501"}})
        self.assertNotIn(self.auth.SESSION, self.st.session_state)

    def test_email_send_response_does_not_reveal_account_or_raw_provider_errors(self):
        self.auth.send_code(self.client, "fixture@example.com", "recovery")
        first = self.st.rendered_text("info")
        self.st.session_state.pop("auth_email_next_send")
        self.st.events.clear()
        self.client.auth.reset_password_for_email.side_effect = RuntimeError("PRIVATE_ACCOUNT_DETAILS")
        self.auth.send_code(self.client, "fixture@example.com", "recovery")
        self.assertEqual(first, self.st.rendered_text("info"))
        self.assertNotIn("PRIVATE_ACCOUNT_DETAILS", self.st.rendered_text())

    def test_email_send_cooldown_blocks_duplicate_requests(self):
        self.auth.send_code(self.client, "fixture@example.com", "signup")
        self.auth.send_code(self.client, "another@example.com", "signup")
        self.client.auth.resend.assert_called_once()
        self.assertIn("Wait a minute", self.st.rendered_text("info"))

    def test_invalid_email_does_not_reach_auth_provider(self):
        self.auth.send_code(self.client, "not an email", "recovery")
        self.client.auth.reset_password_for_email.assert_not_called()
        self.assertIn("account email", self.st.rendered_text("error"))

    def test_confirmation_checks_verified_identity_before_accepting_session(self):
        self.st.session_state.clear()
        self.st.radios["Account action"] = "Confirm email"
        self.st.inputs.update({"Email": "fixture@example.com", "Confirmation code": "123456"})
        self.st.pressed.add("Confirm email")
        self.client.auth.verify_otp.return_value = types.SimpleNamespace(session=self.session)
        self.user.email = "different@example.com"
        self.auth.login_screen("https://fixture.supabase.co", "sb_publishable_fixture")
        self.assertNotIn(self.auth.SESSION, self.st.session_state)
        self.client.auth.verify_otp.assert_called_once_with({"email": "fixture@example.com", "token": "123456", "type": "email"})

    def test_successful_confirmation_clears_previous_customer_state(self):
        self.st.session_state.clear()
        self.st.session_state["draft_old_claim"] = "PRIVATE_OLD_CLAIM"
        self.st.radios["Account action"] = "Confirm email"
        self.st.inputs.update({"Email": "fixture@example.com", "Confirmation code": "123456"})
        self.st.pressed.add("Confirm email")
        self.client.auth.verify_otp.return_value = types.SimpleNamespace(session=self.session)
        with self.assertRaises(auth_fixtures.RerunPage):
            self.auth.login_screen("https://fixture.supabase.co", "sb_publishable_fixture")
        self.assertEqual(set(self.st.session_state), {self.auth.SESSION})

    def test_password_mismatch_and_invalid_otp_cannot_change_password(self):
        self.reset_inputs()
        self.st.inputs["Repeat new password"] = "different-password"
        self.auth.recovery_form(self.client, "url", "key")
        self.client.auth.verify_otp.assert_not_called()
        self.client.auth.update_user.assert_not_called()
        self.st.inputs["Repeat new password"] = "long-fixture-password"
        self.client.auth.verify_otp.side_effect = RuntimeError("PRIVATE_INVALID_OTP")
        self.auth.recovery_form(self.client, "url", "key")
        self.client.auth.update_user.assert_not_called()
        self.assertNotIn(self.auth.RECOVERY, self.st.session_state)
        self.assertNotIn("PRIVATE_INVALID_OTP", self.st.rendered_text())

    def test_reset_rejects_mismatched_or_unconfirmed_recovered_identity(self):
        self.reset_inputs()
        for field, value in (("email", "someone-else@example.com"), ("email_confirmed_at", None), ("is_anonymous", True)):
            with self.subTest(field=field):
                old = getattr(self.user, field)
                setattr(self.user, field, value)
                self.auth.recovery_form(self.client, "url", "key")
                self.client.auth.update_user.assert_not_called()
                self.assertNotIn(self.auth.RECOVERY, self.st.session_state)
                setattr(self.user, field, old)

    def test_verified_recovery_retry_preserves_deadline_and_rotates_tokens(self):
        self.reset_inputs()
        deadline = time.time()+90
        self.st.session_state[self.auth.RECOVERY] = {"email": "fixture@example.com", "valid_until": deadline,
            "access_token": "recovery-access", "refresh_token": "recovery-refresh", "expires_at": time.time()+300}
        self.client.auth.update_user.side_effect = RuntimeError("PRIVATE_PASSWORD_POLICY")
        self.auth.recovery_form(self.client, "url", "key")
        self.client.auth.refresh_session.assert_called_once_with("recovery-refresh")
        self.client.auth.verify_otp.assert_not_called()
        saved = self.st.session_state[self.auth.RECOVERY]
        self.assertEqual(saved["valid_until"], deadline)
        self.assertEqual(saved["refresh_token"], "rotated-refresh")
        self.assertNotIn("PRIVATE_PASSWORD_POLICY", self.st.rendered_text())

    def test_expired_recovery_session_requires_a_new_valid_code(self):
        self.reset_inputs()
        self.st.session_state[self.auth.RECOVERY] = {"email": "fixture@example.com", "valid_until": time.time()-1,
            "access_token": "old", "refresh_token": "old"}
        self.client.auth.verify_otp.side_effect = RuntimeError("Expired code")
        self.auth.recovery_form(self.client, "url", "key")
        self.client.auth.refresh_session.assert_not_called()
        self.client.auth.verify_otp.assert_called_once()
        self.client.auth.update_user.assert_not_called()
        self.assertNotIn(self.auth.RECOVERY, self.st.session_state)

    def test_verified_recovery_keeps_rotated_tokens_if_user_lookup_fails(self):
        self.reset_inputs()
        deadline = time.time()+90
        self.st.session_state[self.auth.RECOVERY] = {"email": "fixture@example.com", "valid_until": deadline,
            "access_token": "old", "refresh_token": "old", "expires_at": time.time()+300}
        self.client.auth.get_user.side_effect = RuntimeError("Temporary user lookup outage")
        self.auth.recovery_form(self.client, "url", "key")
        self.assertEqual(self.st.session_state[self.auth.RECOVERY]["refresh_token"], "rotated-refresh")
        self.assertEqual(self.st.session_state[self.auth.RECOVERY]["valid_until"], deadline)
        self.client.auth.update_user.assert_not_called()

    def test_successful_reset_clears_all_customer_state_and_revokes_sessions(self):
        self.reset_inputs()
        self.st.session_state.update(customer_identity="previous", draft_private_note="PRIVATE", selected_document="private")
        self.auth.recovery_form(self.client, "url", "key")
        self.client.auth.sign_out.assert_called_once_with({"scope": "global"})
        self.assertEqual(dict(self.st.session_state), {})
        self.assertIn("Password updated", self.st.rendered_text("success"))

    def test_reset_still_clears_local_state_if_global_signout_cannot_be_confirmed(self):
        self.reset_inputs()
        self.st.session_state["draft_private"] = "PRIVATE"
        self.client.auth.sign_out.side_effect = RuntimeError("PRIVATE_AUTH_OUTAGE")
        self.auth.recovery_form(self.client, "url", "key")
        self.assertEqual(dict(self.st.session_state), {})
        self.assertIn("could not be confirmed", self.st.rendered_text("warning"))
        self.assertNotIn("PRIVATE_AUTH_OUTAGE", self.st.rendered_text())

    def test_invalid_session_clears_old_private_state_but_keeps_tokens_for_logout(self):
        self.auth.require_access()
        self.st.session_state["draft_private"] = "PRIVATE"
        self.client.auth.refresh_session.side_effect = RuntimeError("Revoked refresh token")
        self.client.auth.get_user.reset_mock()
        with self.assertRaises(auth_fixtures.StopPage):
            self.auth.require_access()
        self.assertEqual(set(self.st.session_state), {self.auth.SESSION})
        self.client.auth.get_user.assert_not_called()

    def test_refresh_checks_server_revocation_even_for_unexpired_access_tokens(self):
        self.st.session_state[self.auth.SESSION]["expires_at"] = time.time()+1800
        self.client.auth.refresh_session.side_effect = RuntimeError("Revoked")
        with self.assertRaises(auth_fixtures.StopPage):
            self.auth.require_access()
        self.memberships.assert_not_called()

    def test_nonfinite_session_expiry_is_rejected(self):
        for value in (float("inf"), float("nan")):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.session.expires_at = value
                self.auth.pack(self.session)

    def test_all_memberships_revoked_clears_old_workspace_drafts(self):
        self.auth.require_access()
        self.st.session_state["draft_private"] = "PRIVATE"
        self.memberships.return_value = []
        with self.assertRaises(auth_fixtures.StopPage):
            self.auth.require_access()
        self.assertNotIn("draft_private", self.st.session_state)
        self.assertNotIn("customer_workspace", self.st.session_state)
        self.assertIn(self.auth.SESSION, self.st.session_state)

    def test_partial_membership_revocation_clears_stale_workspace_selection(self):
        self.memberships.return_value = [{"id": "a"*32, "name": "A"}, {"id": "b"*32, "name": "B"}]
        self.auth.require_access()
        self.st.session_state["draft_private_a"] = "PRIVATE_A"
        self.memberships.return_value = [{"id": "b"*32, "name": "B"}]
        access = self.auth.require_access()
        self.assertEqual(access.workspace, "b"*32)
        self.assertNotIn("draft_private_a", self.st.session_state)

    def test_workspace_lookup_outage_does_not_leave_a_stale_authorized_identity(self):
        self.auth.require_access()
        self.st.session_state["draft_private"] = "PRIVATE"
        self.memberships.side_effect = RuntimeError("PRIVATE_DATABASE_ERROR")
        with self.assertRaises(auth_fixtures.StopPage):
            self.auth.require_access()
        self.assertEqual(set(self.st.session_state), {self.auth.SESSION})
        self.assertIn("Workspace access could not be checked", self.st.rendered_text("error"))
        self.assertNotIn("PRIVATE_DATABASE_ERROR", self.st.rendered_text())

    def test_missing_login_cannot_retain_a_previous_customer_identity(self):
        self.st.session_state.pop(self.auth.SESSION)
        self.st.session_state.update(customer_identity="old", draft_private="PRIVATE")
        with self.assertRaises(auth_fixtures.StopPage):
            self.auth.require_access()
        self.assertNotIn("customer_identity", self.st.session_state)
        self.assertNotIn("draft_private", self.st.session_state)


if __name__ == "__main__":
    unittest.main()
