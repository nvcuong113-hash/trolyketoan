from __future__ import annotations

import json
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


GEMINI_INTERACTIONS_URL = "https://generativelanguage.googleapis.com/v1beta/interactions"


def create_interaction(api_key: str, model: str, input_parts: list[dict[str, Any]],
                       response_format: dict[str, Any] | None = None,
                       timeout: int = 120) -> dict[str, Any]:
    """Call Gemini Interactions API without creating a Gemini Files API upload."""
    if not api_key:
        raise ValueError("Chưa cấu hình GEMINI_API_KEY.")
    if not model:
        raise ValueError("Chưa cấu hình GEMINI_MODEL.")

    body: dict[str, Any] = {
        "model": model,
        "store": False,
        "input": input_parts,
    }
    if response_format:
        body["response_format"] = response_format

    request = Request(
        GEMINI_INTERACTIONS_URL,
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "x-goog-api-key": api_key,
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        try:
            details = exc.read().decode("utf-8", errors="replace")
        except Exception:
            details = ""
        raise RuntimeError(f"Gemini API trả lỗi HTTP {exc.code}: {details[:800]}") from exc
    except URLError as exc:
        raise RuntimeError(f"Không kết nối được Gemini API: {exc.reason}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError("Gemini API trả về phản hồi không phải JSON hợp lệ.") from exc

    status = str(payload.get("status") or "").lower()
    if status and status not in {"completed", "done"}:
        message = payload.get("error") or payload.get("status")
        raise RuntimeError(f"Gemini chưa hoàn thành yêu cầu: {message}")
    return payload


def interaction_text(response: dict[str, Any]) -> str:
    direct = response.get("output_text")
    if isinstance(direct, str) and direct.strip():
        return direct

    fragments: list[str] = []
    for step in response.get("steps") or []:
        if step.get("type") != "model_output":
            continue
        for item in step.get("content") or []:
            if item.get("type") == "text" and item.get("text"):
                fragments.append(item["text"])

    # Compatibility with an older Interactions response envelope.
    if not fragments:
        for item in response.get("outputs") or []:
            if item.get("type") == "text" and item.get("text"):
                fragments.append(item["text"])

    if not fragments:
        raise ValueError("Gemini không trả về nội dung văn bản.")
    return "\n".join(fragments)
