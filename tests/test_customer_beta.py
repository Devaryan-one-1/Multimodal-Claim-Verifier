"""Customer auth, review-screen and export checks; no live services or source data."""
from contextlib import ExitStack
import csv
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import types
import unittest
from unittest.mock import MagicMock, patch

APP = Path(__file__).resolve().parents[1] / "app"
sys.path.insert(0, str(APP))
from pilot import store
from pilot.exports import exports


class StopPage(BaseException):
    pass


class RerunPage(BaseException):
    pass


class Session(dict):
    def __getattr__(self, key):
        return self[key]

    def __setattr__(self, key, value):
        self[key] = value


class StreamlitFixture(types.ModuleType):
    def __init__(self):
        super().__init__("streamlit")
        self.session_state = Session()
        self.secrets = {}
        self.events, self.inputs, self.pressed, self.radios = [], {}, set(), {}
        self.sidebar = self

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)

        def call(*args, **kwargs):
            self.events.append((name, args, kwargs))
            if name == "stop":
                raise StopPage()
            if name == "rerun":
                raise RerunPage()
            if name == "fragment":
                return (lambda fn: fn) if not args else args[0]
            if name in {"form", "expander", "status", "spinner", "container", "popover"}:
                return self
            if name == "columns":
                return [self] * (args[0] if isinstance(args[0], int) else len(args[0]))
            if name in {"button", "form_submit_button"}:
                pressed = args[0] in self.pressed and not kwargs.get("disabled", False)
                if pressed and kwargs.get("on_click"):
                    kwargs["on_click"](*kwargs.get("args", ()), **kwargs.get("kwargs", {}))
                return pressed
            if name == "file_uploader":
                return []
            if name in {"text_input", "text_area"}:
                key = kwargs.get("key")
                previous = self.session_state.get(key, kwargs.get("value", ""))
                value = self.inputs.get(args[0], previous)
                if key:
                    self.session_state[key] = value
                    if value != previous and kwargs.get("on_change"):
                        kwargs["on_change"](*kwargs.get("args", ()))
                return value
            if name == "radio":
                return self.radios.get(args[0], args[1][0])
            if name == "selectbox":
                options = list(args[1])
                selected = self.session_state.get(kwargs.get("key"), options[kwargs.get("index", 0)])
                return selected if selected in options else options[0]
            return None
        return call

    def rendered_text(self, kind=None):
        return "\n".join(str(arg) for name, args, _ in self.events
                         if kind is None or name == kind for arg in args)


def load_module(stack, name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    stack.enter_context(patch.dict(sys.modules, {name: module}))
    spec.loader.exec_module(module)
    return module


class CustomerAuthTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.st = StreamlitFixture()
        self.client = MagicMock()
        sdk = types.ModuleType("supabase")
        sdk.create_client = MagicMock(return_value=self.client)
        options = types.ModuleType("supabase.client")
        options.ClientOptions = lambda **kwargs: kwargs
        self.stack.enter_context(patch.dict(sys.modules, {
            "streamlit": self.st, "supabase": sdk, "supabase.client": options,
        }))
        self.stack.enter_context(patch.dict(os.environ, {
            "SUPABASE_URL": "https://fixture.supabase.co",
            "SUPABASE_PUBLISHABLE_KEY": "sb_publishable_fixture", "SUPABASE_ENABLE_SIGNUP": "true",
        }))
        self.auth = load_module(self.stack, "_customer_auth_beta", APP / "pilot/auth.py")
        self.memberships = self.stack.enter_context(patch.object(
            self.auth, "memberships", return_value=[{"id": "a" * 32, "name": "Fixture workspace"}]))
        self.session = types.SimpleNamespace(access_token="rotated-access", refresh_token="rotated-refresh",
                                             expires_at=time.time() + 1800, expires_in=1800)
        self.user = types.SimpleNamespace(id="fixture-uid", email="fixture@example.com", email_confirmed_at="2026-01-01T00:00:00Z",
                                          deleted_at=None, is_anonymous=False, banned_until=None)
        self.client.auth.set_session.return_value = types.SimpleNamespace(session=self.session)
        self.client.auth.refresh_session.return_value = types.SimpleNamespace(session=self.session)
        self.client.auth.get_user.return_value = types.SimpleNamespace(user=self.user)
        self.st.session_state[self.auth.SESSION] = {
            "access_token": "old-access", "refresh_token": "old-refresh", "expires_at": time.time() - 1,
        }

    def test_access_uses_verified_uid_and_rotated_tokens(self):
        access = self.auth.require_access()
        expected = store.principal("https://fixture.supabase.co/auth/v1", "fixture-uid")
        self.assertEqual((access.principal, access.workspace), (expected, "a" * 32))
        self.client.auth.get_user.assert_called_once_with("rotated-access")
        self.assertEqual(self.st.session_state[self.auth.SESSION]["refresh_token"], "rotated-refresh")
        self.assertLessEqual(access.expires, time.time() + 300)

    def test_no_session_displays_login_and_stops(self):
        self.st.session_state.clear()
        with self.assertRaises(StopPage):
            self.auth.require_access()
        self.memberships.assert_not_called()
        self.assertIn("Sign in", self.st.rendered_text())

    def test_unverified_or_unavailable_accounts_cannot_get_workspace_access(self):
        cases = [("email_confirmed_at", None), ("id", ""), ("deleted_at", "2026-01-01"),
                 ("is_anonymous", True), ("banned_until", "2999-01-01T00:00:00Z")]
        for field, value in cases:
            with self.subTest(field=field):
                original = getattr(self.user, field)
                setattr(self.user, field, value)
                with self.assertRaises(StopPage):
                    self.auth.require_access()
                self.memberships.assert_not_called()
                setattr(self.user, field, original)

    def test_still_expired_provider_session_is_denied(self):
        self.session.expires_at = time.time() - 100
        with self.assertRaises(StopPage):
            self.auth.require_access()
        self.memberships.assert_not_called()

    def test_provider_validation_failure_is_denied_without_raw_error(self):
        self.client.auth.get_user.side_effect = RuntimeError("PRIVATE_TOKEN_DIAGNOSTIC")
        with self.assertRaises(StopPage):
            self.auth.require_access()
        self.memberships.assert_not_called()
        self.assertNotIn("PRIVATE_TOKEN_DIAGNOSTIC", self.st.rendered_text())

    def test_confirmed_uninvited_user_sees_access_request(self):
        self.memberships.return_value = []
        with self.assertRaises(StopPage):
            self.auth.require_access()
        self.assertIn("administrator must grant", self.st.rendered_text("info"))
        self.assertIn("fixture-uid", self.st.rendered_text("code"))

    def test_revoked_membership_is_checked_on_the_next_rerun(self):
        first = self.auth.require_access()
        self.memberships.return_value = []
        with self.assertRaises(StopPage):
            self.auth.require_access()
        self.assertEqual(self.memberships.call_count, 2)
        self.assertEqual(self.memberships.call_args.args[0], first.principal)

    def test_switching_identity_clears_old_customer_widget_state(self):
        self.st.session_state.update(customer_identity="previous-user", customer_workspace="b" * 32,
                                     previous_customer_document="PRIVATE_OLD_DOCUMENT")
        access = self.auth.require_access()
        self.assertNotIn("previous_customer_document", self.st.session_state)
        self.assertEqual(access.workspace, "a" * 32)

    def test_logout_clears_local_state_even_when_remote_revocation_fails(self):
        self.st.session_state["cached_document"] = "PRIVATE_OLD_DOCUMENT"
        self.client.auth.set_session.side_effect = RuntimeError("PRIVATE_TOKEN_DIAGNOSTIC")
        self.auth.logout("https://fixture.supabase.co", "sb_publishable_fixture")
        self.assertEqual(set(self.st.session_state), {"auth_notice"})
        self.assertIn("could not confirm", self.st.session_state["auth_notice"])
        self.assertNotIn("PRIVATE_TOKEN_DIAGNOSTIC", self.st.session_state["auth_notice"])

    def test_rejects_secret_key_and_non_base_project_urls(self):
        cases = [{"SUPABASE_PUBLISHABLE_KEY": "service-role-fixture"},
                 {"SUPABASE_URL": "http://fixture.supabase.co"},
                 {"SUPABASE_URL": "https://fixture.supabase.co/auth/v1"}]
        for values in cases:
            with self.subTest(values=values), patch.dict(os.environ, values):
                with self.assertRaises(ValueError):
                    self.auth.configuration()

    def test_wrong_password_does_not_create_session_or_expose_provider_error(self):
        self.st.session_state.clear()
        self.st.inputs.update(Email="fixture@example.com", Password="incorrect-password")
        self.st.pressed.add("Sign in")
        self.client.auth.sign_in_with_password.side_effect = RuntimeError("PRIVATE_TOKEN_DIAGNOSTIC")
        self.auth.login_screen("https://fixture.supabase.co", "sb_publishable_fixture")
        self.assertNotIn(self.auth.SESSION, self.st.session_state)
        self.assertIn("Authentication could not complete", self.st.rendered_text("error"))
        self.assertNotIn("PRIVATE_TOKEN_DIAGNOSTIC", self.st.rendered_text())

    def test_reset_requires_verified_recovery_before_password_update(self):
        self.st.inputs.update({"Email for reset": "fixture@example.com", "Reset code": "123456",
                               "New password": "long-fixture-password", "Repeat new password": "long-fixture-password"})
        self.st.pressed.add("Set new password")
        self.client.auth.verify_otp.return_value = types.SimpleNamespace(session=self.session)
        self.client.auth.get_user.return_value = types.SimpleNamespace(user=None)
        self.auth.recovery_form(self.client, "https://fixture.supabase.co", "sb_publishable_fixture")
        self.client.auth.verify_otp.assert_called_once_with({"email": "fixture@example.com", "token": "123456", "type": "recovery"})
        self.client.auth.update_user.assert_not_called()

    def test_successful_reset_clears_login_and_recovery_sessions(self):
        self.st.inputs.update({"Email for reset": "fixture@example.com", "Reset code": "123456",
                               "New password": "long-fixture-password", "Repeat new password": "long-fixture-password"})
        self.st.pressed.add("Set new password")
        self.client.auth.verify_otp.return_value = types.SimpleNamespace(session=self.session)
        self.auth.recovery_form(self.client, "https://fixture.supabase.co", "sb_publishable_fixture")
        self.client.auth.update_user.assert_called_once_with({"password": "long-fixture-password"})
        self.assertNotIn(self.auth.SESSION, self.st.session_state)
        self.assertNotIn(self.auth.RECOVERY, self.st.session_state)


class CustomerReviewScreenTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.st = StreamlitFixture()
        pil = types.ModuleType("PIL")
        pil.Image, pil.ImageDraw = MagicMock(), MagicMock()
        calculation = types.ModuleType("ui.calculation")
        self.stack.enter_context(patch.dict(sys.modules, {
            "streamlit": self.st, "pymupdf": types.ModuleType("pymupdf"), "PIL": pil,
            "ui.calculation": calculation,
        }))
        renderer = load_module(self.stack, "_customer_calculation_beta", APP / "ui/calculation.py")
        calculation.render_calculation = MagicMock(wraps=renderer.render_calculation)
        self.calculation = calculation.render_calculation
        self.ui = load_module(self.stack, "_customer_ui_beta", APP / "pilot/ui.py")
        self.real_viewer = self.ui.viewer
        self.stack.enter_context(patch.object(self.ui, "viewer"))
        self.stack.enter_context(patch.object(self.ui, "exports", return_value=("csv", "html", "json")))
        self.stack.enter_context(patch.object(store, "decisions", return_value={}))
        self.resume = self.stack.enter_context(patch.object(store, "resume"))
        self.access = store.Access("fixture", "a" * 32, time.time() + 300)
        self.st.session_state.customer_identity = self.access.principal
        self.stack.enter_context(patch.object(self.ui.model_connections, "metadata", return_value={
            "version": "fixture-version", "models": {"text_model": "vendor/fixture"}}))

    def review(self, status="complete", results=None, attempts=1):
        job = {"id": "c" * 32, "status": status, "stage": "Fixture", "attempts": attempts,
               "kind": "verify", "principal": self.access.principal,
               "payload": json.dumps({"name": "Public fixture", "openrouter": {"connection_version": "fixture-version"}}),
               "result": json.dumps({"results": results or []})}
        self.ui.review_detail(self.access, job, "fixture")

    def test_failed_and_paused_reviews_do_not_display_factual_unknown_heading(self):
        for status in ("failed", "paused"):
            with self.subTest(status=status):
                self.st.events.clear()
                self.review(status)
                self.assertIn("Verification failed or paused", self.st.rendered_text("error"))
                self.assertNotIn("### UNKNOWN", self.st.rendered_text("markdown"))

    def test_queued_and_running_reviews_offer_no_retry(self):
        self.st.pressed.add("Retry / resume")
        for status in ("queued", "running"):
            self.review(status)
        self.resume.assert_not_called()
        self.assertNotIn("Verification failed", self.st.rendered_text("error"))

    def test_explicit_retry_uses_current_review_identity(self):
        self.st.pressed.add("Retry / resume")
        with self.assertRaises(RerunPage):
            self.review("paused")
        self.resume.assert_called_once_with(self.access, "c" * 32)

    def test_third_attempt_does_not_offer_another_retry(self):
        self.st.pressed.add("Retry / resume")
        self.review("failed", attempts=3)
        self.resume.assert_not_called()
        self.assertFalse(any(name == "button" and args[0] == "Retry / resume" for name, args, _ in self.st.events))

    def test_shared_workspace_member_cannot_offer_a_retry_using_another_users_key(self):
        self.st.pressed.add("Retry / resume")
        job = {"id": "c" * 32, "kind": "verify", "principal": "another-user", "status": "paused",
               "stage": "Fixture", "attempts": 1, "payload": "{}", "result": "{}"}
        self.ui.review_detail(self.access, job, "fixture")
        self.resume.assert_not_called()
        self.assertIn("Only the request creator", self.st.rendered_text("caption"))

    def test_completed_unknown_remains_a_factual_result(self):
        self.review(results=[{"status": "complete", "verdict": "UNKNOWN", "reason": "Source does not establish this."}])
        self.assertIn("### UNKNOWN", self.st.rendered_text("markdown"))
        self.assertNotIn("Verification failed", self.st.rendered_text("error"))

    def test_source_and_model_text_cannot_render_remote_markdown_images(self):
        tainted = "![untrusted](https://example.invalid/tracking)"
        self.review(results=[{"status": "complete", "verdict": "SUPPORTED", "claim_text": tainted,
                             "reason": tainted, "evidence": [{"content": tainted, "page": 1}]}])
        self.assertIn(tainted, self.st.rendered_text("text"))
        self.assertFalse(any(name in {"write", "markdown"} and tainted in args for name, args, _ in self.st.events))

    def test_saved_success_is_visible_inside_a_paused_partial_review(self):
        result = {"status": "complete", "verdict": "SUPPORTED", "claim_text": "First fixture"}
        self.review("paused", [result])
        self.assertIn("Verification failed or paused", self.st.rendered_text("error"))
        self.assertIn("First fixture", self.st.rendered_text())
        self.assertIn("### SUPPORTED", self.st.rendered_text("markdown"))

    def test_customer_screen_receives_the_saved_calculation_and_signed_difference(self):
        for claimed, difference, shown in ((110, 10, "+10 number"), (90, -10, "-10 number")):
            with self.subTest(claimed=claimed):
                self.st.events.clear()
                result = {"status": "complete", "verdict": "CONTRADICTED", "claim_text": "Fixture",
                          "calculation": {"formula": "value", "value": 100, "result_unit": "number",
                                          "inputs": {"value": 100}, "raw_inputs": {"value": "100"}},
                          "comparison": {"claimed": claimed, "actual": 100, "unit": "number", "difference": difference}}
                self.review(results=[result])
                self.calculation.assert_called_with(result)
                metrics = {args[0]: args[1] for name, args, _ in self.st.events if name == "metric"}
                self.assertEqual(metrics["Signed difference"].replace("\u2212", "-"), shown)
                self.assertEqual(metrics["Actual"], "100 number")
                self.assertIn("100", self.st.rendered_text("text"))
                self.assertIn("value", self.st.rendered_text("code"))

    def test_percentage_result_displays_difference_in_percentage_points(self):
        result = {"status": "complete", "verdict": "CONTRADICTED",
                  "calculation": {"formula": "(new-old)/old*100", "value": 5, "result_unit": "percent",
                                  "inputs": {"old": 100, "new": 105}, "raw_inputs": {"old": "100", "new": "105"}},
                  "comparison": {"claimed": 6.5, "actual": 5, "unit": "percent", "difference": 1.5}}
        self.review(results=[result])
        metrics = {args[0]: args[1] for name, args, _ in self.st.events if name == "metric"}
        self.assertEqual(metrics["Actual"], "5%")
        self.assertEqual(metrics["Signed difference"], "+1.5 percentage points")

    def test_foreign_citation_is_blocked_before_reading_any_pdf(self):
        result = {"evidence": [{"document_id": "b" * 32, "page": 1}]}
        with patch.object(store, "documents", return_value=[{"id": "a" * 32}]), patch.object(store, "document_bytes") as read:
            self.real_viewer(self.access, result, "fixture-viewer")
        read.assert_not_called()
        self.assertIn("unavailable", self.st.rendered_text("warning"))

    def test_evidence_view_page_returns_to_same_citation_after_manual_browsing(self):
        self.st.pressed.add("View Page")
        for kind, page, doc in (("chart", 2, "b"*32), ("table", 7, "d"*32), ("text", 4, "e"*32)):
            with self.subTest(kind=kind, page=page):
                evidence = {"id": "citation-"+kind, "document_id": doc, "page": page,
                            "type": kind, "content": "Public citation fixture"}
                result = {"status": "complete", "verdict": "SUPPORTED", "evidence": [evidence]}
                key = "fixtureviewer_0"
                self.st.session_state[key+"citation"] = 0
                self.st.session_state[key+"page"] = 1
                with patch.object(store, "documents", return_value=[{"id": doc, "name": kind+".pdf"}]):
                    self.review(results=[result])
                self.assertEqual(self.st.session_state[key+"citation"], 0)
                self.assertEqual(self.st.session_state[key+"page"], page)

    def test_evidence_view_page_uses_ownership_filtered_citation_index(self):
        allowed, foreign = "b"*32, "d"*32
        result = {"status": "complete", "verdict": "SUPPORTED", "evidence": [
            {"id": "foreign", "document_id": foreign, "page": 9, "type": "chart"},
            {"id": "allowed", "document_id": allowed, "page": 3, "type": "text"}]}
        self.st.session_state["fixtureviewer_0page"] = 1
        self.st.pressed.add("View Page")
        with patch.object(store, "documents", return_value=[{"id": allowed, "name": "Allowed.pdf"}]):
            self.review(results=[result])
        self.assertEqual(self.st.session_state["fixtureviewer_0citation"], 0)
        self.assertEqual(self.st.session_state["fixtureviewer_0page"], 3)

    def test_evidence_view_page_does_not_navigate_to_foreign_or_invalid_citations(self):
        allowed = "b"*32
        self.st.pressed.add("View Page")
        for doc, page in (("d"*32, 2), (allowed, 0), (allowed, -1), (allowed, True), (allowed, "2")):
            with self.subTest(doc=doc, page=page):
                result = {"status": "complete", "verdict": "SUPPORTED", "evidence": [
                    {"id": "unavailable", "document_id": doc, "page": page, "type": "chart"}]}
                self.st.session_state["fixtureviewer_0page"] = 1
                self.st.session_state["fixtureviewer_0citation"] = 0
                with patch.object(store, "documents", return_value=[{"id": allowed, "name": "Allowed.pdf"}]):
                    self.review(results=[result])
                self.assertEqual(self.st.session_state["fixtureviewer_0page"], 1)
                self.assertEqual(self.st.session_state["fixtureviewer_0citation"], 0)
    def test_connection_key_widget_is_masked_without_saved_plaintext(self):
        with patch.object(self.ui.model_connections, "metadata", return_value=None):
            self.assertFalse(self.ui.connection_panel(self.access, "fixture"))
        fields = [(args, kwargs) for name, args, kwargs in self.st.events if name == "text_input"]
        key_field = next(kwargs for args, kwargs in fields if args[0] == "OpenRouter API key")
        self.assertEqual(key_field["type"], "password")
        self.assertNotIn("value", key_field)
        self.assertTrue(any(kwargs.get("clear_on_submit") for name, _, kwargs in self.st.events if name == "form"))

    def test_connection_save_passes_only_current_users_settings(self):
        self.st.inputs.update({"OpenRouter API key": "sk-or-customer-fixture", "Reasoning model ID": "vendor/text:free", "Vision model ID": "vendor/vision:free"})
        self.st.pressed.add("Check and save connection")
        with patch.object(self.ui.model_connections, "metadata", return_value=None), patch.object(self.ui.model_connections, "save") as save:
            with self.assertRaises(RerunPage): self.ui.connection_panel(self.access, "fixture")
        save.assert_called_once_with(self.access, "sk-or-customer-fixture", "vendor/text:free", "vendor/vision:free")
        self.assertNotIn("sk-or-customer-fixture", self.st.rendered_text())

    def test_connection_remove_uses_current_user(self):
        self.st.pressed.add("Remove my saved key")
        with patch.object(self.ui.model_connections, "metadata", return_value={"models": {}}), patch.object(self.ui.model_connections, "remove") as remove:
            with self.assertRaises(RerunPage): self.ui.connection_panel(self.access, "fixture")
        remove.assert_called_once_with(self.access)

    def test_upload_is_disabled_without_blocking_document_history(self):
        with patch.object(store, "documents", return_value=[]), patch.object(store, "jobs", return_value=[]):
            self.ui.documents_page(self.access, "fixture", connected=False)
        uploader = next(kwargs for name, _, kwargs in self.st.events if name == "file_uploader")
        self.assertTrue(uploader["disabled"])
        self.assertIn("Connect your OpenRouter key", self.st.rendered_text("info"))


class CustomerExportTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.temp = self.stack.enter_context(tempfile.TemporaryDirectory())
        self.stack.enter_context(patch.dict(os.environ, {"CUSTOMER_DATA_ROOT": self.temp}))
        store.initialize()
        self.access = store.Access("fixture", "a" * 32, time.time() + 300)
        self.job = "c" * 32
        self.result = {"status": "complete", "verdict": "SUPPORTED", "claim_text": "Revenue was USD 100 million in 2025.",
                       "reason": "Public fixture", "comparison": {"claimed": 100000000, "actual": 100000000, "unit": "USD"},
                       "calculation": {"formula": "value", "raw_inputs": {"value": "USD 100 million"}, "inputs": {"value": 100000000}},
                       "evidence": [{"document_id": "b" * 32, "page": 7, "source_evidence_id": "fixture-source"}]}
        with store.db() as c:
            c.execute("INSERT INTO workspaces VALUES(?,?)", (self.access.workspace, "Fixture"))
            c.execute("INSERT INTO members VALUES(?,?)", (self.access.principal, self.access.workspace))
            c.execute("INSERT INTO jobs(id,workspace,principal,kind,payload,status,stage,result,created,updated) VALUES(?,?,?,?,?,'complete','Fixture',?,?,?)",
                      (self.job, self.access.workspace, self.access.principal, "verify", json.dumps({"name": "Fixture", "versions": {"b" * 32: "fixture-hash"}}), json.dumps({"results": [self.result]}), time.time(), time.time()))

    def test_exports_keep_source_values_units_formula_citation_and_review_decision(self):
        store.decide(self.access, self.job, 0, "reviewed", "Checked fixture page.")
        csv_data, report, raw = exports(self.access, self.job)
        row = next(csv.DictReader(io.StringIO(csv_data)))
        self.assertEqual(row["actual"], "100000000")
        self.assertEqual(row["unit"], "USD")
        self.assertEqual(row["formula"], "value")
        self.assertEqual(json.loads(row["source_values"]), {"value": "USD 100 million"})
        self.assertIn("PDF page 7", row["citations"])
        self.assertEqual(row["decision"], "reviewed")
        self.assertIn("fixture-hash", report)
        self.assertEqual(json.loads(raw)["results"]["results"][0], self.result)

    def test_partial_export_keeps_completion_status_and_only_saved_results(self):
        with store.db() as c:
            c.execute("UPDATE jobs SET status='paused',result=? WHERE id=?",
                      (json.dumps({"results": [self.result], "error_stage": "claim_parser", "model_calls": [{"status": "error", "http_status": 402}]}), self.job))
        csv_data, report, raw = exports(self.access, self.job)
        self.assertEqual(len(list(csv.DictReader(io.StringIO(csv_data)))), 1)
        self.assertIn("Run status: paused", report)
        self.assertEqual(json.loads(raw)["review"]["status"], "paused")

    def test_export_escapes_html_and_spreadsheet_formulas(self):
        self.result.update(claim_text='=HYPERLINK("fixture")', reason="<script>fixture</script>")
        with store.db() as c:
            c.execute("UPDATE jobs SET result=? WHERE id=?", (json.dumps({"results": [self.result]}), self.job))
        csv_data, report, _ = exports(self.access, self.job)
        self.assertTrue(next(csv.DictReader(io.StringIO(csv_data)))["claim"].startswith("'="))
        self.assertNotIn("<script>fixture</script>", report)
        self.assertIn("&lt;script&gt;fixture&lt;/script&gt;", report)

    def test_foreign_customer_cannot_export_a_review(self):
        foreign = store.Access("other-user", "b" * 32, time.time() + 300)
        with self.assertRaises(PermissionError):
            exports(foreign, self.job)


if __name__ == "__main__":
    unittest.main()
