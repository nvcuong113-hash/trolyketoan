from __future__ import annotations
import os
import streamlit as st
from supabase import Client, create_client

def get_public_client() -> Client | None:
    url = os.getenv("SUPABASE_URL") or st.secrets.get("SUPABASE_URL", None)
    key = os.getenv("SUPABASE_PUBLISHABLE_KEY") or os.getenv("SUPABASE_ANON_KEY") or st.secrets.get("SUPABASE_PUBLISHABLE_KEY", None)
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
    result = client.table("subscriptions").select("plan,status,expires_at,tax_codes").eq("user_id", user.id).eq("status","active").order("expires_at", desc=True).limit(1).execute()
    return result.data[0] if result.data else None
