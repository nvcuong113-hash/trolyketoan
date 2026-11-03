from __future__ import annotations
import hmac
import os
from typing import Any
from fastapi import FastAPI, Header, HTTPException
from supabase import create_client

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

@app.get("/health")
def health():
    return {"ok": True}

@app.post("/webhooks/sepay")
def sepay_webhook(payload: dict[str, Any], authorization: str | None = Header(default=None)):
    expected = os.getenv("SEPAY_WEBHOOK_API_KEY", "")
    if not expected:
        raise HTTPException(status_code=503, detail="Webhook chưa được cấu hình")
    supplied = (authorization or "").removeprefix("Apikey ").strip()
    if not hmac.compare_digest(supplied, expected):
        raise HTTPException(status_code=401, detail="Xác thực webhook không hợp lệ")
    try:
        event = parse_sepay_event(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    url = os.getenv("SUPABASE_URL", "")
    secret = os.getenv("SUPABASE_SECRET_KEY") or os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")
    if not url or not secret:
        raise HTTPException(status_code=503, detail="Backend thanh toán chưa được cấu hình")
    try:
        db = create_client(url, secret)
        result = db.rpc("apply_sepay_payment", {
            "p_transaction_id": event["transaction_id"],
            "p_content": event["content"],
            "p_transfer_amount": event["amount"],
            "p_payload": event["payload"],
        }).execute()
    except Exception as exc:
        raise HTTPException(status_code=500, detail="Không thể ghi nhận giao dịch") from exc
    return {"success": True, "result": result.data}
