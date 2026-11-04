from __future__ import annotations
import os
import streamlit as st
from supabase import Client, create_client

def _setting(name: str):
    value = os.getenv(name)
    if value:
        return value
    try:
        return st.secrets.get(name, None)
    except Exception:
        return None

def get_public_client() -> Client | None:
    url = _setting("SUPABASE_URL")
    key = _setting("SUPABASE_PUBLISHABLE_KEY") or _setting("SUPABASE_ANON_KEY")
    if not url or not key:
        return None
    return create_client(url, key)

def sign_up(client: Client, email: str, password: str):
    return client.auth.sign_up({"email": email, "password": password})

def sign_in(client: Client, email: str, password: str):
    return client.auth.sign_in_with_password({"email": email, "password": password})

def restore_session(client: Client):
    session = st.session_state.get("auth_session")
    if not session:
        return None
    try:
        client.auth.set_session(session["access_token"], session["refresh_token"])
        current = client.auth.get_user()
        refreshed = getattr(client.auth, "get_session", lambda: None)()
        if refreshed:
            save_session(refreshed)
        return current.user
    except Exception:
        st.session_state.pop("auth_session", None)
        return None

def save_session(session):
    if session:
        st.session_state["auth_session"] = {
            "access_token": session.access_token,
            "refresh_token": session.refresh_token,
        }

def current_subscription(client: Client):
    user = restore_session(client)
    if not user:
        return None
    result = client.table("subscriptions").select("plan,status,expires_at,tax_codes").eq("user_id", user.id).eq("status","active").order("expires_at", desc=True).limit(5).execute()
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    for row in result.data or []:
        try:
            expiry = datetime.fromisoformat(row["expires_at"].replace("Z","+00:00"))
            if expiry > now:
                return row
        except Exception:
            continue
    return None


def get_admin_client() -> Client | None:
    url = _setting("SUPABASE_URL")
    secret = _setting("SUPABASE_SECRET_KEY") or _setting("SUPABASE_SERVICE_ROLE_KEY")
    if not url or not secret:
        return None
    return create_client(url, secret)
