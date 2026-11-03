"""Real Streamlit widget/proto checks; fixture store and mocked auth, no browser/API calls."""
import json
import hashlib
import os
from pathlib import Path
import sys
import tempfile
import time
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/"app"))


def main():
    from streamlit.testing.v1 import AppTest
    from streamlit.proto.TextInput_pb2 import TextInput
    from pilot import store

    with tempfile.TemporaryDirectory(prefix="customer-ui-fixture-") as root, patch.dict(os.environ, {"CUSTOMER_DATA_ROOT": root}):
        store.initialize()
        access = store.Access("fixture", "a"*32, time.time()+300)
        job_id = "c"*32
        with store.db() as c:
            c.execute("INSERT INTO workspaces VALUES(?,?)", (access.workspace, "Fixture"))
            c.execute("INSERT INTO members VALUES(?,?)", (access.principal, access.workspace))
            c.execute("INSERT INTO jobs(id,workspace,principal,kind,payload,status,stage,result,created,updated) VALUES(?,?,?,'verify',?,'complete','Done',?,?,?)",
                      (job_id, access.workspace, access.principal, json.dumps({"name": "Saved fixture"}),
                       json.dumps({"results": [{"status": "complete", "verdict": "SUPPORTED", "claim_text": "Public fixture"}]}), time.time(), time.time()))

        # Exercise the actual Settings gate/menu with native Streamlit controls.
        # Supabase responses are synthetic; memberships and connection reads use the fixture DB.
        menu_principal = store.principal("https://fixture.supabase.co/auth/v1", "fixture-subject")
        with store.db() as c:
            c.execute("INSERT INTO workspaces VALUES(?,?)", ("b"*32, "Second fixture"))
            for workspace in (access.workspace, "b"*32):
                c.execute("INSERT INTO members VALUES(?,?)", (menu_principal, workspace))
        menu = AppTest.from_string('''
import time
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
import streamlit as st
from pilot import auth, ui
from pilot.responsive import apply_layout
apply_layout()
session = SimpleNamespace(access_token='fixture-access', refresh_token='fixture-refresh', expires_at=time.time()+1800)
user = SimpleNamespace(id='fixture-subject', email_confirmed_at='2026-01-01', deleted_at=None, is_anonymous=False, banned_until=None)
client = MagicMock()
client.auth.refresh_session.return_value = SimpleNamespace(session=session)
client.auth.get_user.return_value = SimpleNamespace(user=user)
st.session_state.setdefault(auth.SESSION, vars(session).copy())
with patch.object(auth, 'configuration', return_value=('https://fixture.supabase.co','sb_publishable_fixture')), patch.object(auth, 'make_client', return_value=client):
    access = auth.require_access(settings_body=lambda current: ui.connection_panel(current,'customer_'+current.workspace+'_'))
st.text('Selected workspace: '+access.workspace)
''').run(timeout=15)
        assert not menu.exception
        assert len(menu.get("popover")) == 1
        assert len(menu.selectbox) == 1 and menu.selectbox[0].label == "Workspace"
        assert len(menu.text_input) == 3  # one set of model/key fields, password masked
        assert menu.text_input[2].proto.type == TextInput.PASSWORD
        model_key = "customer_"+access.workspace+"_text_model"
        menu.text_input(key=model_key).input("fixture/unsaved-model").run(timeout=15)
        assert not menu.exception
        menu.selectbox[0].set_value("b"*32).run(timeout=15)
        assert not menu.exception
        assert menu.text_input(key="customer_"+"b"*32+"_text_model").value == ""
        menu.selectbox[0].set_value(access.workspace).run(timeout=15)
        assert not menu.exception
        assert menu.text_input(key=model_key).value == "fixture/unsaved-model"

        drafts = AppTest.from_string('''
import streamlit as st
from pilot import ui
from pilot.responsive import apply_layout, navigation
apply_layout()
area = navigation('customer_fixture_')
if area == 'Reviews':
    ui.draft_text('Claim draft', 'customer_fixture_claim', multiline=True)
''').run(timeout=15)
        assert not drafts.exception and len(drafts.radio) == 1
        drafts.text_area[0].input("Retain this unfinished claim").run(timeout=15)
        drafts.radio[0].set_value("Documents").run(timeout=15)
        assert not drafts.exception and not drafts.text_area
        drafts.radio[0].set_value("Reviews").run(timeout=15)
        assert not drafts.exception
        assert drafts.text_area[0].value == "Retain this unfinished claim"

        # Real PDF rasterization through the customer viewer; no original project PDF.
        import pymupdf
        with pymupdf.open() as pdf:
            page = pdf.new_page(width=612, height=792)
            page.insert_text((72, 72), "Public source fixture: 14.9 billion")
            source_pdf = pdf.tobytes()
        source_sha = hashlib.sha256(source_pdf).hexdigest()
        source_path = store.file_path(access.workspace, "data", "documents", "d"*32, "original.pdf")
        source_path.parent.mkdir(parents=True)
        source_path.write_bytes(source_pdf)
        with store.db() as c:
            c.execute("INSERT INTO documents(id,workspace,name,sha,status,created) VALUES(?,?,?,?,?,?)",
                      ("d"*32, access.workspace, "Public fixture.pdf", source_sha, "ready", time.time()))
        preview = AppTest.from_string('''
import time
import streamlit as st
from pilot import store, ui
access = store.Access('fixture', 'a'*32, time.time()+300)
st.session_state.setdefault('customer_identity', access.principal)
st.session_state.setdefault('customer_workspace', access.workspace)
source = store.documents(access)[0]
result = {'evidence': [{'id':'fixture-citation', 'document_id':source['id'], 'page':1}]}
ui.viewer(access, result, 'preview_', versions={source['id']:source['sha']})
''').run(timeout=15)
        assert not preview.exception and preview.slider[0].value == 100
        preview.slider[0].set_value(175).run(timeout=15)
        assert not preview.exception
        image_markup = next(element.proto.body for element in preview.get("html") if "data:image/png;base64," in element.proto.body)
        assert 'width:175%' in image_markup and 'overflow-x:auto' in image_markup
        assert 'tabindex="0"' in image_markup and '/media/' not in image_markup

        history = AppTest.from_string('''
import streamlit as st
import time
from pilot import store, ui
from pilot.responsive import navigation
access = store.Access('fixture', 'a'*32, time.time()+300)
key = 'customer_'+access.workspace+'_'
st.session_state.setdefault('customer_identity', access.principal)
st.session_state.setdefault('customer_workspace', access.workspace)
st.session_state.setdefault(key+'area', 'History')
area = navigation(key)
if area == 'History':
    ui.history_page(access, key)
else:
    st.text('Opened review: '+st.session_state.get(key+'selected_review',''))
''').run(timeout=15)
        assert not history.exception
        history.button[0].click().run(timeout=15)
        assert not history.exception
        assert history.radio[0].value == "Reviews"
        assert job_id in history.text[0].value

        download = AppTest.from_string('''
import hashlib, json, time
import streamlit as st
from pilot import store, private_media
from pilot.exports import exports
access = store.Access('fixture', 'a'*32, time.time()+300)
st.session_state.setdefault('customer_identity', access.principal)
st.session_state.setdefault('customer_workspace', access.workspace)
try:
    job = store.get_job(access, 'c'*32)
    revision = hashlib.sha256((job['result']+json.dumps(store.decisions(access,'c'*32),sort_keys=True)).encode()).hexdigest()
    private_media.download(st, access, 'Export CSV', lambda: exports(access,'c'*32)[0], filename='fixture.csv', mime='text/csv', key='export', revision=revision)
except PermissionError:
    st.warning('Access unavailable')
''').run(timeout=15)
        assert not download.exception and not download.get("iframe")
        download.button[0].click().run(timeout=15)
        assert not download.exception
        frame = download.get("iframe")[0].proto
        assert "data:text/csv;base64," in frame.srcdoc
        assert "/media/" not in frame.srcdoc and not frame.src
        store.decide(access, job_id, 0, "reviewed", "Changed saved note")
        download.run(timeout=15)
        assert not download.exception and not download.get("iframe")
        with store.db() as c:
            c.execute("DELETE FROM members WHERE principal=?", (access.principal,))
        download.button[0].click().run(timeout=15)
        assert not download.exception and not download.get("iframe")
        assert "Access unavailable" in download.warning[0].value
    print("Real Streamlit Settings/workspace/model drafts, navigation/claim drafts, PDF preview zoom, history, inline export, revision invalidation and revocation checks passed (no browser/live auth).")


if __name__ == "__main__":
    main()
