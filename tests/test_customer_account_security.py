"""Workspace denial and private delivery checks using temporary files only."""
import ast
import base64
import hashlib
from html.parser import HTMLParser
import json
import time
import sys
import types
import unittest
from unittest.mock import MagicMock, patch

import test_customer_access as access_fixtures
from test_customer_beta import APP, StreamlitFixture, RerunPage
from pilot import private_media, store, model_connections
from pilot.exports import exports


class Links(HTMLParser):
    def __init__(self, source):
        super().__init__()
        self.links = []
        self.feed(source)

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self.links.append(dict(attrs))


class WorkspaceSecurityTests(unittest.TestCase):
    setUp = access_fixtures.CustomerAccessTests.setUp
    tearDown = access_fixtures.CustomerAccessTests.tearDown

    def test_lists_and_progress_never_include_another_workspace(self):
        with store.db() as c:
            c.execute("INSERT INTO documents(id,workspace,name,sha,status,created) VALUES(?,?,?,?,?,?)",
                      ("e"*32, self.b.workspace, "B_ONLY.pdf", "b-hash", "ready", time.time()))
        self.assertEqual([d["name"] for d in store.documents(self.a)], ["private.pdf"])
        self.assertEqual([d["name"] for d in store.documents(self.b)], ["B_ONLY.pdf"])
        self.assertEqual(store.jobs(self.b), [])
        self.assertEqual(store.progress_snapshot(self.b)["jobs"], [])
        self.assertNotIn("c"*32, json.dumps(store.progress_snapshot(self.b)))

    def test_foreign_decisions_context_and_exports_are_denied_without_names(self):
        actions = [lambda: store.decisions(self.b, "d"*32),
                   lambda: store.decide(self.b, "d"*32, 0, "reviewed", "foreign write"),
                   lambda: store.save_context(self.b, "c"*32, "foreign", "", ""),
                   lambda: exports(self.b, "d"*32)]
        for action in actions:
            with self.subTest(action=action):
                with self.assertRaises((PermissionError, ValueError)) as error:
                    action()
                self.assertNotIn("private.pdf", str(error.exception))
        self.assertEqual(store.decisions(self.a, "d"*32), {})
        self.assertEqual(store.documents(self.a)[0]["context"], "{}")

    def test_revoked_access_blocks_all_customer_resource_operations(self):
        with store.db() as c:
            c.execute("DELETE FROM members WHERE principal=?", (self.a.principal,))
        actions = [lambda: store.documents(self.a), lambda: store.jobs(self.a),
                   lambda: store.progress_snapshot(self.a), lambda: store.get_job(self.a, "d"*32),
                   lambda: store.document_bytes(self.a, "c"*32), lambda: exports(self.a, "d"*32),
                   lambda: store.decide(self.a, "d"*32, 0, "reviewed", "blocked"),
                   lambda: store.save_context(self.a, "c"*32, "blocked", "", ""),
                   lambda: store.resume(self.a, "d"*32), lambda: store.request_delete(self.a, "c"*32),
                   lambda: model_connections.metadata(self.a)]
        for action in actions:
            with self.subTest(action=action), self.assertRaises(PermissionError):
                action()

    def test_uninvited_account_cannot_list_or_read_an_existing_workspace(self):
        uninvited = store.Access("not-a-member", self.a.workspace, time.time()+300)
        for action in (store.documents, store.jobs, store.progress_snapshot, model_connections.metadata):
            with self.subTest(action=action), self.assertRaises(PermissionError):
                action(uninvited)


class PrivateDeliveryTests(unittest.TestCase):
    setUp = access_fixtures.CustomerAccessTests.setUp
    tearDown = access_fixtures.CustomerAccessTests.tearDown

    def view(self, access=None):
        access = access or self.a
        view = StreamlitFixture()
        view.session_state.update(customer_identity=access.principal, customer_workspace=access.workspace)
        return view

    def prepare(self, view, access, load, filename="fixture.pdf", mime="application/pdf"):
        private_media.download(view, access, "Download", load, filename=filename, mime=mime, key="download")

    def test_no_file_is_read_or_registered_before_explicit_download(self):
        view, load = self.view(), MagicMock(return_value=b"private file")
        self.prepare(view, self.a, load)
        load.assert_not_called()
        self.assertFalse(any(name in {"iframe", "html", "download_button"} for name, _, _ in view.events))

    def test_download_is_inline_and_filename_cannot_inject_html(self):
        view = self.view()
        view.pressed.add("Download")
        payload = b"<script>PRIVATE_PAYLOAD</script>"
        self.prepare(view, self.a, lambda: payload, filename='report" onclick="injected.pdf')
        body = next(args[0] for name, args, _ in view.events if name == "iframe")
        links = Links(body).links
        self.assertEqual(len(links), 1)
        self.assertEqual(set(links[0]), {"download", "href", "style"})
        self.assertEqual(links[0]["download"], 'report" onclick="injected.pdf')
        self.assertEqual(base64.b64decode(links[0]["href"].split(",", 1)[1]), payload)
        self.assertTrue(links[0]["href"].startswith("data:application/pdf;base64,"))
        self.assertNotIn("<script>", body)
        self.assertNotIn("/media/", body)
        self.assertFalse(any(name == "download_button" for name, _, _ in view.events))

    def test_real_source_download_still_checks_ownership_and_source_hash(self):
        pdf = b"%PDF-public-synthetic-fixture"
        path = store.file_path(self.a.workspace, "data", "documents", "c"*32, "original.pdf")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(pdf)
        with store.db() as c:
            c.execute("UPDATE documents SET sha=? WHERE id=?", (hashlib.sha256(pdf).hexdigest(), "c"*32))
        view = self.view()
        view.pressed.add("Download")
        self.prepare(view, self.a, lambda: store.document_bytes(self.a, "c"*32))
        self.assertIn(base64.b64encode(pdf).decode(), view.rendered_text("iframe"))
        foreign = self.view(self.b)
        foreign.pressed.add("Download")
        with self.assertRaises(PermissionError):
            self.prepare(foreign, self.b, lambda: store.document_bytes(self.b, "c"*32))
        self.assertNotIn("data:application/pdf", foreign.rendered_text())

    def test_logout_expiry_and_workspace_switch_block_old_download_callbacks(self):
        for kind in ("logout", "expiry", "workspace", "identity"):
            view, access = self.view(), self.a
            view.pressed.add("Download")
            if kind == "logout":
                view.session_state.clear()
            elif kind == "expiry":
                access = store.Access(self.a.principal, self.a.workspace, time.time()-1)
            elif kind == "workspace":
                view.session_state.customer_workspace = self.b.workspace
            else:
                view.session_state.customer_identity = self.b.principal
            load = MagicMock(return_value=b"PRIVATE")
            with self.subTest(kind=kind), self.assertRaises(RerunPage):
                self.prepare(view, access, load)
            load.assert_not_called()

    def test_revocation_before_or_during_preparation_prevents_delivery(self):
        view = self.view()
        view.pressed.add("Download")
        def revoke():
            with store.db() as c:
                c.execute("DELETE FROM members WHERE principal=?", (self.a.principal,))
            return b"PRIVATE"
        with self.assertRaises(PermissionError):
            self.prepare(view, self.a, revoke)
        load = MagicMock(return_value=b"PRIVATE")
        with self.assertRaises(PermissionError):
            self.prepare(view, self.a, load)
        load.assert_not_called()
        self.assertNotIn("data:application/pdf", view.rendered_text())

    def test_page_image_has_no_shared_media_url_and_rechecks_membership(self):
        view = self.view()
        private_media.image(view, self.a, b"PNG-FIXTURE", width=800, caption='Page "one"')
        html = next(args[0] for name, args, _ in view.events if name == "html")
        self.assertIn("data:image/png;base64,", html)
        self.assertIn("&quot;one&quot;", html)
        self.assertNotIn("/media/", html)
        self.assertFalse(any(name == "image" for name, _, _ in view.events))
        with store.db() as c:
            c.execute("DELETE FROM members WHERE principal=?", (self.a.principal,))
        view.events.clear()
        with self.assertRaises(PermissionError):
            private_media.image(view, self.a, b"PRIVATE", width=800, caption="Page")
        self.assertNotIn("PRIVATE", view.rendered_text())

    def test_unsupported_mime_is_rejected_before_loading(self):
        view, load = self.view(), MagicMock()
        view.pressed.add("Download")
        with self.assertRaises(ValueError):
            self.prepare(view, self.a, load, mime='text/html" onclick="bad')
        load.assert_not_called()

    def test_prepared_copy_survives_progress_reruns_but_remains_authorized(self):
        view, load = self.view(), MagicMock(return_value=b"PRIVATE")
        view.pressed.add("Download")
        self.prepare(view, self.a, load)
        view.pressed.clear()
        view.events.clear()
        self.prepare(view, self.a, load)
        load.assert_called_once()
        self.assertTrue(any(name == "iframe" for name, _, _ in view.events))
        with store.db() as c:
            c.execute("DELETE FROM members WHERE principal=?", (self.a.principal,))
        view.events.clear()
        with self.assertRaises(PermissionError):
            self.prepare(view, self.a, load)
        self.assertFalse(any(name == "iframe" for name, _, _ in view.events))

    def test_legacy_streamlit_download_uses_inline_component_not_a_public_file(self):
        view = self.view()
        view.iframe = None
        view.pressed.add("Download")
        component = types.ModuleType("streamlit.components.v1")
        component.html = MagicMock()
        with patch.dict(sys.modules, {"streamlit.components.v1": component}):
            self.prepare(view, self.a, lambda: b"PDF")
        component.html.assert_called_once()
        self.assertIn("data:application/pdf;base64,", component.html.call_args.args[0])

    def test_customer_ui_does_not_register_native_public_media(self):
        tree = ast.parse((APP / "pilot/ui.py").read_text(encoding="utf-8"))
        native_calls = [n.func.attr for n in ast.walk(tree) if isinstance(n, ast.Call) and
                        isinstance(n.func, ast.Attribute) and n.func.attr in {"image", "download_button"} and
                        isinstance(n.func.value, ast.Name) and n.func.value.id != "private_media"]
        self.assertEqual(native_calls, [])


if __name__ == "__main__":
    unittest.main()
