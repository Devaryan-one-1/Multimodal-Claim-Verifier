"""Customer media delivered inline after access checks, without shared file URLs."""
from __future__ import annotations

import base64
from html import escape
import time

from pilot import store

MIMES = {"application/pdf", "image/png", "text/csv", "text/html", "application/json"}


def _authorize(view, access):
    if (view.session_state.get("customer_identity") != access.principal or
            view.session_state.get("customer_workspace", access.workspace) != access.workspace or
            access.expires <= time.time()):
        view.rerun()
    with store.db() as c:
        store.authorize(c, access)


def image(view, access, png, *, width, caption, zoom=None):
    _authorize(view, access)
    if zoom is not None:
        if isinstance(zoom, bool) or not isinstance(zoom, (int, float)) or not 75 <= zoom <= 200:
            raise ValueError("Preview zoom must be between 75 and 200 percent.")
    encoded = base64.b64encode(png).decode("ascii")
    label = escape(caption, quote=True)
    style = (f'display:block;width:{zoom:g}%;max-width:none;height:auto' if zoom is not None else
             f'width:{int(width)}px;max-width:100%;height:auto')
    markup = f'<img src="data:image/png;base64,{encoded}" alt="{label}" style="{style}">'
    if zoom is not None:
        markup = (f'<div class="customer-pdf-preview" role="region" tabindex="0" aria-label="{label}" '
                  f'style="width:100%;max-width:100%;overflow-x:auto">{markup}</div>')
    view.html(markup)
    view.caption(caption)


def download(view, access, label, load, *, filename, mime, key, revision=None):
    # Reading and encoding only happens after the explicit prepare action.
    pressed = view.button(label, key=key)
    prepared = view.session_state.get("customer_prepared_download")
    if prepared and (prepared["principal"] != access.principal or prepared["workspace"] != access.workspace):
        view.session_state.pop("customer_prepared_download", None)
        prepared = None
    if prepared and prepared["key"] == key and prepared.get("revision") != revision:
        view.session_state.pop("customer_prepared_download", None)
        prepared = None
    if pressed:
        _authorize(view, access)
        if mime not in MIMES:
            raise ValueError("Unsupported download type.")
        data = load()
        _authorize(view, access)  # Recheck after any file/export generation.
        encoded = base64.b64encode(data.encode("utf-8") if isinstance(data, str) else data).decode("ascii")
        name = escape(filename.replace("\\", "/").split("/")[-1], quote=True)
        body = ('<html><body style="margin:4px;font-family:system-ui">'
                f'<a download="{name}" href="data:{mime};base64,{encoded}" '
                'style="display:inline-block;padding:10px 14px;border:1px solid #7d8590;'
                'border-radius:6px;color:#0969da;background:#fff;text-decoration:none">Save file</a>'
                '</body></html>')
        # Keep one prepared copy per session so a live update cannot hide Save file.
        prepared = {"key": key, "principal": access.principal, "workspace": access.workspace, "body": body, "revision": revision}
        view.session_state["customer_prepared_download"] = prepared
    if not prepared or prepared["key"] != key:
        return
    _authorize(view, access)
    # New versions expose st.iframe; older supported versions provide components.html.
    frame = getattr(view, "iframe", None)
    if callable(frame):
        frame(prepared["body"], height=64, tab_index=0)
    else:
        from streamlit.components.v1 import html
        html(prepared["body"], height=64, tab_index=0)
    view.caption("Save file downloads the prepared copy. Preparing another copy requires current workspace access.")
