from __future__ import annotations

import io
import re
import uuid
from copy import copy
from datetime import date
from urllib.parse import urlencode

import pandas as pd
from openpyxl import load_workbook

from core import find_column, normalize_text, parse_money, similarity, subset_sum_indices


def reconcile_payables(invoices: pd.DataFrame, payments: pd.DataFrame, today: date | None = None,
                       epsilon: int = 1000, threshold: float = 0.80) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Match outgoing payments to supplier invoices; unmatched payments are documentary debt."""
    today = today or date.today()
    inv = invoices.copy().reset_index(drop=True)
    pay = payments.copy().reset_index(drop=True)
    inv_amount_col = find_column(inv.columns, ["Tổng thanh toán", "Số tiền phải trả", "Số tiền", "Amount"], required=True)
    inv_name_col = find_column(inv.columns, ["Tên người bán", "Nhà cung cấp", "Tên nhà cung cấp", "Đối tác"])
    inv_tax_col = find_column(inv.columns, ["MST người bán", "Mã số thuế người bán", "MST", "Mã số thuế"])
    inv_no_col = find_column(inv.columns, ["Số hóa đơn", "Ký hiệu và số hóa đơn", "Invoice number"])
    inv_date_col = find_column(inv.columns, ["Ngày lập", "Ngày hóa đơn", "Issue date"])
    pay_amount_col = find_column(pay.columns, ["Số tiền ghi Nợ", "Tiền ra", "Số tiền chi", "Số tiền", "Amount"], required=True)
    pay_desc_col = find_column(pay.columns, ["Nội dung giao dịch", "Nội dung", "Diễn giải", "Description"])
    pay_date_col = find_column(pay.columns, ["Ngày giao dịch", "Ngày hạch toán", "Ngày", "Transaction date"])
    pay_tax_col = find_column(pay.columns, ["MST nhà cung cấp", "Mã số thuế", "Tax code"])
    pay_name_col = find_column(pay.columns, ["Tên nhà cung cấp", "Nhà cung cấp", "Đối tác"])
    inv["_amount"] = inv[inv_amount_col].map(parse_money)
    inv["_name"] = inv[inv_name_col].fillna("").astype(str) if inv_name_col else ""
    inv["_tax"] = inv[inv_tax_col].fillna("").astype(str).str.replace(r"\D", "", regex=True) if inv_tax_col else ""
    inv["_no"] = inv[inv_no_col].fillna("").astype(str) if inv_no_col else ""
    inv["_date"] = pd.to_datetime(inv[inv_date_col], errors="coerce", dayfirst=True) if inv_date_col else pd.NaT
    pay["_amount"] = pay[pay_amount_col].map(parse_money)
    pay["_desc"] = pay[pay_desc_col].fillna("").astype(str) if pay_desc_col else ""
    pay["_date"] = pd.to_datetime(pay[pay_date_col], errors="coerce", dayfirst=True) if pay_date_col else pd.NaT
    pay["_tax"] = pay[pay_tax_col].fillna("").astype(str).str.replace(r"\D", "", regex=True) if pay_tax_col else ""
    pay["_name"] = pay[pay_name_col].fillna("").astype(str) if pay_name_col else ""
    inv_paid, pay_used = set(), set()
    for pidx, p in pay.iterrows():
        if p["_amount"] <= 0:
            continue
        candidates = [i for i in range(len(inv)) if i not in inv_paid and inv.at[i, "_amount"] > 0]
        def partner_match(i):
            tax_match = bool(p["_tax"] and inv.at[i, "_tax"] and p["_tax"] == inv.at[i, "_tax"])
            name = p["_name"] or p["_desc"]
            name_match = bool(inv.at[i, "_name"] and name and
                              (normalize_text(inv.at[i, "_name"]) in normalize_text(name) or
                               similarity(inv.at[i, "_name"], name) >= threshold))
            return tax_match or name_match
        partner_idxs = [i for i in candidates if partner_match(i)]
        exact = [i for i in partner_idxs if abs(inv.at[i, "_amount"] - p["_amount"]) <= epsilon]
        chosen = [exact[0]] if exact else []
        if not chosen and partner_idxs:
            subset = subset_sum_indices([inv.at[i, "_amount"] for i in partner_idxs], p["_amount"], epsilon)
            if subset:
                chosen = [partner_idxs[j] for j in subset]
        if chosen:
            inv_paid.update(chosen)
            pay_used.add(pidx)
    inv["Trạng thái chứng từ"] = ["Đủ chứng từ" if i in inv_paid else "Hóa đơn treo" for i in range(len(inv))]
    inv["Ngày thanh toán tìm thấy"] = ""
    inv["Cảnh báo"] = ["Đã tìm thấy khoản chi tương ứng" if i in inv_paid else "Có hóa đơn nhưng chưa ghép được khoản chi" for i in range(len(inv))]
    missing = pay.loc[[i for i in range(len(pay)) if i not in pay_used]].copy()
    missing["Số ngày từ ngày chi"] = (pd.Timestamp(today) - missing["_date"]).dt.days
    missing["Trạng thái chứng từ"] = "Thiếu hóa đơn (Nợ chứng từ)"
    missing["Cảnh báo"] = missing.apply(
        lambda r: "Không tìm thấy hóa đơn tương ứng" if pd.notna(r["_date"]) else "Không tìm thấy hóa đơn; thiếu ngày giao dịch",
        axis=1)
    inv = inv.drop(columns=["_amount", "_name", "_tax", "_no", "_date"], errors="ignore")
    missing = missing.drop(columns=["_amount", "_desc", "_date", "_tax", "_name"], errors="ignore")
    return inv, missing


def build_executive_pnl(journal: pd.DataFrame, receivables: pd.DataFrame | None = None,
                        payables: pd.DataFrame | None = None) -> dict:
    debit_col = find_column(journal.columns, ["Tài khoản Nợ", "TK Nợ", "Debit account"], required=True)
    credit_col = find_column(journal.columns, ["Tài khoản Có", "TK Có", "Credit account"], required=True)
    amount_col = find_column(journal.columns, ["Số tiền", "Phát sinh", "Số phát sinh", "Amount"], required=True)
    work = journal.copy()
    work["_debit"] = work[debit_col].fillna("").astype(str).str.strip()
    work["_credit"] = work[credit_col].fillna("").astype(str).str.strip()
    work["_amount"] = work[amount_col].map(parse_money)
    credit_sum = lambda prefix: work.loc[work["_credit"].str.startswith(prefix), "_amount"].sum()
    debit_sum = lambda prefix: work.loc[work["_debit"].str.startswith(prefix), "_amount"].sum()
    gross_revenue = credit_sum("511")
    deductions = debit_sum("521")
    net_revenue = gross_revenue - deductions
    cogs = debit_sum("632")
    payroll = sum(debit_sum(p) for p in ("622", "627", "6411", "6421"))
    operating = sum(debit_sum(p) for p in ("641", "642")) - debit_sum("6411") - debit_sum("6421")
    other_expenses = debit_sum("635") + debit_sum("811")
    estimated_profit = net_revenue - cogs - payroll - operating - other_expenses
    def top_debt(df, amount_aliases, name_aliases, label):
        if df is None or df.empty:
            return []
        amount_col = find_column(df.columns, amount_aliases)
        name_col = find_column(df.columns, name_aliases)
        if not amount_col:
            return []
        tmp = pd.DataFrame({
            "Đối tác": df[name_col].fillna("").astype(str) if name_col else "",
            "Số tiền": df[amount_col].map(parse_money)
        })
        return tmp.groupby("Đối tác", as_index=False)["Số tiền"].sum().sort_values("Số tiền", ascending=False).head(5).to_dict("records")
    return {
        "Doanh thu": float(gross_revenue), "Giảm trừ doanh thu": float(deductions),
        "Doanh thu thuần": float(net_revenue), "Giá vốn": float(cogs),
        "Chi phí nhân công (ước tính)": float(payroll), "Chi phí vận hành (ước tính)": float(operating),
        "Chi phí tài chính/khác": float(other_expenses), "Lợi nhuận ước tính": float(estimated_profit),
        "Top 5 phải thu": top_debt(receivables, ["Còn phải thu", "Số tiền phải thu", "Số dư nợ", "Amount"], ["Tên khách hàng", "Khách hàng", "Đối tác"], "AR"),
        "Top 5 phải trả": top_debt(payables, ["Còn phải trả", "Số tiền phải trả", "Số dư có", "Amount"], ["Tên nhà cung cấp", "Nhà cung cấp", "Đối tác"], "AP"),
        "Ghi chú": "P&L ước tính theo phát sinh tài khoản; cần rà soát bút toán kết chuyển, phân loại chi phí và kỳ báo cáo."
    }


def advanced_pre_audit(journal: pd.DataFrame, trial_balance: pd.DataFrame | None = None,
                       invoice_register: pd.DataFrame | None = None, opening_cash: float = 0.0,
                       period_end: date | None = None, cash_payment_threshold: int | None = None) -> pd.DataFrame:
    from core import pre_audit
    base = pre_audit(journal, invoice_register, opening_cash, cash_payment_threshold)
    findings = base.to_dict("records")
    date_col = find_column(journal.columns, ["Ngày hạch toán", "Ngày chứng từ", "Ngày", "Date"])
    debit_col = find_column(journal.columns, ["Tài khoản Nợ", "TK Nợ", "Debit account"], required=True)
    credit_col = find_column(journal.columns, ["Tài khoản Có", "TK Có", "Credit account"], required=True)
    amount_col = find_column(journal.columns, ["Số tiền", "Phát sinh Nợ", "Số phát sinh", "Amount"], required=True)
    voucher_col = find_column(journal.columns, ["Số chứng từ", "Mã chứng từ", "Voucher number"])
    work = journal.copy()
    work["_debit"] = work[debit_col].fillna("").astype(str).str.strip()
    work["_credit"] = work[credit_col].fillna("").astype(str).str.strip()
    work["_amount"] = work[amount_col].map(parse_money)
    # Check 4: malformed/self-offsetting account pairs. This avoids assuming
    # that every credit to an asset or debit to a liability is erroneous.
    for idx, row in work.iterrows():
        d, cr = row["_debit"], row["_credit"]
        if d and cr and d == cr:
            findings.append({"Nhóm kiểm tra": "Định khoản cùng tài khoản", "Ngày": "",
                "Dòng dữ liệu": int(idx)+2, "Giá trị": row["_amount"],
                "Cảnh báo": f"TK Nợ và TK Có cùng là {d}; cần kiểm tra khả năng nhập nhầm"})
        for side, account in (("Nợ", d), ("Có", cr)):
            if account and not re.fullmatch(r"\d{3,8}", account):
                findings.append({"Nhóm kiểm tra": "Mã tài khoản bất thường", "Ngày": "",
                    "Dòng dữ liệu": int(idx)+2, "Giá trị": row["_amount"],
                    "Cảnh báo": f"Mã tài khoản {side} '{account}' không theo cấu trúc số phổ biến; cần đối chiếu hệ thống tài khoản doanh nghiệp"})
    # Check 5: repeated identical journal lines, not merely repeated voucher IDs
    # (a voucher may legitimately contain many accounting lines).
    desc_col = find_column(work.columns, ["Diễn giải", "Nội dung", "Description"])
    duplicate_keys = ["_debit", "_credit", "_amount"]
    if date_col:
        work["_audit_date"] = pd.to_datetime(work[date_col], errors="coerce", dayfirst=True).dt.strftime("%Y-%m-%d")
        duplicate_keys.append("_audit_date")
    if voucher_col:
        work["_audit_voucher"] = work[voucher_col].fillna("").astype(str).str.strip()
        duplicate_keys.append("_audit_voucher")
    if desc_col:
        work["_audit_desc"] = work[desc_col].fillna("").astype(str).str.strip()
        duplicate_keys.append("_audit_desc")
    duplicate = work[work.duplicated(duplicate_keys, keep=False)]
    for idx, row in duplicate.iterrows():
        findings.append({"Nhóm kiểm tra": "Dòng hạch toán trùng lặp", "Ngày": row.get("_audit_date", ""),
            "Dòng dữ liệu": int(idx)+2, "Giá trị": row["_amount"],
            "Cảnh báo": "Có dòng hạch toán giống nhau về ngày, tài khoản, số tiền và thông tin chứng từ; cần kiểm tra trùng nhập"})
    # Check 6: trial-balance debit/credit totals and potential unclosed P&L accounts.
    if trial_balance is not None and not trial_balance.empty:
        debit_total_col = find_column(trial_balance.columns, ["Tổng phát sinh Nợ", "Phát sinh Nợ", "Số dư Nợ", "Cộng Nợ"])
        credit_total_col = find_column(trial_balance.columns, ["Tổng phát sinh Có", "Phát sinh Có", "Số dư Có", "Cộng Có"])
        account_col = find_column(trial_balance.columns, ["Số hiệu tài khoản", "Tài khoản", "Mã tài khoản", "Account"], required=True)
        if debit_total_col and credit_total_col:
            debit_total = trial_balance[debit_total_col].map(parse_money).sum()
            credit_total = trial_balance[credit_total_col].map(parse_money).sum()
            if abs(debit_total-credit_total) > 1:
                findings.append({"Nhóm kiểm tra": "Bảng cân đối phát sinh không cân", "Ngày": "",
                    "Dòng dữ liệu": "", "Giá trị": debit_total-credit_total,
                    "Cảnh báo": f"Tổng Nợ {debit_total:,.0f} khác tổng Có {credit_total:,.0f}"})
        if period_end:
            accounts = trial_balance[account_col].fillna("").astype(str).str.strip()
            ending_debit_col = find_column(trial_balance.columns, ["Số dư cuối kỳ Nợ", "Dư Nợ cuối kỳ"])
            ending_credit_col = find_column(trial_balance.columns, ["Số dư cuối kỳ Có", "Dư Có cuối kỳ"])
            if ending_debit_col or ending_credit_col:
                debit_end = trial_balance[ending_debit_col].map(parse_money) if ending_debit_col else pd.Series(0, index=trial_balance.index)
                credit_end = trial_balance[ending_credit_col].map(parse_money) if ending_credit_col else pd.Series(0, index=trial_balance.index)
                pnl_open = trial_balance.loc[accounts.str.startswith(("511","521","632","635","641","642","811","711")) & ((debit_end+credit_end)>1)]
                close_dates = pd.to_datetime(work[date_col], errors="coerce", dayfirst=True) if date_col else pd.Series([], dtype="datetime64[ns]")
                close_mask = close_dates.dt.date.eq(period_end)
                has_911 = work.loc[close_mask, "_debit"].str.startswith("911").any() or work.loc[close_mask, "_credit"].str.startswith("911").any()
                if not pnl_open.empty and not has_911:
                    findings.append({"Nhóm kiểm tra": "Rà soát kết chuyển cuối kỳ", "Ngày": period_end,
                        "Dòng dữ liệu": "", "Giá trị": len(pnl_open),
                        "Cảnh báo": "Tài khoản doanh thu/chi phí còn số dư cuối kỳ nhưng chưa thấy bút toán liên quan TK 911 vào ngày kết thúc kỳ; cần xác nhận chế độ và quy trình kết chuyển"})
    return pd.DataFrame(findings, columns=["Nhóm kiểm tra", "Ngày", "Dòng dữ liệu", "Giá trị", "Cảnh báo"])


def map_staging_to_template(staging: pd.DataFrame, template_bytes: bytes,
                            transaction_type: str = "thu_tien_gui") -> tuple[bytes, list[str]]:
    """Fill an uploaded MISA/VACOM template while retaining its worksheets and formatting."""
    if transaction_type not in {"thu_tien_gui", "chi_tien_gui"}:
        raise ValueError("Loại chứng từ phải là thu_tien_gui hoặc chi_tien_gui. Mỗi loại dùng một mẫu riêng.")
    wb = load_workbook(io.BytesIO(template_bytes))
    aliases = {
        "ngay hach toan": ("Ngày hạch toán", "Ngày HT", "Ngày", "Ngày giao dịch", "Accounting date"),
        "ngay chung tu": ("Ngày chứng từ", "Ngày CT", "Ngày", "Ngày giao dịch", "Voucher date"),
        "so chung tu": ("Số chứng từ", "Mã chứng từ", "Số phiếu", "Voucher number"),
        "ma doi tuong": ("Mã đối tượng", "Mã khách hàng", "Mã nhà cung cấp", "Mã đối tượng THCP"),
        "ten doi tuong": ("Tên đối tượng", "Đối tác", "Tên khách hàng", "Nhà cung cấp"),
        "dien giai": ("Diễn giải", "Diễn giải chuẩn", "Diễn giải gốc", "Nội dung", "Description"),
        "tai khoan no": ("Tài khoản Nợ", "TK Nợ", "Tài khoản hạch toán Nợ"),
        "tai khoan co": ("Tài khoản Có", "TK Có", "Tài khoản hạch toán Có"),
        "so tien": ("Số tiền", "Tiền thu", "Tiền chi", "Số tiền quy đổi"),
        "ma hoa don": ("Số hóa đơn", "Mã hóa đơn", "Invoice number"),
        "ma so thue": ("Mã số thuế", "MST", "Tax code"),
    }
    source_aliases = {
        "ngay hach toan": ("Ngày",), "ngay chung tu": ("Ngày",),
        "so chung tu": (), "ma doi tuong": ("Mã đối tượng",),
        "ten doi tuong": ("Đối tác",), "dien giai": ("Diễn giải chuẩn", "Diễn giải gốc"),
        "tai khoan no": ("Tài khoản Nợ",), "tai khoan co": ("Tài khoản Có",),
        "so tien": ("Tiền thu",) if transaction_type == "thu_tien_gui" else ("Tiền chi",),
        "ma hoa don": ("Mã hóa đơn",), "ma so thue": ("Mã số thuế",),
    }
    required = {"ngay hach toan", "ngay chung tu", "so chung tu", "tai khoan no", "tai khoan co", "so tien"}
    chosen_sheet, header_row, mapping, best_score = None, None, None, -1
    for ws in wb.worksheets:
        for row_idx in range(1, min(ws.max_row, 40)+1):
            raw_headers = {normalize_text(cell.value): cell.column for cell in ws[row_idx] if cell.value is not None}
            canonical = {}
            for key, names in aliases.items():
                for name in names:
                    norm = normalize_text(name)
                    if norm in raw_headers:
                        canonical[key] = raw_headers[norm]
                        break
            score = len(canonical)
            if score > best_score:
                chosen_sheet, header_row, mapping, best_score = ws, row_idx, canonical, score
    if chosen_sheet is None or not mapping or sum(1 for k in required if k in mapping) < len(required):
        raise ValueError("Không nhận diện được mẫu Excel hoặc thiếu các cột bắt buộc. Hãy tải đúng mẫu nhập khẩu cho loại chứng từ đã chọn từ phần mềm kế toán.")
    missing_headers = [k for k in required if k not in mapping]
    if missing_headers:
        raise ValueError("Mẫu thiếu cột bắt buộc: " + ", ".join(missing_headers))
    source_cols = {}
    for key, source_names in source_aliases.items():
        if source_names:
            col = find_column(staging.columns, source_names)
            if col:
                source_cols[key] = col
    amount_source = "Tiền thu" if transaction_type == "thu_tien_gui" else "Tiền chi"
    rows = staging.copy()
    if amount_source in rows.columns:
        rows = rows[rows[amount_source].map(parse_money) > 0]
    rows = rows[~rows.get("Chưa đủ chứng từ", pd.Series(False, index=rows.index)).fillna(False).astype(bool)]
    # MISA SME imports bank receipts and payments as separate voucher types/files.
    style_source = chosen_sheet[header_row+1] if chosen_sheet.max_row >= header_row+1 else None
    for out_idx, (_, source) in enumerate(rows.iterrows(), start=header_row+1):
        for key, col_idx in mapping.items():
            if key not in aliases:
                continue
            cell = chosen_sheet.cell(out_idx, col_idx)
            if style_source:
                source_cell = style_source[col_idx-1] if col_idx-1 < len(style_source) else None
                if source_cell and source_cell.has_style:
                    cell._style = copy(source_cell._style)
            if key == "so chung tu":
                cell.value = f"{'PTG' if transaction_type == 'thu_tien_gui' else 'PCG'}{out_idx-header_row:06d}"
            elif key in source_cols:
                cell.value = source[source_cols[key]]
            elif key in {"ngay hach toan", "ngay chung tu"}:
                cell.value = source.get("Ngày")
            elif key == "so tien":
                cell.value = parse_money(source.get(amount_source, 0))
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue(), [aliases[k][0] for k in mapping if k in aliases and k not in source_cols and k not in {"so chung tu","ngay hach toan","ngay chung tu"}]


def build_sepay_qr_url(bank: str, account: str, amount: int, user_id: str,
                       account_holder: str = "", require_sevqr: bool = False) -> str:
    if not bank or not account or not user_id or amount <= 0:
        raise ValueError("Thiếu cấu hình ngân hàng hoặc thông tin thanh toán.")
    code = re.sub(r"[^A-Za-z0-9]", "", user_id).upper()
    memo = code if code.startswith("TKPRO") else f"TKPRO{code}"
    if require_sevqr:
        memo = f"SEVQR {memo}"
    params = {"acc": account, "bank": bank, "amount": str(int(amount)), "des": memo, "template": "compact"}
    if account_holder:
        params["holder"] = re.sub(r"[^A-Za-z0-9 ]", "", account_holder.upper())[:80]
        params["showinfo"] = "true"
    return "https://vietqr.app/img?" + urlencode(params)
