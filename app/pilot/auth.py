from __future__ import annotations

import os
import time
import streamlit as st
from pilot.store import Access, memberships, principal


def require_access():
    try:
        issuer = str(st.secrets["pilot"]["issuer"])
        configured = bool(st.secrets["auth"]["client_id"])
    except (KeyError, FileNotFoundError):
        issuer, configured = "", False
    if not issuer or not configured:
        st.error("Sign-in is not configured. Ask the administrator to complete setup.")
        st.stop()
    if not getattr(st.user, "is_logged_in", False):
        st.title("Check the figures before you send your report.")
        st.write("Review financial claims against source reports, with visible calculations and page citations.")
        st.button("Sign in", on_click=st.login, type="primary")
        st.stop()
    info = st.user.to_dict()
    try:
        expires = float(info.get("exp", 0))
    except (ValueError, TypeError):
        expires = 0
    if info.get("iss") != issuer or not info.get("sub") or expires <= time.time():
        st.error("Your sign-in has expired or does not match this service.")
        st.button("Sign out and sign in again", on_click=st.logout)
        st.stop()
    who = principal(issuer, str(info["sub"]))
    if st.session_state.get("customer_identity") != who:
        st.session_state.clear()
        st.session_state.customer_identity = who
    entries = memberships(who)
    if not entries:
        st.info("Your account needs a workspace invitation from the administrator.")
        st.caption("Give the administrator this account subject (not an API key):")
        st.code(str(info["sub"]))
        st.button("Sign out", on_click=st.logout)
        st.stop()
    ids = [r["id"] for r in entries]
    names = {r["id"]: r["name"] for r in entries}
    with st.sidebar:
        st.caption("Private workspaces")
        selected = st.selectbox("Workspace", ids, format_func=lambda x: names[x], key="customer_workspace")
        st.button("Sign out", on_click=st.logout)
    return Access(who, selected, expires)
