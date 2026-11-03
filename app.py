from __future__ import annotations

import gc
import io
import re
import uuid
import zipfile
from datetime import date, datetime
from difflib import SequenceMatcher
from itertools import combinations
from pathlib import Path
import xml.etree.ElementTree as ET

import pandas as pd
import streamlit as st

APP_NAME = "Trợ lý Kế toán SME"
SESSION_TTL_SECONDS = 600
MAX_COMBINATION_SIZE = 12
EPSILON_VND = 1000

st.set_page_config(page_title=APP_NAME, page_icon="🧾", layout="wide")

# ----------------------------- Shared utilities -----------------------------
def money(value):
    if pd.isna(value):
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    raw = str(value).strip().replace("₫", "").replace("VND", "").replace(" ", "")
    if not raw:
        return 0.0
    # Vietnamese exports commonly use 1.234.567 or 1,234,567.
    if "," in raw and "." in raw:
        raw = raw.replace(".", "").replace(",", ".") if raw.rfind(",") > raw.rfind(".") else raw.replace(",", "")
    elif raw.count(".") > 1 or raw.count(",") > 1:
        raw = raw.replace(".", "").replace(",", "")
    elif "," in raw:
        tail = raw.rsplit(",", 1)[-1]
        raw = raw.replace(",", ".") if len(tail) <= 2 else raw.replace(",", "")
    try:
        return float(raw)
    except ValueError:
        return 0.0

def norm_text(value):
    return re.sub(r"\s+", " ", str(value or "")).strip().casefold()

def similarity(a, b):
    return SequenceMatcher(None, norm_text(a), norm_text(b)).ratio()

def find_column(columns, aliases):
    normalized = {norm_text(c): c for c in columns}
    for alias in aliases:
        if norm_text(alias) in normalized:
            return normalized[norm_text(alias)]
    for c in columns:
        if any(norm_text(alias) in norm_text(c) for alias in aliases):
            return c
    return None

def read_tabular(uploaded, aliases=None):
    raw = uploaded.getvalue()
    name = uploaded.name.lower()
    if name.endswith(".csv"):
        try:
            df = pd.read_csv(io.BytesIO(raw), dtype=str, encoding="utf-8-sig")
        except UnicodeDecodeError:
            df = pd.read_csv(io.BytesIO(raw), dtype=str, encoding="cp1258")
        return df
    if not name.endswith((".xlsx", ".xls")):
        raise ValueError("Chỉ hỗ trợ tệp Excel hoặc CSV ở chức năng này.")
    book = pd.ExcelFile(io.BytesIO(raw))
    best, best_score = None, -1
    for sheet in book.sheet_names:
        candidate = pd.read_excel(book, sheet_name=sheet, header=None, dtype=object)
        score = 0
        for _, row in candidate.head(20).iterrows():
            cells = [norm_text(x) for x in row.tolist()]
            row_score = sum(any(norm_text(a) in cell for cell in cells for a in aliases or []) for a in (aliases or []))
            if row_score > score:
                score = row_score
                header_idx = _
        if score > best_score:
            best, best_score = (candidate, header_idx if score else 0), score
    raw_df, header_idx = best
    headers = [str(x).strip() if pd.notna(x) else f"Col_{i}" for i, x in enumerate(raw_df.iloc[header_idx])]
    df = raw_df.iloc[header_idx + 1:].copy()
    df.columns = headers
    return df.dropna(how="all").reset_index(drop=True)

def to_excel_bytes(sheets):
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        for sheet_name, frame in sheets.items():
            (frame if isinstance(frame, pd.DataFrame) else pd.DataFrame(frame)).to_excel(
                writer, index=False, sheet_name=sheet_name[:31]
            )
    output.seek(0)
    return output.getvalue()

def mask_sensitive(text):
    value = str(text or "")
    value = re.sub(r"(?<!\d)\d{8,16}(?!\d)", "[STK_BANK]", value)
    value = re.sub(r"(?<!\d)\d{10}(?:\d{3})?(?!\d)", "[MST]", value)
    value = re.sub(r"(?<!\d)0\d{9}(?!\d)", "[SDT]", value)
    return value

def clear_session_data():
    keep = {"_last_activity", "_authenticated_demo"}
    for key in list(st.session_state.keys()):
        if key not in keep:
            del st.session_state[key]
    gc.collect()

def check_session_ttl():
    now = datetime.now().timestamp()
    last = st.session_state.get("_last_activity", now)
    if now - last > SESSION_TTL_SECONDS:
        clear_session_data()
        st.warning("Phiên đã hết thời gian không hoạt động. Dữ liệu làm việc tạm đã được xóa.")
    st.session_state["_last_activity"] = now

def money_column(df, aliases):
    col = find_column(df.columns, aliases)
    if not col:
        return pd.Series([0.0] * len(df), index=df.index), None
    return df[col].map(money), col

def date_column(df, aliases):
    col = find_column(df.columns, aliases)
    if not col:
        return pd.Series([pd.NaT] * len(df), index=df.index), None
    return pd.to_datetime(df[col], errors="coerce", dayfirst=True), col

def subset_sum_indices(amounts, target, epsilon=EPSILON_VND, max_items=MAX_COMBINATION_SIZE):
    # Bounded backtracking: sorted descending, with suffix pruning.
    vals = [(i, int(round(money(v)))) for i, v in enumerate(amounts) if money(v) > 0]
    vals.sort(key=lambda x: x[1], reverse=True)
    if not vals or len(vals) > 80:
        return None
    suffix = [0] * (len(vals) + 1)
    for i in range(len(vals) - 1, -1, -1):
        suffix[i] = suffix[i + 1] + vals[i][1]
    def dfs(start, chosen, total):
        if chosen and abs(total - target) <= epsilon:
            return [vals[j][0] for j in chosen]
        if len(chosen) >= max_items or start >= len(vals) or total > target + epsilon:
            return None
        if total + suffix[start] < target - epsilon:
            return None
        for j in range(start, len(vals)):
            if total + vals[j][1] > target + epsilon:
                continue
            found = dfs(j + 1, chosen + [j], total + vals[j][1])
            if found is not None:
                return found
        return None
    return dfs(0, [], 0)

# ----------------------------- FR-01 bank matching -----------------------------
def reconcile_bank(bank, invoices, threshold=0.80):
    b = bank.copy()
    inv = invoices.copy()
    b_date, _ = date_column(b, ["Ngày giao dịch", "Ngày hạch toán", "Transaction date", "Date"])
    b_in, _ = money_column(b, ["Số tiền ghi Có", "Ghi Có", "Tiền vào", "Credit", "Amount in"])
    b_out, _ = money_column(b, ["Số tiền ghi Nợ", "Ghi Nợ", "Tiền ra", "Debit", "Amount out"])
    desc_col = find_column(b.columns, ["Nội dung giao dịch", "Diễn giải", "Description", "Transaction details"])
    if not desc_col:
        raise ValueError("Không tìm thấy cột Nội dung giao dịch/Diễn giải trong sao kê.")
    b["Ngày giao dịch chuẩn"] = b_date
    b["Tiền vào"] = b_in
    b["Tiền ra"] = b_out
    b["Nội dung"] = b[desc_col].fillna("").astype(str)
    inv_amount, _ = money_column(inv, ["Số tiền phải thu", "Còn phải thu", "Amount", "Outstanding"])
    inv_no = find_column(inv.columns, ["Số hóa đơn", "Mã đơn hàng", "Invoice number", "Mã chứng từ"])
    inv_name = find_column(inv.columns, ["Tên khách hàng", "Khách hàng", "Customer name"])
    inv_id = find_column(inv.columns, ["Mã khách hàng", "Customer code"])
    inv["Số tiền chuẩn"] = inv_amount
    inv["Số hóa đơn chuẩn"] = inv[inv_no].astype(str) if inv_no else ""
    inv["Tên KH chuẩn"] = inv[inv_name].astype(str) if inv_name else ""
    inv["Mã KH chuẩn"] = inv[inv_id].astype(str) if inv_id else ""
    inv["Đã ghép"] = False
    results = []
    for idx, row in b.iterrows():
        amount = int(round(row["Tiền vào"]))
        if amount <= 0:
            continue
        desc = norm_text(row["Nội dung"])
        candidates = []
        for j, invoice in inv.loc[~inv["Đã ghép"]].iterrows():
            if abs(invoice["Số tiền chuẩn"] - amount) > EPSILON_VND:
                continue
            number_hit = bool(invoice["Số hóa đơn chuẩn"].strip() and norm_text(invoice["Số hóa đơn chuẩn"]) in desc)
            name_score = similarity(invoice["Tên KH chuẩn"], row["Nội dung"]) if invoice["Tên KH chuẩn"] else 0
            if number_hit or name_score >= threshold:
                candidates.append((j, "Số hóa đơn" if number_hit else f"Tên KH {name_score:.0%}"))
        if candidates:
            j, reason = candidates[0]
            inv.at[j, "Đã ghép"] = True
            results.append({"Mã giao dịch": idx + 1, "Ngày": row["Ngày giao dịch chuẩn"], "Số tiền": amount,
                            "Nội dung": row["Nội dung"], "Kết quả": "Khớp 1-1",
                            "Hóa đơn": inv.at[j, "Số hóa đơn chuẩn"], "Khách hàng": inv.at[j, "Tên KH chuẩn"], "Căn cứ": reason})
            continue
        # Try subset sum only against likely customer names found in transfer description.
        likely = []
        for j, invoice in inv.loc[~inv["Đã ghép"]].iterrows():
            score = similarity(invoice["Tên KH chuẩn"], row["Nội dung"]) if invoice["Tên KH chuẩn"] else 0
            if score >= threshold or (invoice["Tên KH chuẩn"] and norm_text(invoice["Tên KH chuẩn"]) in desc):
                likely.append(j)
        if likely:
            subset = subset_sum_indices(inv.loc[likely, "Số tiền chuẩn"].tolist(), amount)
            if subset:
                chosen = [likely[k] for k in subset]
                for j in chosen:
                    inv.at[j, "Đã ghép"] = True
                results.append({"Mã giao dịch": idx + 1, "Ngày": row["Ngày giao dịch chuẩn"], "Số tiền": amount,
                                "Nội dung": row["Nội dung"], "Kết quả": "Khớp trả gộp (1-N)",
                                "Hóa đơn": ", ".join(inv.loc[chosen, "Số hóa đơn chuẩn"].tolist()),
                                "Khách hàng": ", ".join(inv.loc[chosen, "Tên KH chuẩn"].dropna().unique().tolist()),
                                "Căn cứ": f"Tổng {sum(inv.loc[chosen, 'Số tiền chuẩn']):,.0f} VND"})
                continue
        results.append({"Mã giao dịch": idx + 1, "Ngày": row["Ngày giao dịch chuẩn"], "Số tiền": amount,
                        "Nội dung": row["Nội dung"], "Kết quả": "Chưa xác định", "Hóa đơn": "", "Khách hàng": "", "Căn cứ": ""})
    result = pd.DataFrame(results)
    if result.empty:
        result = pd.DataFrame(columns=["Mã giao dịch", "Ngày", "Số tiền", "Nội dung", "Kết quả", "Hóa đơn", "Khách hàng", "Căn cứ"])
    return result, inv.loc[~inv["Đã ghép"]].copy()

# ----------------------------- FR-02 XML invoice checks -----------------------------
def local_tag(tag):
    return tag.split("}")[-1].split(":")[-1]

def parse_invoice_xml(xml_bytes, company_tax="", company_name=""):
    root = ET.fromstring(xml_bytes)
    values = {}
    aliases = {
        "seller_name": ["Ten", "TenNBan", "TenNguoiBan"],
        "buyer_name": ["TenNMua", "TenNguoiMua"],
        "buyer_tax": ["MSTNMua", "MSTNguoiMua"],
        "seller_tax": ["MSTNBan", "MSTNguoiBan"],
        "invoice_no": ["SHDon"],
        "invoice_date": ["NLap"],
        "pre_tax": ["TgTCThue"],
        "vat": ["TgTThue"],
        "total": ["TgTTTBSo"],
        "signature": ["DSign"],
    }
    flat = {}
    for elem in root.iter():
        tag = local_tag(elem.tag)
        if elem.text and elem.text.strip():
            flat.setdefault(tag, elem.text.strip())
        for key, value in elem.attrib.items():
            flat.setdefault(local_tag(key), value)
    for field, tags in aliases.items():
        values[field] = next((flat[t] for t in tags if t in flat), "")
    buyer_tax = re.sub(r"\D", "", values["buyer_tax"])
    expected_tax = re.sub(r"\D", "", company_tax or "")
    errors = []
    if expected_tax and buyer_tax and buyer_tax != expected_tax:
        errors.append("Sai mã số thuế người mua")
    if company_name and values["buyer_name"] and similarity(company_name, values["buyer_name"]) < 0.80:
        errors.append("Tên người mua khác thông tin doanh nghiệp")
    if not values["signature"]:
        errors.append("Không tìm thấy thông tin chữ ký số DSign")
    values["buyer_tax"] = buyer_tax
    values["pre_tax"] = money(values["pre_tax"])
    values["vat"] = money(values["vat"])
    values["total"] = money(values["total"])
    values["status"] = "Cần kiểm tra" if errors else "Hợp lệ theo kiểm tra cơ bản"
    values["warnings"] = "; ".join(errors)
    return values

def parse_xml_upload(uploaded, company_tax, company_name):
    files = []
    raw = uploaded.getvalue()
    if uploaded.name.lower().endswith(".zip"):
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            for name in archive.namelist():
                if name.lower().endswith(".xml") and not name.startswith("__MACOSX"):
                    try:
                        row = parse_invoice_xml(archive.read(name), company_tax, company_name)
                        row["Tên tệp"] = Path(name).name
                        files.append(row)
                    except Exception as exc:
                        files.append({"Tên tệp": Path(name).name, "status": "Lỗi đọc XML", "warnings": str(exc)})
    else:
        row = parse_invoice_xml(raw, company_tax, company_name)
        row["Tên tệp"] = uploaded.name
        files.append(row)
    return pd.DataFrame(files)

# ----------------------------- FR-03 accounting mappings -----------------------------
def suggest_account(description, direction="out", standard="133"):
    text = norm_text(description)
    if direction == "in":
        return "1121", "131", "Thu tiền khách hàng"
    if any(x in text for x in ["điện", "nước", "internet", "văn phòng phẩm", "ship", "vận chuyển", "thuê văn phòng"]):
        return "642", "1121", "Chi phí vận hành (cần kiểm tra VAT)"
    if any(x in text for x in ["nhà cung cấp", "thanh toán ncc", "mua hàng", "tiền hàng"]):
        return "331", "1121", "Thanh toán nhà cung cấp"
    if any(x in text for x in ["tạm ứng", "ứng trước", "hoàn ứng"]):
        return "141", "1121", "Tạm ứng nhân viên"
    if any(x in text for x in ["lương", "tiền công", "salary"]):
        return "334", "1121", "Thanh toán lương"
    return "", "", "Chưa đủ thông tin - kế toán cần chọn tài khoản"

def make_staging(transactions):
    rows = []
    for _, row in transactions.iterrows():
        amount_in = money(row.get("Tiền vào", row.get("amount_in", 0)))
        amount_out = money(row.get("Tiền ra", row.get("amount_out", 0)))
        desc = str(row.get("Nội dung", row.get("Diễn giải", "")))
        debit, credit, note = suggest_account(desc, "in" if amount_in > 0 else "out")
        rows.append({"trans_id": str(uuid.uuid4()), "trans_date": row.get("Ngày", row.get("Ngày giao dịch chuẩn", pd.NaT)),
                     "partner_tax_code": "", "partner_name": row.get("Khách hàng", ""),
                     "raw_description": desc, "std_description": note, "amount_in": amount_in,
                     "amount_out": amount_out, "vat_amount": 0, "matched_invoices": row.get("Hóa đơn", ""),
                     "doc_status": "MISSING_INV" if row.get("Chưa đủ chứng từ", False) else "UNMATCHED",
                     "debit_account": debit, "credit_account": credit,
                     "Chưa đủ chứng từ": bool(row.get("Chưa đủ chứng từ", False))})
    return pd.DataFrame(rows)

# ----------------------------- FR-05 pre-audit checks -----------------------------
def pre_audit(journal, invoice_register=None):
    findings = []
    date_col = find_column(journal.columns, ["Ngày hạch toán", "Ngày chứng từ", "Ngày", "Date"])
    debit_col = find_column(journal.columns, ["Tài khoản Nợ", "TK Nợ", "Debit account"])
    credit_col = find_column(journal.columns, ["Tài khoản Có", "TK Có", "Credit account"])
    amount_col = find_column(journal.columns, ["Số tiền", "Phát sinh Nợ", "Amount"])
    desc_col = find_column(journal.columns, ["Diễn giải", "Nội dung", "Description"])
    if not date_col or not amount_col:
        raise ValueError("Sổ nhật ký chung cần có cột Ngày và Số tiền/Phát sinh.")
    dates = pd.to_datetime(journal[date_col], errors="coerce", dayfirst=True)
    amounts = journal[amount_col].map(money)
    debit = journal[debit_col].astype(str) if debit_col else pd.Series("", index=journal.index)
    credit = journal[credit_col].astype(str) if credit_col else pd.Series("", index=journal.index)
    desc = journal[desc_col].astype(str) if desc_col else pd.Series("", index=journal.index)
    cash = (debit.str.contains(r"^1111", regex=True) * amounts) - (credit.str.contains(r"^1111", regex=True) * amounts)
    running = cash.groupby(dates).sum().sort_index().cumsum()
    for day, balance in running.items():
        if balance < 0:
            findings.append({"Nhóm kiểm tra": "Âm quỹ tiền mặt", "Ngày": day, "Dòng dữ liệu": "", "Giá trị": balance,
                             "Cảnh báo": "Số dư lũy kế 1111 âm theo dữ liệu được cung cấp"})
    if debit_col and credit_col:
        for idx in journal.index:
            if debit.loc[idx].strip() == "" or credit.loc[idx].strip() == "":
                findings.append({"Nhóm kiểm tra": "Thiếu tài khoản đối ứng", "Ngày": dates.loc[idx], "Dòng dữ liệu": idx + 2,
                                 "Giá trị": amounts.loc[idx], "Cảnh báo": "Thiếu tài khoản Nợ hoặc Có"})
            if debit.loc[idx].startswith("1111") and any(x in norm_text(desc.loc[idx]) for x in ["hóa đơn", "mua hàng", "dịch vụ"]):
                findings.append({"Nhóm kiểm tra": "Rà soát thanh toán tiền mặt", "Ngày": dates.loc[idx], "Dòng dữ liệu": idx + 2,
                                 "Giá trị": amounts.loc[idx], "Cảnh báo": "Kiểm tra điều kiện thanh toán không dùng tiền mặt"})
    if invoice_register is not None and not invoice_register.empty:
        vat_col = find_column(invoice_register.columns, ["Tiền thuế", "Thuế GTGT", "VAT", "TgTThue"])
        if vat_col:
            journal_vat = amounts[debit.str.startswith("1331")].sum()
            invoice_vat = invoice_register[vat_col].map(money).sum()
            if abs(journal_vat - invoice_vat) > 1:
                findings.append({"Nhóm kiểm tra": "Lệch thuế GTGT", "Ngày": "", "Dòng dữ liệu": "",
                                 "Giá trị": journal_vat - invoice_vat,
                                 "Cảnh báo": f"TK 1331 trên nhật ký ({journal_vat:,.0f}) khác bảng kê ({invoice_vat:,.0f})"})
    return pd.DataFrame(findings)

# ----------------------------- FR-04 receivables and reminders -----------------------------
def aging_and_messages(invoices, today=None):
    today = today or date.today()
    df = invoices.copy()
    due_col = find_column(df.columns, ["Ngày đến hạn", "Hạn thanh toán", "Due date"])
    amount_col = find_column(df.columns, ["Số tiền phải thu", "Còn phải thu", "Số tiền", "Amount"])
    name_col = find_column(df.columns, ["Tên khách hàng", "Tên nhà cung cấp", "Đối tác", "Khách hàng"])
    number_col = find_column(df.columns, ["Số hóa đơn", "Mã đơn hàng", "Số chứng từ"])
    if not due_col:
        raise ValueError("Cần cột Ngày đến hạn để tính tuổi nợ.")
    df["Ngày đến hạn chuẩn"] = pd.to_datetime(df[due_col], errors="coerce", dayfirst=True)
    df["Số tiền chuẩn"] = df[amount_col].map(money) if amount_col else 0
    df["Đối tác"] = df[name_col].astype(str) if name_col else "Quý khách"
    df["Số hóa đơn chuẩn"] = df[number_col].astype(str) if number_col else ""
    df["Số ngày quá hạn"] = (pd.Timestamp(today) - df["Ngày đến hạn chuẩn"]).dt.days
    def priority(days):
        if pd.isna(days): return "Chưa có hạn"
        if -3 <= days <= 0: return "Sắp đến hạn"
        if days > 15: return "Quá hạn nghiêm trọng"
        if days > 0: return "Quá hạn nhẹ"
        return "Chưa đến hạn"
    df["Mức độ"] = df["Số ngày quá hạn"].map(priority)
    def message(row, kind):
        partner, amount, invno = row["Đối tác"], f"{row['Số tiền chuẩn']:,.0f} VNĐ", row["Số hóa đơn chuẩn"]
        if kind == "customer":
            return f"Kính gửi {partner}, bên em xin phép nhắc khoản công nợ {amount} liên quan hóa đơn/đơn hàng {invno}. Anh/chị vui lòng kiểm tra và hỗ trợ thanh toán theo thỏa thuận. Cảm ơn anh/chị."
        if kind == "supplier":
            return f"Chào {partner}, bên em đã thanh toán {amount} ngày {row['Ngày đến hạn chuẩn']:%d/%m/%Y} (nếu ngày chưa chính xác, vui lòng kiểm tra lại). Nhờ anh/chị gửi bổ sung hóa đơn/chứng từ cho khoản này. Cảm ơn anh/chị."
        return f"Chào bạn, khoản tạm ứng {amount} liên quan {invno} đang cần hoàn ứng/nộp chứng từ. Vui lòng bổ sung hồ sơ để kế toán hoàn tất đối chiếu."
    df["Tin nhắn nhắc khách"] = df.apply(lambda r: message(r, "customer"), axis=1)
    df["Tin nhắn đòi hóa đơn"] = df.apply(lambda r: message(r, "supplier"), axis=1)
    df["Tin nhắn hoàn ứng"] = df.apply(lambda r: message(r, "staff"), axis=1)
    return df

# ----------------------------- UI -----------------------------
check_session_ttl()
st.title("🧾 Trợ lý Kế toán SME")
st.caption("Trợ lý xử lý đối soát, chứng từ và dòng tiền — dữ liệu nghiệp vụ chỉ làm việc trong phiên hiện tại.")
with st.sidebar:
    st.subheader("Không gian làm việc")
    st.info("Chế độ MVP: chưa lưu dữ liệu kế toán lên máy chủ.")
    st.caption("Phiên tự xóa sau 10 phút không hoạt động.")
    if st.button("🧹 Xóa toàn bộ dữ liệu phiên", use_container_width=True):
        clear_session_data()
        st.rerun()

pages = st.tabs(["Tổng quan", "01 · Đối soát ngân hàng", "02 · Kiểm soát hóa đơn", "03 · Xuất dữ liệu", "04 · Tuổi nợ", "05 · Pre-audit", "06 · Báo cáo", "07 · Cố vấn thuế", "08 · Gói dịch vụ"])

with pages[0]:
    st.subheader("Trung tâm nghiệp vụ kế toán")
    c1, c2, c3 = st.columns(3)
    c1.metric("Phân hệ", "08")
    c2.metric("Lưu trữ sổ sách", "Không")
    c3.metric("Tự xóa phiên", "10 phút")
    st.markdown("""
    **Quy trình khuyến nghị**
    1. Tải sao kê ngân hàng và bảng công nợ tại **Đối soát ngân hàng**.
    2. Kiểm tra hóa đơn XML và khoản chi đang nợ chứng từ.
    3. Sinh file quản trị nội bộ và file dữ liệu kế toán để kế toán rà soát.
    4. Chạy kiểm toán nhanh trước kỳ thuế và tạo báo cáo dòng tiền.
    
    > Các kết quả ghép nối và định khoản là gợi ý hỗ trợ. Kế toán cần kiểm tra chứng từ gốc trước khi ghi sổ hoặc kê khai thuế.
    """)
    st.download_button("Tải bộ file Excel mẫu", data=to_excel_bytes({
        "Sao_ke": pd.DataFrame([{"Ngày giao dịch":"01/11/2026","Số tiền ghi Có":1500000,"Số tiền ghi Nợ":0,"Nội dung giao dịch":"KH THANH TOAN HD HD001"}]),
        "Cong_no": pd.DataFrame([{"Mã khách hàng":"KH01","Tên khách hàng":"Công ty ABC","Số hóa đơn":"HD001","Ngày hóa đơn":"01/10/2026","Số tiền phải thu":1500000,"Ngày đến hạn":"15/10/2026"}]),
        "Nhat_ky": pd.DataFrame([{"Ngày hạch toán":"01/11/2026","Tài khoản Nợ":"1121","Tài khoản Có":"131","Số tiền":1500000,"Diễn giải":"Thu tiền khách hàng"}])
    }), file_name="Bo_file_mau_TroLyKeToan.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

with pages[1]:
    st.subheader("Đối soát sao kê ngân hàng & ghép hóa đơn trả gộp")
    bank_file = st.file_uploader("1. Tải sao kê ngân hàng (Excel/CSV)", type=["xlsx","xls","csv"], key="bank")
    invoice_file = st.file_uploader("2. Tải danh sách hóa đơn/công nợ chưa thu", type=["xlsx","xls","csv"], key="ar")
    if st.button("Thực hiện đối soát", type="primary", disabled=not (bank_file and invoice_file)):
        try:
            bank = read_tabular(bank_file, ["Ngày giao dịch","Số tiền ghi Có","Nội dung giao dịch"])
            invoices = read_tabular(invoice_file, ["Tên khách hàng","Số hóa đơn","Số tiền phải thu"])
            result, unmatched = reconcile_bank(bank, invoices)
            st.session_state["reconcile_result"] = result
            st.session_state["unmatched_invoices"] = unmatched
        except Exception as exc:
            st.error(f"Không thể đối soát: {exc}")
    result = st.session_state.get("reconcile_result")
    if result is not None:
        for label in ["Khớp 1-1","Khớp trả gộp (1-N)","Chưa xác định"]:
            subset = result[result["Kết quả"] == label]
            with st.expander(f"{label} · {len(subset)} giao dịch", expanded=(label=="Chưa xác định")):
                st.dataframe(subset, use_container_width=True, hide_index=True)
        st.download_button("Tải kết quả đối soát Excel", to_excel_bytes({"Ket_qua":result,"Hoa_don_chua_ghep":st.session_state["unmatched_invoices"]}),
                           "Ket_qua_doi_soat.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

with pages[2]:
    st.subheader("Kiểm soát hóa đơn điện tử & nợ chứng từ")
    company_tax = st.text_input("Mã số thuế doanh nghiệp")
    company_name = st.text_input("Tên doanh nghiệp theo đăng ký")
    xml_file = st.file_uploader("Tải hóa đơn XML hoặc ZIP chứa XML", type=["xml","zip"], key="xml")
    payment_file = st.file_uploader("Tải nhật ký thanh toán (không bắt buộc)", type=["xlsx","xls","csv"], key="payments")
    if st.button("Kiểm tra hóa đơn", type="primary", disabled=not xml_file):
        try:
            parsed = parse_xml_upload(xml_file, company_tax, company_name)
            st.session_state["parsed_invoices"] = parsed
            if payment_file:
                payments = read_tabular(payment_file, ["Số tiền","Ngày thanh toán","Nội dung"])
                p_amount, pcol = money_column(payments, ["Số tiền","Số tiền ghi Nợ","Amount"])
                p_desc = find_column(payments.columns, ["Nội dung","Diễn giải","Nhà cung cấp"])
                inv_total = pd.to_numeric(parsed.get("total", pd.Series(dtype=float)), errors="coerce")
                inv_names = parsed.get("seller_name", pd.Series([""]*len(parsed))).astype(str)
                parsed["Trạng thái đối chiếu"] = "Hóa đơn treo"
                for i, invrow in parsed.iterrows():
                    hit = (p_amount - invrow.get("total", 0)).abs() <= EPSILON_VND
                    if p_desc and invrow.get("seller_name"):
                        hit = hit & payments[p_desc].astype(str).map(lambda x: similarity(x, invrow.get("seller_name")) >= 0.80)
                    if hit.any():
                        parsed.at[i, "Trạng thái đối chiếu"] = "Đủ chứng từ"
                st.session_state["parsed_invoices"] = parsed
        except Exception as exc:
            st.error(f"Lỗi xử lý hóa đơn: {exc}")
    parsed = st.session_state.get("parsed_invoices")
    if parsed is not None:
        st.dataframe(parsed, use_container_width=True, hide_index=True)
        st.download_button("Tải danh sách kiểm tra hóa đơn", to_excel_bytes({"Hoa_don":parsed}), "Kiem_tra_hoa_don.xlsx",
                           mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    st.caption("Bộ đọc XML kiểm tra các trường có thể nhận diện theo cấu trúc file cung cấp; kết quả không thay thế việc xác minh chữ ký số bằng công cụ chuyên dụng.")

with pages[3]:
    st.subheader("Nhập một lần – xuất nhiều định dạng")
    source_file = st.file_uploader("Tải nhật ký thu-chi đã đối soát hoặc file giao dịch", type=["xlsx","xls","csv"], key="staging")
    standard = st.selectbox("Chế độ hạch toán", ["Thông tư 133","Thông tư 200"])
    if source_file:
        try:
            source = read_tabular(source_file, ["Ngày","Ngày giao dịch","Số tiền","Nội dung","Diễn giải"])
            if st.button("Chuẩn hóa & gợi ý định khoản"):
                staging = make_staging(source)
                st.session_state["staging"] = staging
        except Exception as exc:
            st.error(str(exc))
    staging = st.session_state.get("staging")
    if staging is not None:
        edited = st.data_editor(staging, use_container_width=True, num_rows="dynamic", key="staging_editor")
        st.session_state["staging"] = edited
        st.caption("Đánh dấu 'Chưa đủ chứng từ' để loại dòng đó khỏi file import kế toán.")
        if st.button("Tạo đồng thời 2 file Excel", type="primary"):
            internal = edited.copy()
            importable = edited[~edited["Chưa đủ chứng từ"].astype(bool)].copy()
            st.session_state["exports"] = {
                "internal": to_excel_bytes({"Bao_cao_thu_chi": internal}),
                "import": to_excel_bytes({"Import_ke_toan": importable})
            }
        exports = st.session_state.get("exports")
        if exports:
            c1,c2 = st.columns(2)
            c1.download_button("Tải báo cáo quản trị nội bộ", exports["internal"], "Bao_Cao_Thu_Chi_Noi_Bo.xlsx")
            c2.download_button("Tải file import kế toán", exports["import"], "Import_MISA_VACOM.xlsx")

with pages[4]:
    st.subheader("Quản trị tuổi nợ & tin nhắn nhắc việc")
    aging_file = st.file_uploader("Tải danh sách công nợ có ngày đến hạn", type=["xlsx","xls","csv"], key="aging")
    if aging_file:
        try:
            aging_source = read_tabular(aging_file, ["Ngày đến hạn","Tên khách hàng","Số tiền"])
            aging = aging_and_messages(aging_source)
            st.session_state["aging"] = aging
        except Exception as exc:
            st.error(str(exc))
    aging = st.session_state.get("aging")
    if aging is not None:
        st.dataframe(aging[["Đối tác","Số hóa đơn chuẩn","Số tiền chuẩn","Ngày đến hạn chuẩn","Số ngày quá hạn","Mức độ"]], use_container_width=True, hide_index=True)
        for i, row in aging.iterrows():
            with st.expander(f"{row['Đối tác']} · {row['Mức độ']} · {row['Số tiền chuẩn']:,.0f} VNĐ"):
                st.text_area("Tin nhắn Zalo nhắc công nợ", row["Tin nhắn nhắc khách"], key=f"msg_{i}")
                st.text_area("Tin nhắn yêu cầu hóa đơn", row["Tin nhắn đòi hóa đơn"], key=f"invmsg_{i}")
                st.text_area("Tin nhắn nhắc hoàn ứng", row["Tin nhắn hoàn ứng"], key=f"staffmsg_{i}")
        st.download_button("Tải báo cáo tuổi nợ", to_excel_bytes({"Tuoi_no":aging}), "Bao_cao_tuoi_no.xlsx")

with pages[5]:
    st.subheader("Kiểm toán nhanh trước kỳ thuế")
    journal_file = st.file_uploader("Tải Sổ Nhật ký chung", type=["xlsx","xls","csv"], key="journal")
    tax_file = st.file_uploader("Tải bảng kê hóa đơn mua vào (không bắt buộc)", type=["xlsx","xls","csv"], key="taxreg")
    if st.button("Chạy kiểm tra", type="primary", disabled=not journal_file):
        try:
            journal = read_tabular(journal_file, ["Ngày hạch toán","Tài khoản Nợ","Tài khoản Có","Số tiền"])
            register = read_tabular(tax_file, ["Tiền thuế","Thuế GTGT"]) if tax_file else None
            findings = pre_audit(journal, register)
            st.session_state["audit_findings"] = findings
        except Exception as exc:
            st.error(f"Không thể kiểm tra: {exc}")
    findings = st.session_state.get("audit_findings")
    if findings is not None:
        if findings.empty:
            st.success("Không phát hiện cảnh báo theo các quy tắc hiện có.")
        else:
            st.warning(f"Phát hiện {len(findings)} điểm cần kế toán rà soát.")
            st.dataframe(findings, use_container_width=True, hide_index=True)
        st.download_button("Tải báo cáo Pre-audit", to_excel_bytes({"Canh_bao":findings}), "Bao_cao_Pre_Audit.xlsx")

with pages[6]:
    st.subheader("Báo cáo quản trị nhanh cho Giám đốc")
    report_file = st.file_uploader("Tải dữ liệu thu-chi trong ngày/tháng", type=["xlsx","xls","csv"], key="report")
    opening = st.number_input("Số dư đầu kỳ (VNĐ)", min_value=0.0, step=100000.0)
    if report_file:
        try:
            df = read_tabular(report_file, ["Ngày giao dịch","Số tiền ghi Có","Số tiền ghi Nợ"])
            inc, _ = money_column(df, ["Số tiền ghi Có","Tiền vào","Thu","Credit"])
            out, _ = money_column(df, ["Số tiền ghi Nợ","Tiền ra","Chi","Debit"])
            total_in, total_out = inc.sum(), out.sum()
            closing = opening + total_in - total_out
            a,b,c = st.columns(3)
            a.metric("Tổng thu", f"{total_in:,.0f} đ")
            b.metric("Tổng chi", f"{total_out:,.0f} đ")
            c.metric("Tồn cuối kỳ", f"{closing:,.0f} đ")
            report_text = f"📊 BÁO CÁO DÒNG TIỀN\n💰 Tồn đầu kỳ: {opening:,.0f} VNĐ\n📥 Tổng thu: {total_in:,.0f} VNĐ\n📤 Tổng chi: {total_out:,.0f} VNĐ\n🏦 Tồn cuối kỳ: {closing:,.0f} VNĐ\n\n_Báo cáo được tạo tự động bởi Trợ lý Kế toán SME._"
            st.text_area("Nội dung gửi Zalo cho Giám đốc", report_text, height=180)
            st.download_button("Tải báo cáo Excel", to_excel_bytes({"Tong_hop":pd.DataFrame([{"Tồn đầu kỳ":opening,"Tổng thu":total_in,"Tổng chi":total_out,"Tồn cuối kỳ":closing}])}),
                               "Bao_cao_dong_tien.xlsx")
        except Exception as exc:
            st.error(str(exc))

with pages[7]:
    st.subheader("Cố vấn nghiệp vụ & pháp lý thuế")
    st.warning("Chế độ hiện tại chưa kết nối kho văn bản pháp luật/RAG đã kiểm chứng. Không sử dụng câu trả lời tự sinh làm căn cứ kê khai hoặc quyết toán.")
    question = st.text_area("Nhập câu hỏi nghiệp vụ kế toán/thuế")
    st.text_input("Khóa API AI (chỉ dùng trong phiên, không lưu)", type="password", key="ai_key")
    if st.button("Chuẩn bị câu hỏi tư vấn", disabled=not question.strip()):
        masked = mask_sensitive(question)
        st.code(masked)
        st.info("Dữ liệu đã được che một số chuỗi định danh phổ biến. Chưa gửi tới AI vì cần cấu hình nhà cung cấp và kho văn bản pháp luật có phiên bản, ngày hiệu lực, nguồn trích dẫn.")
    st.caption("Để hoàn thiện FR-07 cần nạp văn bản pháp luật chính thức, theo dõi hiệu lực và triển khai truy xuất đoạn văn có trích dẫn.")

with pages[8]:
    st.subheader("Tài khoản & gói dịch vụ")
    st.info("Đây là màn hình mô phỏng chính sách gói. Đăng nhập, xác thực người dùng và thanh toán VietQR/webhook chưa được kích hoạt.")
    st.table(pd.DataFrame([
        {"Gói":"Dùng thử","Giá":"0 VNĐ","Giới hạn":"50 dòng tải xuống"},
        {"Gói":"Kế toán Nội bộ","Giá":"69.000 VNĐ/tháng","Giới hạn":"Không giới hạn dòng · 01 MST"},
        {"Gói":"Kế toán Dịch vụ","Giá":"249.000 VNĐ/tháng","Giới hạn":"Không giới hạn dòng · nhiều MST"},
    ]))
    st.caption("Không nhập thông tin tài khoản ngân hàng hoặc thông tin thanh toán thật vào bản MVP này.")

st.divider()
st.caption("TroLyKeToan · MVP theo SRS v1.0 · Kết quả tự động chỉ mang tính hỗ trợ, cần kế toán kiểm tra trước khi ghi sổ/kê khai.")
