from __future__ import annotations

import hashlib
import hmac
import os
import time
from datetime import datetime, timezone
from typing import Any

from fastapi import BackgroundTasks, FastAPI, Header, HTTPException, Request
from supabase import create_client
from dotenv import load_dotenv

load_dotenv()

app = FastAPI(title="TroLyKeToan Billing Webhook", docs_url=None, redoc_url=None)


def parse_sepay_event(payload: dict[str, Any]) -> dict[str, Any]:
    try:
        transaction_id = int(payload["id"])
        amount = int(payload["transferAmount"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("Webhook thiếu mã giao dịch hoặc số tiền hợp lệ") from exc
    memo = str(payload.get("content") or payload.get("description") or "")
    direction = str(payload.get("transferType") or "").lower()
    if direction != "in":
        raise ValueError("Chỉ xử lý giao dịch tiền vào")
    if amount <= 0 or not memo:
        raise ValueError("Giao dịch thiếu số tiền hoặc nội dung chuyển khoản")
    return {"transaction_id": transaction_id, "content": memo, "amount": amount, "payload": payload}


def verify_hmac(raw_body: bytes, signature: str | None, timestamp: str | None, secret: str,
                now: int | None = None) -> bool:
    if not signature or not timestamp or not secret:
        return False
    try:
        stamp = int(timestamp)
    except ValueError:
        return False
    current = int(time.time()) if now is None else now
    if abs(current-stamp) > 300:
        return False
    message = timestamp.encode() + b"." + raw_body
    digest = hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()
    return hmac.compare_digest(signature, "sha256=" + digest)


def _server_client():
    url = os.getenv("SUPABASE_URL", "")
    secret = os.getenv("SUPABASE_SECRET_KEY") or os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")
    if not url or not secret:
        raise RuntimeError("Supabase server credentials are not configured")
    return create_client(url, secret)


def _process_event(event_id: str):
    db = _server_client()
    event = db.table("webhook_events").select("id,transaction_id,payload,status,attempts").eq("id",event_id).limit(1).execute().data
    if not event or event[0]["status"] == "processed":
        return
    row = event[0]
    try:
        parsed = parse_sepay_event(row["payload"])
        attempts = int(row.get("attempts") or 0) + 1
        db.table("webhook_events").update({"status":"processing","attempts":attempts}).eq("id",event_id).execute()
        result = db.rpc("apply_sepay_payment", {
            "p_transaction_id": parsed["transaction_id"],
            "p_content": parsed["content"],
            "p_transfer_amount": parsed["amount"],
            "p_payload": parsed["payload"],
        }).execute()
        db.table("webhook_events").update({
            "status":"processed", "processed_at":datetime.now(timezone.utc).isoformat(), "last_error":None,
            "processing_result":result.data
        }).eq("id",event_id).execute()
    except Exception as exc:
        db.table("webhook_events").update({
            "status":"failed", "attempts":int(row.get("attempts") or 0)+1, "last_error":str(exc)[:1000]
        }).eq("id",event_id).execute()


def process_pending_events(limit: int = 50):
    db = _server_client()
    db.table("payments").update({"status":"expired"}).eq("status","pending").lte("expires_at",__import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat()).execute()
    rows = db.table("webhook_events").select("id").in_("status",["pending","failed","processing"]).lt("attempts",5).order("created_at").limit(limit).execute().data
    for row in rows or []:
        _process_event(row["id"])


@app.get("/health")
def health():
    return {"ok": True}


@app.post("/webhooks/sepay")
async def sepay_webhook(request: Request, background_tasks: BackgroundTasks,
                        authorization: str | None = Header(default=None),
                        x_sepay_signature: str | None = Header(default=None),
                        x_sepay_timestamp: str | None = Header(default=None)):
    raw_body = await request.body()
    hmac_secret = os.getenv("SEPAY_WEBHOOK_HMAC_SECRET", "")
    api_key = os.getenv("SEPAY_WEBHOOK_API_KEY", "")
    if hmac_secret:
        if not verify_hmac(raw_body,x_sepay_signature,x_sepay_timestamp,hmac_secret):
            raise HTTPException(status_code=401,detail="Chữ ký webhook không hợp lệ hoặc đã hết hạn")
    elif api_key:
        supplied = (authorization or "")
        if not supplied.startswith("Apikey ") or not hmac.compare_digest(supplied[7:],api_key):
            raise HTTPException(status_code=401,detail="Xác thực webhook không hợp lệ")
    else:
        raise HTTPException(status_code=503,detail="Webhook chưa được cấu hình xác thực")
    try:
        import json
        payload = json.loads(raw_body)
        event = parse_sepay_event(payload)
    except Exception as exc:
        raise HTTPException(status_code=400,detail=f"Payload không hợp lệ: {exc}") from exc
    expected_account = os.getenv("PAYMENT_ACCOUNT","").strip()
    received_account = str(payload.get("accountNumber") or "").strip()
    if expected_account and not hmac.compare_digest(expected_account,received_account):
        raise HTTPException(status_code=400,detail="Giao dịch không vào tài khoản nhận tiền đã cấu hình")
    try:
        db = _server_client()
        queued = db.table("webhook_events").upsert({
            "provider":"sepay",
            "transaction_id":event["transaction_id"],
            "payload":event["payload"],
            "status":"pending",
        },on_conflict="transaction_id",ignore_duplicates=True).execute()
        rows = db.table("webhook_events").select("id,status").eq("transaction_id",event["transaction_id"]).limit(1).execute().data
        if rows and rows[0]["status"] != "processed":
            background_tasks.add_task(_process_event,rows[0]["id"])
    except Exception as exc:
        raise HTTPException(status_code=503,detail="Không thể ghi sự kiện vào hàng đợi thanh toán") from exc
    # SePay requires HTTP 200/201 and exactly {"success": true}; processing continues
    # from the durable queue after the webhook has been acknowledged.
    return {"success": True}


@app.post("/internal/process-pending")
def process_pending(authorization: str | None = Header(default=None)):
    token = os.getenv("BILLING_WORKER_TOKEN","")
    if not token or not authorization or not hmac.compare_digest(authorization.removeprefix("Bearer ").strip(),token):
        raise HTTPException(status_code=401,detail="Unauthorized")
    process_pending_events()
    return {"success":True}
