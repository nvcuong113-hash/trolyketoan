from __future__ import annotations

import io
import re
import uuid
import zipfile
import xml.etree.ElementTree as ET
from datetime import date
from difflib import SequenceMatcher
from pathlib import Path
from typing import Iterable

import pandas as pd

EPSILON_VND = 1_000
MAX_COMBINATION_SIZE = 12
MAX_SUBSET_CANDIDATES = 80


def normalize_text(value: object) -> str:
    if value is None or pd.isna(value):
        return ""
    value = str(value).casefold().strip()
    value = value.replace("đ", "d")
    value = re.sub(r"[\W_]+", " ", value, flags=re.UNICODE)
    return re.sub(r"\s+", " ", value).strip()


def similarity(left: object, right: object) -> float:
    a, b = normalize_text(left), normalize_text(right)
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()


def parse_money(value: object) -> float:
    if value is None or pd.isna(value):
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    raw = str(value).strip().replace("₫", "").replace("VND", "").replace(" ", "")
    if not raw or raw in {"-", "—"}:
        return 0.0
    # Resolve decimal/thousands separators without interpreting 1.234 as 1.234 VND.
    if "," in raw and "." in raw:
        if raw.rfind(",") > raw.rfind("."):
            raw = raw.replace(".", "").replace(",", ".")
        else:
            raw = raw.replace(",", "")
    elif "," in raw:
        tail = raw.rsplit(",", 1)[-1]
        raw = raw.replace(",", ".") if len(tail) in (1, 2) else raw.replace(",", "")
    elif raw.count(".") > 1:
        raw = raw.replace(".", "")
    elif "." in raw and len(raw.rsplit(".", 1)[-1]) == 3:
        raw = raw.replace(".", "")
    try:
        return float(raw)
    except ValueError:
        return 0.0


def find_column(columns: Iterable[object], aliases: Iterable[str], required: bool = False) -> str | None:
    cols = list(columns)
    norm = {normalize_text(c): str(c) for c in cols}
    for alias in aliases:
        if normalize_text(alias) in norm:
            return norm[normalize_text(alias)]
    for alias in aliases:
        a = normalize_text(alias)
        if a:
            matches = [str(c) for c in cols if a in normalize_text(c)]
            if matches:
                return matches[0]
    if required:
        raise ValueError(f"Thiếu cột bắt buộc. Cần một trong: {', '.join(aliases)}")
    return None


def read_tabular_bytes(filename: str, content: bytes, aliases: Iterable[str] = ()) -> pd.DataFrame:
    suffix = Path(filename).suffix.lower()
    if suffix == ".csv":
        errors = []
        for encoding in ("utf-8-sig", "cp1258", "latin1"):
            try:
                return pd.read_csv(io.BytesIO(content), dtype=object, encoding=encoding, sep=None, engine="python")
            except Exception as exc:
                errors.append(str(exc))
        raise ValueError("Không đọc được CSV: " + "; ".join(errors[-2:]))
    if suffix not in {".xlsx", ".xls"}:
        raise ValueError("Định dạng không hỗ trợ. Hãy dùng XLSX, XLS hoặc CSV.")
    book = pd.ExcelFile(io.BytesIO(content))
    best_frame, best_score = None, -1
    aliases_norm = [normalize_text(a) for a in aliases]
    for sheet in book.sheet_names:
        raw = pd.read_excel(book, sheet_name=sheet, header=None, dtype=object)
        if raw.empty:
            continue
        header_idx, score_best = 0, -1
        for idx, row in raw.head(40).iterrows():
            cells = [normalize_text(x) for x in row.tolist()]
            score = sum(1 for a in aliases_norm if a and any(a == c or a in c for c in cells))
            if score > score_best:
                header_idx, score_best = idx, score
        if score_best > best_score:
            best_frame = (raw, header_idx)
            best_score = score_best
    if best_frame is None:
        raise ValueError("Tệp Excel không có dữ liệu.")
    raw, header_idx = best_frame
    headers = []
    for idx, value in enumerate(raw.iloc[header_idx].tolist()):
        label = str(value).strip() if pd.notna(value) else ""
        headers.append(label or f"Column_{idx + 1}")
    # Make duplicate column labels unique.
    seen, unique = {}, []
    for label in headers:
        seen[label] = seen.get(label, 0) + 1
        unique.append(label if seen[label] == 1 else f"{label}_{seen[label]}")
    frame = raw.iloc[header_idx + 1:].copy()
    frame.columns = unique
    return frame.dropna(how="all").reset_index(drop=True)


def excel_bytes(sheets: dict[str, pd.DataFrame]) -> bytes:
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        for name, frame in sheets.items():
            safe = frame.copy()
            for col in safe.columns:
                if safe[col].map(lambda x: isinstance(x, (list, dict, set))).any():
                    safe[col] = safe[col].map(lambda x: ", ".join(map(str, x)) if isinstance(x, (list, tuple, set)) else str(x) if isinstance(x, dict) else x)
            safe.to_excel(writer, index=False, sheet_name=name[:31])
    return output.getvalue()


def mask_sensitive(text: object) -> str:
    value = str(text or "")
    # Labeled identifiers take precedence because a 10-digit phone and tax code
    # can share the same shape. Unlabeled 0xxxxxxxxx values are treated as phones.
    value = re.sub(r"(?i)(\bMST\s*[:#-]?\s*)\d{10}(?:\d{3})?\b", r"\1[MST]", value)
    value = re.sub(r"(?i)(\b(?:SDT|điện thoại|phone)\s*[:#-]?\s*)0\d{9}\b", r"\1[SDT]", value)
    value = re.sub(r"(?<!\d)0\d{9}(?!\d)", "[SDT]", value)
    value = re.sub(r"(?<!\d)\d{8,16}(?!\d)", "[STK_BANK]", value)
    return value

def subset_sum_indices(amounts: Iterable[object], target: object, epsilon: int = EPSILON_VND,
                       max_items: int = MAX_COMBINATION_SIZE) -> list[int] | None:
    target_i = int(round(parse_money(target)))
    values = [(i, int(round(parse_money(v)))) for i, v in enumerate(amounts) if parse_money(v) > 0]
    values.sort(key=lambda item: item[1], reverse=True)
    if not values or len(values) > MAX_SUBSET_CANDIDATES or target_i <= 0:
        return None
    # Dynamic programming keeps the shortest combination for each reachable sum.
    # Bound the search range to avoid unbounded memory on malformed input.
    states: dict[int, tuple[int, ...]] = {0: ()}
    ceiling = target_i + epsilon
    for original_idx, amount in values:
        updates = {}
        for subtotal, chosen in list(states.items()):
            new_total = subtotal + amount
            if new_total > ceiling or len(chosen) >= max_items:
                continue
            candidate = chosen + (original_idx,)
            old = states.get(new_total) or updates.get(new_total)
            if old is None or len(candidate) < len(old):
                updates[new_total] = candidate
        for total, chosen in updates.items():
            if total not in states or len(chosen) < len(states[total]):
                states[total] = chosen
        # Hard memory ceiling: return "unmatched" rather than risking worker exhaustion.
        if len(states) > 200_000:
            return None
        matches = [(total, chosen) for total, chosen in states.items() if chosen and abs(total-target_i) <= epsilon]
        if matches:
            return list(min(matches, key=lambda item: (len(item[1]), abs(item[0]-target_i)))[1])
    return None


def _customer_candidates(invoices: pd.DataFrame, description: str, threshold: float) -> list[int]:
    desc_norm = normalize_text(description)
    selected = []
    for idx, row in invoices.iterrows():
        name = str(row.get("_customer_name", ""))
        code = str(row.get("_customer_code", ""))
        tax = str(row.get("_tax_code", ""))
        exact = any(token and normalize_text(token) in desc_norm for token in (name, code, tax))
        score = similarity(name, description)
        if exact or score >= threshold:
            selected.append(idx)
    return selected


def reconcile_bank(bank: pd.DataFrame, invoices: pd.DataFrame, threshold: float = 0.80,
                   epsilon: int = EPSILON_VND) -> tuple[pd.DataFrame, pd.DataFrame]:
    b, inv = bank.copy(), invoices.copy()
    date_col = find_column(b.columns, ["Ngày giao dịch", "Ngày hạch toán", "Transaction date", "Date"])
    credit_col = find_column(b.columns, ["Số tiền ghi Có", "Ghi Có", "Tiền vào", "Credit", "Amount in"], required=True)
    debit_col = find_column(b.columns, ["Số tiền ghi Nợ", "Ghi Nợ", "Tiền ra", "Debit", "Amount out"])
    desc_col = find_column(b.columns, ["Nội dung giao dịch", "Diễn giải", "Description", "Transaction details"], required=True)
    amount_col = find_column(inv.columns, ["Số tiền phải thu", "Còn phải thu", "Số tiền còn lại", "Outstanding", "Amount"], required=True)
    invoice_col = find_column(inv.columns, ["Số hóa đơn", "Mã đơn hàng", "Invoice number", "Mã chứng từ"])
    name_col = find_column(inv.columns, ["Tên khách hàng", "Khách hàng", "Customer name"])
    code_col = find_column(inv.columns, ["Mã khách hàng", "Customer code"])
    tax_col = find_column(inv.columns, ["Mã số thuế", "MST", "Tax code"])
    b["_date"] = pd.to_datetime(b[date_col], errors="coerce", dayfirst=True) if date_col else pd.NaT
    b["_credit"] = b[credit_col].map(parse_money)
    b["_debit"] = b[debit_col].map(parse_money) if debit_col else 0.0
    b["_description"] = b[desc_col].fillna("").astype(str)
    inv["_amount"] = inv[amount_col].map(parse_money)
    inv["_invoice"] = inv[invoice_col].fillna("").astype(str) if invoice_col else ""
    inv["_customer_name"] = inv[name_col].fillna("").astype(str) if name_col else ""
    inv["_customer_code"] = inv[code_col].fillna("").astype(str) if code_col else ""
    inv["_tax_code"] = inv[tax_col].fillna("").astype(str) if tax_col else ""
    inv["_matched"] = False
    records = []
    for source_idx, row in b.iterrows():
        amount = int(round(row["_credit"]))
        if amount <= 0:
            continue
        desc = row["_description"]
        eligible = inv.loc[(~inv["_matched"]) & (inv["_amount"] > 0)].copy()
        # Exact invoice number + exact amount gets first priority.
        exact_hits = eligible[
            eligible.apply(lambda r: bool(r["_invoice"].strip() and normalize_text(r["_invoice"]) in normalize_text(desc))
                           and abs(r["_amount"] - amount) <= epsilon, axis=1)
        ]
        if len(exact_hits):
            chosen = [exact_hits.index[0]]
            match_type, reason = "Khớp 1-1", "Mã hóa đơn trong nội dung + số tiền"
        else:
            customer_idxs = _customer_candidates(eligible, desc, threshold)
            exact_amount = [i for i in customer_idxs if abs(inv.at[i, "_amount"] - amount) <= epsilon]
            if exact_amount:
                chosen = [exact_amount[0]]
                match_type, reason = "Khớp 1-1", "Đối tác nhận diện được + số tiền"
            else:
                pool = customer_idxs
                # If description doesn't identify the customer, do not combine invoices across customers.
                subset = subset_sum_indices(inv.loc[pool, "_amount"].tolist(), amount, epsilon) if pool else None
                if subset:
                    chosen = [pool[i] for i in subset]
                    match_type, reason = "Khớp trả gộp (1-N)", "Tổ hợp hóa đơn cùng đối tác; sai số cho phép"
                else:
                    chosen, match_type, reason = [], "Chưa xác định", "Chưa đủ căn cứ ghép tự động"
        for idx in chosen:
            inv.at[idx, "_matched"] = True
        records.append({
            "Mã giao dịch": str(uuid.uuid4())[:8], "Dòng sao kê": source_idx + 1,
            "Ngày": row["_date"], "Số tiền": amount, "Nội dung": desc,
            "Kết quả": match_type,
            "Hóa đơn": ", ".join(inv.loc[chosen, "_invoice"].astype(str).tolist()),
            "Khách hàng": ", ".join(sorted(set(inv.loc[chosen, "_customer_name"].astype(str).tolist()))),
            "Số hóa đơn ghép": len(chosen), "Căn cứ": reason
        })
    columns = ["Mã giao dịch", "Dòng sao kê", "Ngày", "Số tiền", "Nội dung", "Kết quả", "Hóa đơn", "Khách hàng", "Số hóa đơn ghép", "Căn cứ"]
    result = pd.DataFrame(records, columns=columns)
    unmatched = inv.loc[~inv["_matched"]].drop(columns=["_amount", "_invoice", "_customer_name", "_customer_code", "_tax_code", "_matched"], errors="ignore")
    return result, unmatched


def _flatten_xml(root: ET.Element) -> dict[str, list[str]]:
    values: dict[str, list[str]] = {}
    for elem in root.iter():
        tag = elem.tag.split("}")[-1].split(":")[-1]
        if elem.text and elem.text.strip():
            values.setdefault(tag, []).append(elem.text.strip())
    return values


def parse_invoice_xml(xml_bytes: bytes, company_tax: str = "", company_name: str = "") -> dict:
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as exc:
        raise ValueError(f"XML không hợp lệ: {exc}") from exc
    flat = _flatten_xml(root)
    def first(*tags: str) -> str:
        for tag in tags:
            vals = flat.get(tag)
            if vals:
                return vals[0]
        return ""
    # Common e-invoice XML schemas use repeated Ten/MST fields. Prefer tags with
    # explicit buyer/seller names, then use positional fallback only as a hint.
    buyer_tax = first("MSTNMua", "MSTNguoiMua", "MST_Mua", "BuyerTaxCode")
    seller_tax = first("MSTNBan", "MSTNguoiBan", "MST_Ban", "SellerTaxCode")
    buyer_name = first("TenNMua", "TenNguoiMua", "BuyerName")
    seller_name = first("TenNBan", "TenNguoiBan", "SellerName")
    if not buyer_tax and "MST" in flat and len(flat["MST"]) >= 2:
        seller_tax, buyer_tax = flat["MST"][0], flat["MST"][1]
    if not buyer_name and "Ten" in flat and len(flat["Ten"]) >= 2:
        seller_name, buyer_name = flat["Ten"][0], flat["Ten"][1]
    signature = any(k.lower().startswith(("dsign", "signature", "sig")) for k in flat)
    # DSign is often an XML element whose content can be empty; check element presence too.
    signature = signature or any(e.tag.split("}")[-1].lower().startswith(("dsign", "signature")) for e in root.iter())
    errors = []
    expected_tax = re.sub(r"\D", "", company_tax or "")
    actual_tax = re.sub(r"\D", "", buyer_tax or "")
    if expected_tax and actual_tax and expected_tax != actual_tax:
        errors.append("Mã số thuế người mua không khớp")
    if expected_tax and not actual_tax:
        errors.append("Không tìm thấy mã số thuế người mua")
    if company_name and buyer_name and similarity(company_name, buyer_name) < 0.80:
        errors.append("Tên người mua không khớp")
    if company_name and not buyer_name:
        errors.append("Không tìm thấy tên người mua")
    if not signature:
        errors.append("Không phát hiện cấu trúc chữ ký số trong XML")
    return {
        "Tên người bán": seller_name, "MST người bán": re.sub(r"\D", "", seller_tax),
        "Tên người mua": buyer_name, "MST người mua": actual_tax,
        "Số hóa đơn": first("SHDon", "InvoiceNo", "InvoiceNumber"),
        "Ngày lập": first("NLap", "InvoiceDate", "IssueDate"),
        "Tiền trước thuế": parse_money(first("TgTCThue", "TotalTaxableAmount")),
        "Tiền thuế": parse_money(first("TgTThue", "TgTThue", "TotalTaxAmount")),
        "Tổng thanh toán": parse_money(first("TgTTTBSo", "Tổng tiền thanh toán", "TotalAmount")),
        "Có cấu trúc chữ ký": signature,
        "Trạng thái kiểm tra": "Cần kiểm tra" if errors else "Không phát hiện lỗi cơ bản",
        "Cảnh báo": "; ".join(errors)
    }


def parse_invoice_upload(filename: str, content: bytes, company_tax: str = "", company_name: str = "") -> pd.DataFrame:
    rows = []
    if Path(filename).suffix.lower() == ".zip":
        try:
            archive = zipfile.ZipFile(io.BytesIO(content))
        except zipfile.BadZipFile as exc:
            raise ValueError("Tệp ZIP bị lỗi hoặc không hợp lệ.") from exc
        with archive:
            for name in archive.namelist():
                if name.lower().endswith(".xml") and not name.startswith("__MACOSX/"):
                    try:
                        row = parse_invoice_xml(archive.read(name), company_tax, company_name)
                        row["Tên tệp"] = Path(name).name
                    except Exception as exc:
                        row = {"Tên tệp": Path(name).name, "Trạng thái kiểm tra": "Lỗi đọc XML", "Cảnh báo": str(exc)}
                    rows.append(row)
    else:
        row = parse_invoice_xml(content, company_tax, company_name)
        row["Tên tệp"] = Path(filename).name
        rows.append(row)
    return pd.DataFrame(rows)


def classify_invoice_payments(invoices: pd.DataFrame, payments: pd.DataFrame, epsilon: int = EPSILON_VND) -> pd.DataFrame:
    result = invoices.copy()
    amount_col = find_column(payments.columns, ["Số tiền", "Số tiền ghi Nợ", "Tiền ra", "Amount"], required=True)
    desc_col = find_column(payments.columns, ["Nội dung", "Diễn giải", "Nhà cung cấp", "Description"])
    p_amount = payments[amount_col].map(parse_money)
    p_desc = payments[desc_col].fillna("").astype(str) if desc_col else pd.Series("", index=payments.index)
    result["Trạng thái thanh toán"] = "Hóa đơn treo"
    for idx, invoice in result.iterrows():
        amount = parse_money(invoice.get("Tổng thanh toán", 0))
        supplier = invoice.get("Tên người bán", "")
        hits = (p_amount - amount).abs() <= epsilon
        if supplier and desc_col:
            hits &= p_desc.map(lambda x: similarity(supplier, x) >= 0.80 or normalize_text(supplier) in normalize_text(x))
        if hits.any():
            result.at[idx, "Trạng thái thanh toán"] = "Đã tìm thấy khoản chi tương ứng"
    return result


def suggest_account(description: str, direction: str, cash_account: str = "1121") -> tuple[str, str, str]:
    text = normalize_text(description)
    if direction == "in":
        return cash_account, "131", "Thu tiền khách hàng – cần xác nhận đối tượng"
    rules = [
        (("dien", "nuoc", "internet", "van phong pham", "van chuyen", "ship", "thue van phong"), "642", "Chi phí vận hành"),
        (("nha cung cap", "thanh toan ncc", "mua hang", "tien hang"), "331", "Thanh toán nhà cung cấp"),
        (("tam ung", "ung truoc", "hoan ung"), "141", "Tạm ứng/hoàn ứng nhân viên"),
        (("luong", "tien cong", "salary"), "334", "Thanh toán lương"),
        (("thue gtgt", "nop thue"), "333", "Nộp thuế – cần xác định sắc thuế"),
    ]
    for keywords, debit, note in rules:
        if any(word in text for word in keywords):
            return debit, cash_account, note
    return "", "", "Chưa đủ thông tin để gợi ý định khoản"


def build_staging(transactions: pd.DataFrame, standard: str = "133") -> pd.DataFrame:
    date_col = find_column(transactions.columns, ["Ngày giao dịch", "Ngày", "Ngày hạch toán", "Date"])
    in_col = find_column(transactions.columns, ["Tiền vào", "Số tiền ghi Có", "Ghi Có", "amount_in", "Credit"])
    out_col = find_column(transactions.columns, ["Tiền ra", "Số tiền ghi Nợ", "Ghi Nợ", "amount_out", "Debit"])
    desc_col = find_column(transactions.columns, ["Nội dung", "Diễn giải", "Description"])
    rows = []
    for _, row in transactions.iterrows():
        incoming = parse_money(row[in_col]) if in_col else 0
        outgoing = parse_money(row[out_col]) if out_col else 0
        desc = str(row[desc_col]) if desc_col else ""
        debit, credit, note = suggest_account(desc, "in" if incoming > 0 else "out")
        rows.append({
            "trans_id": str(uuid.uuid4()), "Ngày": pd.to_datetime(row[date_col], errors="coerce", dayfirst=True) if date_col else pd.NaT,
            "Đối tác": "", "Diễn giải gốc": desc, "Diễn giải chuẩn": note,
            "Tiền thu": incoming, "Tiền chi": outgoing, "Tài khoản Nợ": debit, "Tài khoản Có": credit,
            "Mã hóa đơn": "", "Chưa đủ chứng từ": False, "Chế độ": f"TT {standard}"
        })
    return pd.DataFrame(rows)


def aging_report(invoices: pd.DataFrame, today: date | None = None) -> pd.DataFrame:
    today = today or date.today()
    df = invoices.copy()
    due_col = find_column(df.columns, ["Ngày đến hạn", "Hạn thanh toán", "Due date"], required=True)
    amount_col = find_column(df.columns, ["Số tiền phải thu", "Còn phải thu", "Số tiền", "Amount"])
    name_col = find_column(df.columns, ["Tên khách hàng", "Tên nhà cung cấp", "Đối tác", "Khách hàng"])
    invoice_col = find_column(df.columns, ["Số hóa đơn", "Mã đơn hàng", "Số chứng từ"])
    df["Ngày đến hạn chuẩn"] = pd.to_datetime(df[due_col], errors="coerce", dayfirst=True)
    df["Số tiền"] = df[amount_col].map(parse_money) if amount_col else 0.0
    df["Đối tác"] = df[name_col].fillna("").astype(str) if name_col else ""
    df["Số hóa đơn"] = df[invoice_col].fillna("").astype(str) if invoice_col else ""
    df["Số ngày quá hạn"] = (pd.Timestamp(today) - df["Ngày đến hạn chuẩn"]).dt.days
    def priority(days):
        if pd.isna(days): return "Thiếu ngày đến hạn"
        if -3 <= days <= 0: return "Sắp đến hạn"
        if 1 <= days <= 15: return "Quá hạn nhẹ"
        if days > 15: return "Quá hạn nghiêm trọng"
        return "Chưa đến hạn"
    df["Mức độ"] = df["Số ngày quá hạn"].map(priority)
    df["Tin nhắn nhắc công nợ"] = df.apply(lambda r: f"Kính gửi {r['Đối tác']}, bên em xin phép nhắc khoản công nợ {r['Số tiền']:,.0f} VNĐ liên quan hóa đơn/đơn hàng {r['Số hóa đơn']}. Anh/chị vui lòng kiểm tra và hỗ trợ thanh toán theo thỏa thuận. Cảm ơn anh/chị.", axis=1)
    df["Tin nhắn yêu cầu hóa đơn"] = df.apply(lambda r: f"Chào {r['Đối tác']}, nhờ anh/chị gửi bổ sung hóa đơn/chứng từ cho khoản thanh toán {r['Số tiền']:,.0f} VNĐ liên quan {r['Số hóa đơn']}. Cảm ơn anh/chị.", axis=1)
    df["Tin nhắn nhắc hoàn ứng"] = df.apply(lambda r: f"Chào bạn, vui lòng bổ sung chứng từ/hoàn ứng khoản {r['Số tiền']:,.0f} VNĐ liên quan {r['Số hóa đơn']} để kế toán hoàn tất đối chiếu.", axis=1)
    return df


def pre_audit(journal: pd.DataFrame, invoice_register: pd.DataFrame | None = None,
              opening_cash: float = 0.0) -> pd.DataFrame:
    date_col = find_column(journal.columns, ["Ngày hạch toán", "Ngày chứng từ", "Ngày", "Date"], required=True)
    debit_col = find_column(journal.columns, ["Tài khoản Nợ", "TK Nợ", "Debit account"], required=True)
    credit_col = find_column(journal.columns, ["Tài khoản Có", "TK Có", "Credit account"], required=True)
    amount_col = find_column(journal.columns, ["Số tiền", "Phát sinh Nợ", "Số phát sinh", "Amount"], required=True)
    desc_col = find_column(journal.columns, ["Diễn giải", "Nội dung", "Description"])
    work = journal.copy()
    work["_date"] = pd.to_datetime(work[date_col], errors="coerce", dayfirst=True)
    work["_debit"] = work[debit_col].fillna("").astype(str).str.strip()
    work["_credit"] = work[credit_col].fillna("").astype(str).str.strip()
    work["_amount"] = work[amount_col].map(parse_money)
    work["_desc"] = work[desc_col].fillna("").astype(str) if desc_col else ""
    findings = []
    # Cash account movement: debit 1111 increases cash; credit 1111 decreases cash.
    work = work.sort_values(["_date"], kind="stable")
    cash_rows = work[(work["_debit"].str.startswith("1111")) | (work["_credit"].str.startswith("1111"))]
    balance = float(opening_cash)
    for idx, row in cash_rows.iterrows():
        balance += row["_amount"] if row["_debit"].startswith("1111") else -row["_amount"]
        if balance < 0:
            findings.append({"Nhóm kiểm tra": "Âm quỹ tiền mặt", "Ngày": row["_date"], "Dòng dữ liệu": int(idx)+2,
                             "Giá trị": balance, "Cảnh báo": "Số dư lũy kế TK 1111 âm tại thời điểm giao dịch"})
    for idx, row in work.iterrows():
        if not row["_debit"] or not row["_credit"]:
            findings.append({"Nhóm kiểm tra": "Thiếu tài khoản đối ứng", "Ngày": row["_date"], "Dòng dữ liệu": int(idx)+2,
                             "Giá trị": row["_amount"], "Cảnh báo": "Thiếu tài khoản Nợ hoặc Có"})
        if (row["_debit"].startswith("1111") or row["_credit"].startswith("1111")) and row["_amount"] >= 20_000_000 and any(
            word in normalize_text(row["_desc"]) for word in ("hoa don", "mua hang", "dich vu")
        ):
            findings.append({"Nhóm kiểm tra": "Rà soát thanh toán không dùng tiền mặt", "Ngày": row["_date"], "Dòng dữ liệu": int(idx)+2,
                             "Giá trị": row["_amount"], "Cảnh báo": "Giao dịch từ 20 triệu đồng hạch toán tiền mặt; cần kiểm tra quy định hiện hành và điều kiện áp dụng"})
    if invoice_register is not None and not invoice_register.empty:
        vat_col = find_column(invoice_register.columns, ["Tiền thuế", "Thuế GTGT", "VAT", "TgTThue"])
        if vat_col:
            journal_vat = work.loc[work["_debit"].str.startswith("1331"), "_amount"].sum()
            invoice_vat = invoice_register[vat_col].map(parse_money).sum()
            if abs(journal_vat-invoice_vat) > 1:
                findings.append({"Nhóm kiểm tra": "Lệch thuế GTGT", "Ngày": "", "Dòng dữ liệu": "",
                                 "Giá trị": journal_vat-invoice_vat,
                                 "Cảnh báo": f"Phát sinh Nợ 1331 ({journal_vat:,.0f}) khác bảng kê ({invoice_vat:,.0f})"})
    return pd.DataFrame(findings, columns=["Nhóm kiểm tra", "Ngày", "Dòng dữ liệu", "Giá trị", "Cảnh báo"])


def cashflow_summary(transactions: pd.DataFrame, opening_balance: float = 0.0) -> dict:
    in_col = find_column(transactions.columns, ["Số tiền ghi Có", "Tiền vào", "Tiền thu", "Thu", "Credit", "amount_in"])
    out_col = find_column(transactions.columns, ["Số tiền ghi Nợ", "Tiền ra", "Tiền chi", "Chi", "Debit", "amount_out"])
    if not in_col and not out_col:
        raise ValueError("Không tìm thấy cột tiền thu/tiền chi trong dữ liệu.")
    total_in = transactions[in_col].map(parse_money).sum() if in_col else 0.0
    total_out = transactions[out_col].map(parse_money).sum() if out_col else 0.0
    return {"Tồn đầu kỳ": float(opening_balance), "Tổng thu": float(total_in), "Tổng chi": float(total_out),
            "Dòng tiền thuần": float(total_in-total_out), "Tồn cuối kỳ": float(opening_balance+total_in-total_out)}
