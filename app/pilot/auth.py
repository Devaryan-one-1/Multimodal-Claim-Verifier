"""Supabase email/password auth for the existing customer workspace app.

Fresh SDK client per rerun. Tokens are kept in this browser's Streamlit session,
never in a shared cached client, database, logs, URL parameters or exports.
"""
from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from urllib.parse import urlparse

import streamlit as st
from supabase import create_client
from supabase.client import ClientOptions
from pilot.store import Access, memberships, principal

SESSION = "supabase_auth_session"
RECOVERY = "supabase_recovery_session"


def setting(name, default=""):
    value = os.getenv(name)
    if value is not None:
        return value.strip()
    try:
        return str(st.secrets.get(name, default)).strip()
    except FileNotFoundError:
        return default


def configuration():
    url = setting("SUPABASE_URL").rstrip("/")
    key = setting("SUPABASE_PUBLISHABLE_KEY")
    parsed = urlparse(url)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username
            or parsed.password or parsed.path not in {"", "/"} or parsed.query or parsed.fragment):
        raise ValueError("Set SUPABASE_URL to the HTTPS Project URL, without /auth/v1.")
    if not key.startswith("sb_publishable_"):
        raise ValueError("Set SUPABASE_PUBLISHABLE_KEY to the project's publishable key. Secret and service-role keys are not accepted.")
    return url, key


def make_client(url, key):
    # Do not decorate this function with st.cache_resource or st.cache_data.
    return create_client(url, key, options=ClientOptions(auto_refresh_token=False, persist_session=False))


def pack(session):
    if not session or not session.access_token or not session.refresh_token:
        raise ValueError("No authenticated session was returned.")
    return {"access_token": session.access_token, "refresh_token": session.refresh_token,
            "expires_at": float(session.expires_at or (time.time()+session.expires_in))}


def logout(url, key):
    saved = st.session_state.get(SESSION) or st.session_state.get(RECOVERY)
    revoked = True
    try:
        if saved:
            client = make_client(url, key)
            client.auth.set_session(saved["access_token"], saved["refresh_token"])
            client.auth.sign_out({"scope": "local"})
    except Exception:
        revoked = False
    finally:
        st.session_state.clear()
    st.session_state["auth_notice"] = ("Signed out." if revoked else
        "Signed out of this app. The authentication service could not confirm remote session revocation.")


def accept_session(session):
    st.session_state[SESSION] = pack(session)
    st.rerun()


def recovery_form(client, url, key):
    st.caption("Request a reset code, then enter the code from your email and a new password.")
    with st.form("reset_request", clear_on_submit=True):
        email = st.text_input("Account email", max_chars=254)
        requested = st.form_submit_button("Send reset code")
    if requested:
        try:
            client.auth.reset_password_for_email(email.strip(), {"redirect_to": setting("SUPABASE_SITE_URL", "http://localhost:8501")})
        except Exception:
            # Do not reveal whether an email has an account.
            pass
        st.info("If the account is eligible and email delivery succeeds, a reset code will arrive. Wait before requesting another code.")
    with st.form("reset_confirm", clear_on_submit=True):
        email = st.text_input("Email for reset", max_chars=254)
        code = st.text_input("Reset code", max_chars=32)
        password = st.text_input("New password", type="password", max_chars=256)
        again = st.text_input("Repeat new password", type="password", max_chars=256)
        changed = st.form_submit_button("Set new password")
    if changed:
        if len(password) < 12 or password != again:
            st.error("Use at least 12 characters and matching passwords.")
            return
        try:
            recovery = st.session_state.get(RECOVERY)
            if recovery and recovery.get("email") == email.strip().lower() and recovery.get("valid_until", 0) > time.time():
                response = client.auth.set_session(recovery["access_token"], recovery["refresh_token"])
            else:
                response = client.auth.verify_otp({"email": email.strip(), "token": code.strip(), "type": "recovery"})
            recovery = pack(response.session)
            recovery.update(email=email.strip().lower(), valid_until=time.time()+600)
            st.session_state[RECOVERY] = recovery
            # Verification is performed by Supabase, never by trusting local token contents.
            if not client.auth.get_user(recovery["access_token"]).user:
                raise ValueError("Invalid recovery session.")
            client.auth.update_user({"password": password})
            try:
                client.auth.sign_out({"scope": "global"})
            except Exception:
                st.warning("Password changed, but revoking other sessions could not be confirmed.")
            st.session_state.pop(RECOVERY, None)
            st.session_state.pop(SESSION, None)
            st.success("Password updated. Sign in with your new password.")
        except Exception:
            st.error("Password reset could not finish. Check the code and password policy. A verified reset session is retained briefly so you can retry the password change.")


def login_screen(url, key):
    st.title("Financial claim review")
    st.write("Sign in to check figures against your source reports.")
    if st.session_state.get("auth_notice"):
        st.info(st.session_state.pop("auth_notice"))
    client = make_client(url, key)
    options = ["Sign in", "Confirm email", "Reset password"]
    if setting("SUPABASE_ENABLE_SIGNUP", "true").lower() == "true":
        options.insert(1, "Create account")
    mode = st.radio("Account action", options, horizontal=True)
    if mode == "Reset password":
        recovery_form(client, url, key)
        return
    with st.form("supabase_"+mode, clear_on_submit=True):
        email = st.text_input("Email", max_chars=254)
        if mode == "Confirm email":
            code = st.text_input("Confirmation code", max_chars=32)
        else:
            password = st.text_input("Password", type="password", max_chars=256)
        if mode == "Create account":
            again = st.text_input("Repeat password", type="password", max_chars=256)
        submitted = st.form_submit_button(mode, type="primary")
    if not submitted:
        return
    try:
        if mode == "Sign in":
            response = client.auth.sign_in_with_password({"email": email.strip(), "password": password})
            saved = pack(response.session)
            user = client.auth.get_user(saved["access_token"]).user
            if not user or not user.email_confirmed_at:
                raise ValueError("Confirm your email first.")
            accept_session(response.session)
        elif mode == "Create account":
            if len(password) < 12 or password != again:
                st.error("Use at least 12 characters and matching passwords.")
                return
            response = client.auth.sign_up({"email": email.strip(), "password": password,
                        "options": {"email_redirect_to": setting("SUPABASE_SITE_URL", "http://localhost:8501")}})
            if response.session:
                # Dashboard confirmation settings can differ; always validate before workspace access.
                accept_session(response.session)
            st.info("If signup is eligible, check your email for a confirmation code. Use Confirm email to enter it.")
        else:
            response = client.auth.verify_otp({"email": email.strip(), "token": code.strip(), "type": "email"})
            accept_session(response.session)
    except Exception:
        st.error("Authentication could not complete. Check your details, email confirmation and service availability; repeated attempts may be rate limited.")


def require_access():
    try:
        url, key = configuration()
    except ValueError as exc:
        st.error(str(exc))
        st.stop()
    saved = st.session_state.get(SESSION)
    if not saved:
        login_screen(url, key)
        st.stop()
    try:
        client = make_client(url, key)
        # set_session refreshes expired sessions; preserve any rotated refresh token.
        response = client.auth.set_session(saved["access_token"], saved["refresh_token"])
        saved = pack(response.session)
        st.session_state[SESSION] = saved
        user = client.auth.get_user(saved["access_token"]).user
        if not user or not user.id or not user.email_confirmed_at or saved["expires_at"] <= time.time():
            raise ValueError("No verified user.")
        if getattr(user, "deleted_at", None) or getattr(user, "is_anonymous", False):
            raise ValueError("Account unavailable.")
        banned = getattr(user, "banned_until", None)
        if banned:
            until = banned if isinstance(banned, datetime) else datetime.fromisoformat(str(banned).replace("Z", "+00:00"))
            if until.replace(tzinfo=until.tzinfo or timezone.utc).timestamp() > time.time():
                raise ValueError("Account unavailable.")
    except Exception:
        st.error("Your session could not be verified. Retry, or sign out and sign in again.")
        st.button("Retry verification", on_click=st.rerun)
        st.button("Sign out", on_click=logout, args=(url, key))
        st.stop()
    issuer = url+"/auth/v1"
    who = principal(issuer, str(user.id))
    if st.session_state.get("customer_identity") != who:
        # Preserve only this newly verified session when identities change.
        st.session_state.clear()
        st.session_state[SESSION] = saved
        st.session_state.customer_identity = who
    entries = memberships(who)
    if not entries:
        st.info("Sign-in succeeded. An administrator must grant your workspace access.")
        st.caption("Account subject / Supabase User UID")
        st.code(str(user.id))
        st.caption("Issuer to use in the workspace-grant command")
        st.code(issuer)
        st.button("Refresh workspace access", on_click=st.rerun)
        st.button("Sign out", on_click=logout, args=(url, key))
        st.stop()
    ids = [entry["id"] for entry in entries]
    names = {entry["id"]: entry["name"] for entry in entries}
    if st.session_state.get("customer_workspace") not in ids:
        st.session_state.customer_workspace = ids[0]
    with st.sidebar:
        st.caption("Private workspaces")
        selected = st.selectbox("Workspace", ids, format_func=lambda x: names[x], key="customer_workspace")
        st.button("Sign out", on_click=logout, args=(url, key))
    # Existing service methods independently enforce this deadline and DB membership.
    return Access(who, selected, min(saved["expires_at"], time.time()+300))

# from __future__ import annotations

# import os
# import time
# import streamlit as st
# from pilot.store import Access, memberships, principal


# def require_access():
#     try:
#         issuer = str(st.secrets["pilot"]["issuer"])
#         configured = bool(st.secrets["auth"]["client_id"])
#     except (KeyError, FileNotFoundError):
#         issuer, configured = "", False
#     if not issuer or not configured:
#         st.error("Sign-in is not configured. Ask the administrator to complete setup.")
#         st.stop()
#     if not getattr(st.user, "is_logged_in", False):
#         st.title("Check the figures before you send your report.")
#         st.write("Review financial claims against source reports, with visible calculations and page citations.")
#         st.button("Sign in", on_click=st.login, type="primary")
#         st.stop()
#     info = st.user.to_dict()
#     try:
#         expires = float(info.get("exp", 0))
#     except (ValueError, TypeError):
#         expires = 0
#     if info.get("iss") != issuer or not info.get("sub") or expires <= time.time():
#         st.error("Your sign-in has expired or does not match this service.")
#         st.button("Sign out and sign in again", on_click=st.logout)
#         st.stop()
#     who = principal(issuer, str(info["sub"]))
#     if st.session_state.get("customer_identity") != who:
#         st.session_state.clear()
#         st.session_state.customer_identity = who
#     entries = memberships(who)
#     if not entries:
#         st.info("Your account needs a workspace invitation from the administrator.")
#         st.caption("Give the administrator this account subject (not an API key):")
#         st.code(str(info["sub"]))
#         st.button("Sign out", on_click=st.logout)
#         st.stop()
#     ids = [r["id"] for r in entries]
#     names = {r["id"]: r["name"] for r in entries}
#     with st.sidebar:
#         st.caption("Private workspaces")
#         selected = st.selectbox("Workspace", ids, format_func=lambda x: names[x], key="customer_workspace")
#         st.button("Sign out", on_click=st.logout)
#     return Access(who, selected, expires)
