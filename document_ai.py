from __future__ import annotations

import base64
import json
import re
import unicodedata
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

import pandas as pd


MAX_DOCUMENT_BYTES = 25 * 1024 * 1024
SUPPORTED_EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg"}
EPSILON_VND = 1_000


def normalize_text(value: object) -> str:
    if value is None or pd.isna(value):
        return ""
    text = str(value).casefold().strip().replace("đ", "d")
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", re.sub(r"[\W_]+", " ", text, flags=re.UNICODE)).strip()


def normalize_tax_code(value: object) -> str:
    """Keep branch suffixes such as -002 and -003; never collapse them to a parent MST."""
    if value is None or pd.isna(value):
        return ""
    raw = str(value).strip().upper()
    raw = re.sub(r"\s+", "", raw)
    raw = re.sub(r"[^0-9-]", "", raw)
    raw = re.sub(r"-{2,}", "-", raw).strip("-")
    return raw


def normalize_invoice_number(value: object) -> str:
    if value is None or pd.isna(value):
        return ""
    raw = str(value).strip().upper()
    compact = re.sub(r"[^A-Z0-9]", "", raw)
    if compact.isdigit():
        return compact.lstrip("0") or "0"
    return compact


def parse_amount(value: object) -> float:
    if value is None or pd.isna(value):
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    raw = str(value).strip().replace("₫", "").replace("VND", "").replace(" ", "")
    if not raw or raw in {"-", "—"}:
        return 0.0
    if "," in raw and "." in raw:
        raw = raw.replace(".", "").replace(",", ".") if raw.rfind(",") > raw.rfind(".") else raw.replace(",", "")
    elif "," in raw:
        tail = raw.rsplit(",", 1)[-1]
        raw = raw.replace(",", ".") if len(tail) in (1, 2) else raw.replace(",", "")
    elif raw.count(".") > 1 or ("." in raw and len(raw.rsplit(".", 1)[-1]) == 3):
        raw = raw.replace(".", "")
    try:
        return float(raw)
    except ValueError:
        return 0.0


def _similarity(left: object, right: object) -> float:
    a, b = normalize_text(left), normalize_text(right)
    return SequenceMatcher(None, a, b).ratio() if a and b else 0.0


def _find_column(columns, aliases, required=False):
    norm = {normalize_text(c): str(c) for c in columns}
    for alias in aliases:
        key = normalize_text(alias)
        if key in norm:
            return norm[key]
    for alias in aliases:
        key = normalize_text(alias)
        matches = [str(c) for c in columns if key and key in normalize_text(c)]
        if matches:
            return matches[0]
    if required:
        raise ValueError("Thiếu cột bắt buộc. Cần một trong: " + ", ".join(aliases))
    return None


def _date_text(value: object) -> str | None:
    if value is None or pd.isna(value) or str(value).strip() == "":
        return None
    parsed = pd.to_datetime(value, errors="coerce", dayfirst=True)
    return parsed.date().isoformat() if pd.notna(parsed) else None


def _schema() -> dict[str, Any]:
    nullable_string = {"type": ["string", "null"]}
    nullable_number = {"type": ["number", "null"]}
    invoice = {
        "type": "object", "additionalProperties": False,
        "properties": {
            "seller_name": nullable_string, "seller_tax_code": nullable_string,
            "buyer_name": nullable_string, "buyer_tax_code": nullable_string,
            "invoice_symbol": nullable_string, "invoice_number": nullable_string,
            "invoice_date": nullable_string, "net_amount": nullable_number,
            "vat_amount": nullable_number, "total_amount": nullable_number,
            "currency": nullable_string, "confidence": {"type": "number"},
            "evidence": nullable_string
        },
        "required": ["seller_name", "seller_tax_code", "buyer_name", "buyer_tax_code",
                     "invoice_symbol", "invoice_number", "invoice_date", "net_amount",
                     "vat_amount", "total_amount", "currency", "confidence", "evidence"]
    }
    transaction = {
        "type": "object", "additionalProperties": False,
        "properties": {
            "transaction_date": nullable_string,
            "direction": {"type": "string", "enum": ["debit", "credit", "unknown"]},
            "amount": nullable_number, "counterparty_name": nullable_string,
            "counterparty_tax_code": nullable_string, "counterparty_account": nullable_string,
            "counterparty_bank": nullable_string, "description": nullable_string,
            "reference": nullable_string, "confidence": {"type": "number"},
            "evidence": nullable_string
        },
        "required": ["transaction_date", "direction", "amount", "counterparty_name",
                     "counterparty_tax_code", "counterparty_account", "counterparty_bank",
                     "description", "reference", "confidence", "evidence"]
    }
    return {
        "type": "object", "additionalProperties": False,
        "properties": {
            "document_type": {"type": "string", "enum": ["invoice", "bank_statement", "payment_proof", "receipt", "other"]},
            "document_confidence": {"type": "number"},
            "document_summary": {"type": "string"},
            "invoices": {"type": "array", "items": invoice},
            "transactions": {"type": "array", "items": transaction},
            "warnings": {"type": "array", "items": {"type": "string"}}
        },
        "required": ["document_type", "document_confidence", "document_summary", "invoices", "transactions", "warnings"]
    }


def _response_text(response: Any) -> str:
    text = getattr(response, "output_text", None)
    if text:
        return text
    fragments = []
    for item in getattr(response, "output", []) or []:
        for content in getattr(item, "content", []) or []:
            value = getattr(content, "text", None)
            if value:
                fragments.append(value)
    if not fragments:
        raise ValueError("Mô hình không trả về nội dung OCR có cấu trúc.")
    return "\n".join(fragments)


def extract_document(filename: str, content: bytes, api_key: str,
                     model: str = "gpt-4o-mini", pdf_detail: str = "high") -> dict[str, Any]:
    """Extract fields from an in-memory PDF or image. No local file is written or uploaded to Files API."""
    suffix = Path(filename).suffix.lower()
    if suffix not in SUPPORTED_EXTENSIONS:
        raise ValueError("Chỉ hỗ trợ PDF, PNG, JPG hoặc JPEG trong chức năng OCR.")
    if not content:
        raise ValueError("Tệp tải lên đang rỗng.")
    if len(content) > MAX_DOCUMENT_BYTES:
        raise ValueError("Mỗi tệp OCR không được vượt quá 25 MB.")
    if not api_key:
        raise ValueError("Chưa cấu hình OPENAI_API_KEY.")
    try:
        from openai import OpenAI
    except ImportError as exc:
        raise RuntimeError("Thiếu thư viện OpenAI. Hãy cài requirements.txt mới nhất.") from exc

    encoded = base64.b64encode(content).decode("ascii")
    if suffix == ".pdf":
        file_part = {
            "type": "input_file", "filename": Path(filename).name,
            "file_data": f"data:application/pdf;base64,{encoded}",
            "detail": "low" if pdf_detail == "low" else "high"
        }
    else:
        mime = "image/png" if suffix == ".png" else "image/jpeg"
        file_part = {
            "type": "input_image",
            "image_url": f"data:{mime};base64,{encoded}",
            "detail": "high"
        }

    instructions = (
        "Bạn là bộ trích xuất chứng từ kế toán Việt Nam. Đọc toàn bộ trang/ảnh và chỉ trả về JSON theo schema. "
        "Phân loại invoice, bank_statement, payment_proof, receipt hoặc other. "
        "Một hóa đơn trả về một bản ghi invoice; hóa đơn có nhiều mức thuế vẫn chỉ có một tổng hóa đơn, "
        "không tạo nhiều hóa đơn giả. Sao kê phải trích xuất từng dòng giao dịch nhìn thấy, direction=debit "
        "cho tiền ra và credit cho tiền vào. Ảnh/ủy nhiệm chi chuyển tiền thường là debit. "
        "Không tự suy đoán MST, số hóa đơn, tài khoản, ngày hoặc số tiền; trường không đọc rõ phải để null "
        "và thêm cảnh báo. Giữ nguyên số hóa đơn kể cả số 0 ở đầu; giữ đầy đủ MST chi nhánh như -001/-002/-003. "
        "Không bỏ qua giao dịch nhỏ hoặc phí ngân hàng. Amount là số VND dạng số, không có dấu phân cách. "
        "Evidence ghi ngắn gọn vị trí/trích dẫn nhìn thấy (ví dụ trang 2, dòng giao dịch 5, vùng tổng thanh toán). "
        "Confidence nằm trong khoảng 0 đến 1. Không đưa ra tư vấn thuế hay kết luận pháp lý."
    )
    client = OpenAI(api_key=api_key)
    response = client.responses.create(
        model=model,
        input=[{"role": "user", "content": [
            file_part, {"type": "input_text", "text": instructions}
        ]}],
        store=False,
        text={"format": {
            "type": "json_schema",
            "name": "accounting_document_extraction",
            "strict": True,
            "schema": _schema()
        }}
    )
    try:
        result = json.loads(_response_text(response))
    except json.JSONDecodeError as exc:
        raise ValueError("AI trả về dữ liệu không đúng cấu trúc JSON. Hãy thử lại hoặc kiểm tra file.") from exc
    result["source_file"] = Path(filename).name
    return result


def documents_to_dataframes(extractions: list[dict[str, Any]]) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    invoices, transactions, summaries = [], [], []
    for doc in extractions:
        source = doc.get("source_file", "")
        summaries.append({
            "Tệp": source, "Loại chứng từ": doc.get("document_type", "other"),
            "Độ tin cậy": round(float(doc.get("document_confidence") or 0), 3),
            "Mô tả": doc.get("document_summary", ""),
            "Cảnh báo": " | ".join(doc.get("warnings") or [])
        })
        for row in doc.get("invoices") or []:
            invoices.append({
                "Tệp nguồn": source, "Số hóa đơn": row.get("invoice_number"),
                "Ký hiệu": row.get("invoice_symbol"), "Ngày hóa đơn": _date_text(row.get("invoice_date")),
                "MST người bán": normalize_tax_code(row.get("seller_tax_code")),
                "Tên người bán": row.get("seller_name"), "MST người mua": normalize_tax_code(row.get("buyer_tax_code")),
                "Tên người mua": row.get("buyer_name"), "Tiền trước thuế": row.get("net_amount"),
                "Tiền thuế": row.get("vat_amount"), "Tổng thanh toán": row.get("total_amount"),
                "Tiền tệ": row.get("currency"), "Độ tin cậy": round(float(row.get("confidence") or 0), 3),
                "Bằng chứng OCR": row.get("evidence")
            })
        for row in doc.get("transactions") or []:
            transactions.append({
                "Tệp nguồn": source, "Ngày giao dịch": _date_text(row.get("transaction_date")),
                "Chiều giao dịch": row.get("direction", "unknown"),
                "Số tiền": row.get("amount"), "Tên đối tác": row.get("counterparty_name"),
                "MST đối tác": normalize_tax_code(row.get("counterparty_tax_code")),
                "Tài khoản đối tác": row.get("counterparty_account"),
                "Ngân hàng đối tác": row.get("counterparty_bank"),
                "Nội dung": row.get("description"), "Mã tham chiếu": row.get("reference"),
                "Độ tin cậy": round(float(row.get("confidence") or 0), 3),
                "Bằng chứng OCR": row.get("evidence")
            })
    invoice_columns = ["Tệp nguồn", "Số hóa đơn", "Ký hiệu", "Ngày hóa đơn", "MST người bán", "Tên người bán",
                       "MST người mua", "Tên người mua", "Tiền trước thuế", "Tiền thuế", "Tổng thanh toán",
                       "Tiền tệ", "Độ tin cậy", "Bằng chứng OCR"]
    transaction_columns = ["Tệp nguồn", "Ngày giao dịch", "Chiều giao dịch", "Số tiền", "Tên đối tác", "MST đối tác",
                           "Tài khoản đối tác", "Ngân hàng đối tác", "Nội dung", "Mã tham chiếu", "Độ tin cậy", "Bằng chứng OCR"]
    summary_columns = ["Tệp", "Loại chứng từ", "Độ tin cậy", "Mô tả", "Cảnh báo"]
    return (
        pd.DataFrame(invoices, columns=invoice_columns),
        pd.DataFrame(transactions, columns=transaction_columns),
        pd.DataFrame(summaries, columns=summary_columns)
    )


def normalize_bank_statement(frame: pd.DataFrame) -> pd.DataFrame:
    """Normalize bank XLSX/CSV data into the same transaction schema produced by OCR."""
    date_col = _find_column(frame.columns, ["Ngày giao dịch", "Ngày hạch toán", "Ngày", "Transaction date", "Date"])
    debit_col = _find_column(frame.columns, ["Số tiền ghi Nợ", "Ghi Nợ", "Tiền ra", "Debit", "Amount out"])
    credit_col = _find_column(frame.columns, ["Số tiền ghi Có", "Ghi Có", "Tiền vào", "Credit", "Amount in"])
    amount_col = _find_column(frame.columns, ["Số tiền", "Amount"])
    desc_col = _find_column(frame.columns, ["Nội dung giao dịch", "Nội dung", "Diễn giải", "Description", "Transaction details"])
    name_col = _find_column(frame.columns, ["Tên đối tác", "Tên nhà cung cấp", "Tên khách hàng", "Đối tác", "Counterparty"])
    tax_col = _find_column(frame.columns, ["MST đối tác", "MST nhà cung cấp", "Mã số thuế", "Tax code"])
    reference_col = _find_column(frame.columns, ["Mã giao dịch", "Mã tham chiếu", "Reference", "Transaction ID"])
    rows = []
    for _, row in frame.iterrows():
        debit = parse_amount(row.get(debit_col)) if debit_col else 0
        credit = parse_amount(row.get(credit_col)) if credit_col else 0
        if not debit and not credit and amount_col:
            amount = parse_amount(row.get(amount_col))
            direction = "unknown"
        elif debit > 0:
            amount, direction = debit, "debit"
        elif credit > 0:
            amount, direction = credit, "credit"
        else:
            continue
        if amount <= 0:
            continue
        rows.append({
            "Tệp nguồn": "Bảng kê ngân hàng", "Ngày giao dịch": _date_text(row.get(date_col)) if date_col else None,
            "Chiều giao dịch": direction, "Số tiền": amount,
            "Tên đối tác": str(row.get(name_col, "") or "") if name_col else "",
            "MST đối tác": normalize_tax_code(row.get(tax_col)) if tax_col else "",
            "Tài khoản đối tác": "", "Ngân hàng đối tác": "",
            "Nội dung": str(row.get(desc_col, "") or "") if desc_col else "",
            "Mã tham chiếu": str(row.get(reference_col, "") or "") if reference_col else "",
            "Độ tin cậy": 1.0, "Bằng chứng OCR": "Dữ liệu đọc trực tiếp từ bảng kê"
        })
    return pd.DataFrame(rows, columns=[
        "Tệp nguồn", "Ngày giao dịch", "Chiều giao dịch", "Số tiền", "Tên đối tác", "MST đối tác",
        "Tài khoản đối tác", "Ngân hàng đối tác", "Nội dung", "Mã tham chiếu", "Độ tin cậy", "Bằng chứng OCR"
    ])


def _register_rows(frame: pd.DataFrame, source: str) -> list[dict[str, Any]]:
    number_col = _find_column(frame.columns, ["Số hóa đơn", "Số HĐ", "Invoice number"], required=True)
    tax_col = _find_column(frame.columns, ["Mã số thuế người bán", "MST người bán/MST người xuất hàng",
                                          "MST người bán", "Mã số thuế", "MST", "Tax code"])
    name_col = _find_column(frame.columns, ["Tên người bán/Tên người xuất hàng", "Tên người bán", "Nhà cung cấp", "Đơn vị bán"])
    date_col = _find_column(frame.columns, ["Ngày hóa đơn", "Ngày lập", "Ngày", "Invoice date"])
    symbol_col = _find_column(frame.columns, ["Ký hiệu hóa đơn", "Ký hiệu HĐ", "Ký hiệu"])
    net_col = _find_column(frame.columns, ["Tổng tiền chưa thuế", "Giá trị HHDV mua vào chưa có thuế",
                                          "Giá trị HHDV chưa thuế", "Tiền trước thuế", "Tiền chưa thuế"])
    vat_col = _find_column(frame.columns, ["Tổng tiền thuế", "Thuế GTGT", "Tiền thuế GTGT", "Tiền thuế"])
    total_col = _find_column(frame.columns, ["Tổng tiền thanh toán", "Tổng thanh toán", "Tổng tiền phải thanh toán"])
    aggregates: dict[tuple, dict[str, Any]] = {}
    for _, row in frame.iterrows():
        number = normalize_invoice_number(row.get(number_col))
        if not number:
            continue
        tax = normalize_tax_code(row.get(tax_col)) if tax_col else ""
        symbol_value = row.get(symbol_col) if symbol_col else ""
        symbol = "" if symbol_value is None or pd.isna(symbol_value) else str(symbol_value).strip()
        clean_symbol = normalize_invoice_number(symbol)
        key = (number, tax, clean_symbol)
        net = parse_amount(row.get(net_col)) if net_col else 0
        vat = parse_amount(row.get(vat_col)) if vat_col else 0
        reported_total = parse_amount(row.get(total_col)) if total_col else 0
        name_value = row.get(name_col) if name_col else ""
        name = "" if name_value is None or pd.isna(name_value) else str(name_value).strip()
        date_value = row.get(date_col) if date_col else None
        if key not in aggregates:
            aggregates[key] = {
                "source": source, "invoice_number": number, "invoice_number_raw": str(row.get(number_col)),
                "symbol": symbol, "tax": tax, "name": name,
                "date": _date_text(date_value) if date_col else None,
                "net": 0.0, "vat": 0.0, "total": 0.0, "reported_totals": [], "line_count": 0
            }
        item = aggregates[key]
        item["net"] += net
        item["vat"] += vat
        if reported_total > 0:
            item["reported_totals"].append(reported_total)
        item["line_count"] += 1
        if not item["name"] and name:
            item["name"] = name
    for item in aggregates.values():
        totals = item.pop("reported_totals")
        # Some exports repeat the full invoice total on every tax-rate row;
        # others provide a separate line total. Avoid multiplying the invoice total.
        if totals:
            if len(totals) > 1 and max(totals) - min(totals) <= 2:
                item["total"] = max(totals)
            else:
                item["total"] = sum(totals)
        else:
            item["total"] = item["net"] + item["vat"]
    return list(aggregates.values())


def reconcile_invoice_registers(left: pd.DataFrame, right: pd.DataFrame,
                                epsilon: int = 2) -> tuple[pd.DataFrame, dict[str, int]]:
    """Compare purchase register and tax portal data with strict invoice/MST/symbol controls."""
    a = _register_rows(left, "Bảng kê quản trị")
    b = _register_rows(right, "Danh sách hóa đơn")
    index: dict[str, list[int]] = {}
    for idx, item in enumerate(b):
        index.setdefault(item["invoice_number"], []).append(idx)
    used: set[int] = set()
    output = []
    for item in a:
        candidates = [idx for idx in index.get(item["invoice_number"], []) if idx not in used]
        selected = None
        if len(candidates) > 1:
            exact_symbol = [
                idx for idx in candidates
                if item["symbol"] and b[idx]["symbol"]
                and normalize_invoice_number(item["symbol"]) == normalize_invoice_number(b[idx]["symbol"])
            ]
            exact_tax = [idx for idx in candidates if item["tax"] and b[idx]["tax"] == item["tax"]]
            if len(exact_symbol) == 1:
                selected = exact_symbol[0]
            elif len(exact_tax) == 1:
                selected = exact_tax[0]
            else:
                output.append(_register_result(item, None, "Cần kiểm tra – nhiều ứng viên"))
                continue
        elif len(candidates) == 1:
            selected = candidates[0]
        if selected is None:
            output.append(_register_result(item, None, "Thiếu ở danh sách hóa đơn"))
            continue
        other = b[selected]
        used.add(selected)
        if not item["tax"] or not other["tax"]:
            status = "Thiếu MST – cần xác minh"
        elif item["tax"] != other["tax"]:
            status = "Nghi vấn sai MST/chi nhánh"
        elif item["symbol"] and other["symbol"] and normalize_invoice_number(item["symbol"]) != normalize_invoice_number(other["symbol"]):
            status = "Nghi vấn ký hiệu hóa đơn"
        elif (abs(item["net"] - other["net"]) > epsilon or
              abs(item["vat"] - other["vat"]) > epsilon or
              abs(item["total"] - other["total"]) > epsilon):
            status = "Lệch giá trị"
        else:
            status = "Khớp hoàn toàn"
        output.append(_register_result(item, other, status))
    for idx, item in enumerate(b):
        if idx not in used:
            output.append(_register_result(None, item, "Chỉ có ở danh sách hóa đơn"))
    result = pd.DataFrame(output)
    labels = [
        "Khớp hoàn toàn", "Nghi vấn sai MST/chi nhánh", "Nghi vấn ký hiệu hóa đơn",
        "Thiếu MST – cần xác minh", "Lệch giá trị", "Thiếu ở danh sách hóa đơn",
        "Chỉ có ở danh sách hóa đơn", "Cần kiểm tra – nhiều ứng viên"
    ]
    counts = {"Tổng dòng sau gộp": len(result)}
    counts.update({label: int((result["Trạng thái"] == label).sum()) if not result.empty else 0 for label in labels})
    return result, counts


def _register_result(left: dict | None, right: dict | None, status: str) -> dict:
    a, b = left or {}, right or {}
    return {
        "Trạng thái": status, "Số hóa đơn chuẩn": a.get("invoice_number") or b.get("invoice_number"),
        "Ký hiệu bảng kê": a.get("symbol"), "Ký hiệu danh sách": b.get("symbol"),
        "Số HĐ bảng kê": a.get("invoice_number_raw"), "Số HĐ danh sách": b.get("invoice_number_raw"),
        "MST bảng kê": a.get("tax"), "MST danh sách": b.get("tax"),
        "Tên người bán bảng kê": a.get("name"), "Tên người bán danh sách": b.get("name"),
        "Ngày bảng kê": a.get("date"), "Ngày danh sách": b.get("date"),
        "Tiền trước thuế bảng kê": round(a.get("net", 0), 2),
        "Tiền trước thuế danh sách": round(b.get("net", 0), 2),
        "Thuế GTGT bảng kê": round(a.get("vat", 0), 2),
        "Thuế GTGT danh sách": round(b.get("vat", 0), 2),
        "Tổng thanh toán bảng kê": round(a.get("total", 0), 2),
        "Tổng thanh toán danh sách": round(b.get("total", 0), 2),
        "Số dòng thuế bảng kê": a.get("line_count", 0),
        "Số dòng thuế danh sách": b.get("line_count", 0)
    }


def map_invoice_payments(invoices: pd.DataFrame, transactions: pd.DataFrame,
                         epsilon: int = EPSILON_VND) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Conservative supplier-payment mapping: explicit reference, unique exact match, then same-supplier subset sum."""
    invoice_number_col = _find_column(invoices.columns, ["Số hóa đơn", "invoice_number"], required=True)
    inv_tax_col = _find_column(invoices.columns, ["MST người bán", "seller_tax_code", "MST đối tác"])
    inv_name_col = _find_column(invoices.columns, ["Tên người bán", "seller_name", "Tên nhà cung cấp"])
    inv_amount_col = _find_column(invoices.columns, ["Tổng thanh toán", "total_amount", "Số tiền phải trả"], required=True)
    inv_date_col = _find_column(invoices.columns, ["Ngày hóa đơn", "invoice_date", "Ngày lập"])
    tx_direction_col = _find_column(transactions.columns, ["Chiều giao dịch", "direction"])
    tx_amount_col = _find_column(transactions.columns, ["Số tiền", "amount"], required=True)
    tx_tax_col = _find_column(transactions.columns, ["MST đối tác", "counterparty_tax_code", "MST nhà cung cấp"])
    tx_name_col = _find_column(transactions.columns, ["Tên đối tác", "counterparty_name", "Tên nhà cung cấp"])
    tx_date_col = _find_column(transactions.columns, ["Ngày giao dịch", "transaction_date", "Ngày"])
    tx_desc_col = _find_column(transactions.columns, ["Nội dung", "description", "Diễn giải"])
    tx_ref_col = _find_column(transactions.columns, ["Mã tham chiếu", "reference", "Mã giao dịch"])

    inv_rows = invoices.to_dict("records")
    tx_rows = transactions.to_dict("records")
    remaining = [max(0.0, parse_amount(r.get(inv_amount_col))) for r in inv_rows]
    paid = [0.0] * len(inv_rows)
    allocated_refs: list[list[str]] = [[] for _ in inv_rows]
    tx_output = []
    mapping_output = []

    for tx_idx, tx in enumerate(tx_rows):
        direction = str(tx.get(tx_direction_col, "debit") if tx_direction_col else "debit").lower()
        amount = parse_amount(tx.get(tx_amount_col))
        if amount <= 0 or direction not in {"debit", "chi", "ghi no", "out"}:
            continue
        desc = str(tx.get(tx_desc_col, "") or "") if tx_desc_col else ""
        ref = str(tx.get(tx_ref_col, "") or "") if tx_ref_col else ""
        reference_text = normalize_invoice_number(f"{desc} {ref}")
        tx_tax = normalize_tax_code(tx.get(tx_tax_col)) if tx_tax_col else ""
        tx_name = str(tx.get(tx_name_col, "") or "") if tx_name_col else ""
        tx_date = _date_text(tx.get(tx_date_col)) if tx_date_col else None

        candidates = []
        for idx, inv in enumerate(inv_rows):
            if remaining[idx] <= epsilon:
                continue
            inv_tax = normalize_tax_code(inv.get(inv_tax_col)) if inv_tax_col else ""
            inv_name = str(inv.get(inv_name_col, "") or "") if inv_name_col else ""
            if tx_tax and inv_tax and tx_tax != inv_tax:
                continue
            tax_match = bool(tx_tax and inv_tax and tx_tax == inv_tax)
            name_score = _similarity(tx_name or desc, inv_name)
            identity = tax_match or name_score >= 0.82
            if not identity:
                continue
            inv_no = normalize_invoice_number(inv.get(invoice_number_col))
            explicit = bool(inv_no and inv_no in reference_text)
            amount_match = abs(remaining[idx] - amount) <= epsilon
            score = (35 if explicit else 0) + (30 if tax_match else 0) + (20 if amount_match else 0) + int(name_score * 10)
            if tx_date and inv_date_col:
                inv_date = _date_text(inv.get(inv_date_col))
                if inv_date:
                    try:
                        gap = abs((pd.Timestamp(tx_date) - pd.Timestamp(inv_date)).days)
                        score += 5 if gap <= 60 else 0
                    except Exception:
                        pass
            candidates.append((idx, score, explicit, amount_match))

        chosen: list[int] = []
        reason = "Chưa đủ căn cứ định danh đối tác hoặc hóa đơn"
        score = 0
        explicit_hits = [c for c in candidates if c[2]]
        exact_hits = [c for c in candidates if c[3]]
        if len(explicit_hits) == 1:
            chosen = [explicit_hits[0][0]]
            score, reason = explicit_hits[0][1], "Số hóa đơn/mã tham chiếu xuất hiện trong nội dung"
        elif len(explicit_hits) > 1:
            explicit_ids = [c[0] for c in explicit_hits]
            explicit_total = sum(remaining[idx] for idx in explicit_ids)
            if abs(explicit_total - amount) <= epsilon:
                chosen = explicit_ids
                score = 90
                reason = "Nội dung chuyển khoản liệt kê nhiều hóa đơn và tổng tiền khớp"
            else:
                reason = "Nội dung chứa nhiều số hóa đơn nhưng tổng tiền không khớp; cần kế toán xác nhận"
        elif len(exact_hits) == 1:
            chosen = [exact_hits[0][0]]
            score, reason = exact_hits[0][1], "Đối tác định danh được và số tiền khớp duy nhất"
        elif len(exact_hits) > 1:
            reason = "Nhiều hóa đơn cùng đối tác có số tiền trùng nhau"
        elif len(candidates) > 1:
            cand_ids = [c[0] for c in candidates]
            try:
                from core import subset_sum_indices
                subset = subset_sum_indices([remaining[i] for i in cand_ids], amount, epsilon)
            except Exception:
                subset = None
            if subset:
                chosen = [cand_ids[i] for i in subset]
                score = 85
                reason = "Một giao dịch thanh toán gộp cho nhiều hóa đơn cùng đối tác"
        elif len(candidates) == 1 and amount <= remaining[candidates[0][0]] + epsilon:
            chosen = [candidates[0][0]]
            score = candidates[0][1]
            reason = "Một đối tác và một hóa đơn phù hợp; cho phép ghi nhận thanh toán một phần"

        allocated = 0.0
        tx_id = str(tx.get("Mã giao dịch") or tx.get("Tệp nguồn") or f"TX-{tx_idx + 1}")
        for idx in chosen:
            take = min(max(amount - allocated, 0), remaining[idx])
            if take <= 0:
                continue
            remaining[idx] -= take
            paid[idx] += take
            allocated += take
            allocated_refs[idx].append(tx_id)
            mapping_output.append({
                "Mã giao dịch": tx_id, "Số hóa đơn": inv_rows[idx].get(invoice_number_col),
                "Đối tác": (inv_rows[idx].get(inv_name_col) if inv_name_col else ""),
                "Số tiền phân bổ": round(take, 2), "Điểm mapping": score,
                "Căn cứ": reason, "Trạng thái": "Gợi ý – cần xác nhận"
            })
        status = "Đã phân bổ" if allocated and abs(amount - allocated) <= epsilon else (
            "Phân bổ một phần" if allocated else "Chưa ghép")
        tx_output.append({
            "Mã giao dịch": tx_id, "Ngày giao dịch": tx_date, "Số tiền": amount,
            "Tên đối tác": tx_name, "MST đối tác": tx_tax,
            "Nội dung": desc, "Số tiền đã phân bổ": round(allocated, 2),
            "Còn chưa phân bổ": round(max(0, amount - allocated), 2),
            "Trạng thái": status, "Căn cứ": reason
        })

    invoice_output = []
    for idx, inv in enumerate(inv_rows):
        total = parse_amount(inv.get(inv_amount_col))
        balance = max(0.0, remaining[idx])
        invoice_output.append({
            "Số hóa đơn": inv.get(invoice_number_col),
            "Ngày hóa đơn": _date_text(inv.get(inv_date_col)) if inv_date_col else None,
            "MST người bán": normalize_tax_code(inv.get(inv_tax_col)) if inv_tax_col else "",
            "Tên người bán": inv.get(inv_name_col) if inv_name_col else "",
            "Tổng thanh toán": total, "Đã thanh toán": round(paid[idx], 2),
            "Còn phải trả": round(balance, 2),
            "Trạng thái": "Đủ thanh toán" if balance <= epsilon else ("Thanh toán một phần" if paid[idx] > epsilon else "Chưa tìm thấy thanh toán"),
            "Mã giao dịch ghép": ", ".join(allocated_refs[idx])
        })
    return pd.DataFrame(invoice_output), pd.DataFrame(mapping_output), pd.DataFrame(tx_output)
