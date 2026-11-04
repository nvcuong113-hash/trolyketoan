from __future__ import annotations

import gc
import os
import time
from datetime import date

import pandas as pd
import streamlit as st
from dotenv import load_dotenv

load_dotenv()

from core import aging_report, build_staging, cashflow_summary, excel_bytes, mask_sensitive, parse_invoice_upload, read_tabular_bytes, reconcile_bank
from accounting_extensions import advanced_pre_audit, build_executive_pnl, build_sepay_qr_url, map_staging_to_template, reconcile_payables
from auth_service import get_public_client, get_admin_client, restore_session, save_session, sign_in, current_subscription
from legal_rag import build_legal_index, retrieve_legal_context, answer_with_openai, make_checklist_xlsx
from legal_storage import publish_legal_document, load_published_legal_documents
from sample_data import sample_workbook, sample_xml_zip, sample_legal_questions
from reports import management_report_pdf
from document_ai import extract_document, documents_to_dataframes, normalize_bank_statement, reconcile_invoice_registers, map_invoice_payments

st.set_page_config(page_title="Trợ lý Kế toán SME", page_icon="🧾", layout="wide")
TTL_SECONDS = 600
PLAN_LABELS = {"internal": "Kế toán Nội bộ", "service": "Kế toán Dịch vụ"}

def upload_key(name):
    return f"{name}_{st.session_state.get('_upload_epoch',0)}"

def clear_data():
    # Keep authentication and increment uploader epoch so every uploaded file
    # widget is recreated empty on the next run.
    next_epoch = int(st.session_state.get("_upload_epoch",0)) + 1
    for key in list(st.session_state.keys()):
        if key not in {"auth_session", "_last_activity", "_upload_epoch"}:
            try:
                del st.session_state[key]
            except Exception:
                pass
    st.session_state["_upload_epoch"] = next_epoch
    gc.collect()

now = time.time()
if now - st.session_state.get("_last_activity", now) > TTL_SECONDS:
    clear_data()
    st.toast("Phiên làm việc đã quá 10 phút không hoạt động; dữ liệu tạm đã được xóa.", icon="🧹")
st.session_state["_last_activity"] = now

@st.fragment(run_every="30s")
def inactivity_guard():
    last = st.session_state.get("_last_activity",time.time())
    if time.time()-last > TTL_SECONDS:
        clear_data()
        st.session_state["_last_activity"] = time.time()
        st.session_state["_session_expired"] = True
        st.rerun()

inactivity_guard()
if st.session_state.pop("_session_expired",False):
    st.toast("Phiên đã hết hạn do không hoạt động; tệp và dữ liệu tạm đã được xóa.",icon="🧹")

def get_setting(name, default=""):
    value = os.getenv(name)
    if value:
        return value
    try:
        return st.secrets.get(name, default)
    except Exception:
        return default

def read_upload(file, aliases=()):
    return read_tabular_bytes(file.name, file.getvalue(), aliases) if file else None


def download_sample_excel(label, module, key, sheet=None):
    sheets = sample_workbook(module)
    if sheet:
        sheets = {sheet: sheets[sheet]}
    st.download_button(label, data=excel_bytes(sheets),
        file_name=f"Du_lieu_mau_{module.upper()}_{(sheet or 'ALL').replace(' ','_')}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        key=key, use_container_width=True,
        on_click=clear_data if st.session_state.get("clear_after_download", True) else None)

def download_sample_bytes(label, data, filename, mime, key):
    st.download_button(label, data=data, file_name=filename, mime=mime,
        key=key, use_container_width=True,
        on_click=clear_data if st.session_state.get("clear_after_download", True) else None)

def export(label, sheets, filename, key):
    premium = bool(st.session_state.get("active_subscription"))
    limited = {name: frame if premium else frame.head(50) for name,frame in sheets.items()}
    st.download_button(label, data=excel_bytes(limited), file_name=filename,
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        key=key, use_container_width=True,
        on_click=clear_data if st.session_state.get("clear_after_download", True) else None)
    if not premium:
        st.caption("Tài khoản khách: file tải xuống giới hạn 50 dòng đầu mỗi sheet; thống kê trên màn hình vẫn tính toàn bộ dữ liệu.")

def export_bytes(label, data, filename, key):
    st.download_button(label, data=data, file_name=filename,
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        key=key, use_container_width=True,
        on_click=clear_data if st.session_state.get("clear_after_download", True) else None)

@st.fragment(run_every="3s")
def payment_status_widget(client, payment_code):
    try:
        rows = client.table("payments").select("status,paid_at,plan,amount,expires_at").eq("payment_code",payment_code).limit(1).execute().data
        if not rows:
            st.caption("Đang chờ trạng thái đơn thanh toán…")
            return
        row = rows[0]
        if row["status"] == "paid":
            st.success(f"Thanh toán thành công · Gói {PLAN_LABELS.get(row['plan'],row['plan'])} đã được kích hoạt.")
            if st.session_state.get("payment_confirmed_for") != payment_code:
                st.session_state["payment_confirmed_for"] = payment_code
                st.rerun()
        elif row["status"] == "expired":
            st.warning("Đơn thanh toán đã hết hạn. Hãy tạo đơn mới.")
        elif row.get("expires_at") and pd.to_datetime(row["expires_at"],utc=True) <= pd.Timestamp.now(tz="UTC"):
            st.warning("Đơn đã hết thời gian hiệu lực; cron worker sẽ cập nhật trạng thái hết hạn. Hãy tạo đơn mới.")
        elif row["status"] == "rejected":
            st.error("Giao dịch chưa được xác nhận do sai số tiền hoặc thông tin đơn.")
        else:
            st.info("Đang chờ ngân hàng xác nhận thanh toán. Trạng thái tự cập nhật mỗi 3 giây.")
    except Exception as exc:
        st.caption(f"Chưa đọc được trạng thái thanh toán: {exc}")

client = get_public_client()
admin_client = get_admin_client()
current_user = restore_session(client) if client else None
if "legal_documents" not in st.session_state:
    try:
        st.session_state["legal_documents"] = load_published_legal_documents(admin_client) if admin_client else []
    except Exception:
        st.session_state["legal_documents"] = []
subscription = None
if client and current_user:
    try:
        subscription = current_subscription(client)
    except Exception as exc:
        st.session_state["subscription_error"] = str(exc)
st.session_state["active_subscription"] = subscription if subscription and subscription.get("expires_at") else None

st.title("🧾 Trợ lý Kế toán SME")
st.caption("Đối soát ngân hàng · Kiểm soát chứng từ · Quản trị dòng tiền")
if client is None:
    st.info("Chế độ dùng thử đang mở. Đăng nhập và thanh toán sẽ hoạt động sau khi cấu hình Supabase.")
elif current_user:
    plan = PLAN_LABELS.get(subscription.get("plan"), "Khách dùng thử") if subscription else "Khách dùng thử"
    st.success(f"Đang đăng nhập: {current_user.email or current_user.id} · {plan}")
else:
    st.info("Bạn đang dùng chế độ khách. Có thể xử lý dữ liệu thử và tải tối đa 50 dòng mỗi sheet.")

with st.sidebar:
    st.markdown("### Không gian làm việc")
    st.info("Tệp kế toán chỉ xử lý tạm trong bộ nhớ phiên; không ghi sổ kế toán vào database.")
    st.caption("Phiên tạm tự kiểm tra timeout sau 10 phút không hoạt động.")
    st.checkbox("Tự xóa file và dữ liệu tạm sau khi tải xuống",value=True,key="clear_after_download")
    if st.button("🧹 Xóa dữ liệu nghiệp vụ", use_container_width=True):
        clear_data()
        st.rerun()

tabs = st.tabs(["Tổng quan","01 · Đối soát","02 · Hóa đơn","03 · Xuất dữ liệu","04 · Tuổi nợ","05 · Pre-audit","06 · Báo cáo","07 · Cố vấn thuế","08 · Tài khoản & gói", "09 · AI Chứng từ & Đối chiếu"])

with tabs[0]:
    st.subheader("Trung tâm nghiệp vụ kế toán")
    a,b,c,d = st.columns(4)
    a.metric("Phân hệ","08"); b.metric("Định dạng","XLSX · CSV · XML · ZIP"); c.metric("Lưu sổ sách","Không"); d.metric("Timeout phiên","10 phút")
    st.markdown("""
    **Quy trình:** Đối soát sao kê → kiểm soát hóa đơn và nợ chứng từ → chuẩn hóa và xuất dữ liệu → theo dõi tuổi nợ → pre-audit → báo cáo quản trị.

    **Lưu ý:** kết quả tự động là gợi ý. Kế toán phải kiểm tra chứng từ gốc, chế độ kế toán áp dụng và căn cứ pháp lý trước khi ghi sổ/kê khai.
    """)
    sample = {
        "Sao_ke":pd.DataFrame([{"Ngày giao dịch":"03/11/2026","Số tiền ghi Có":1500000,"Số tiền ghi Nợ":0,"Nội dung giao dịch":"CTY ABC thanh toan HD001"}]),
        "Cong_no":pd.DataFrame([{"Mã khách hàng":"ABC","Tên khách hàng":"CTY ABC","Số hóa đơn":"HD001","Ngày hóa đơn":"01/10/2026","Số tiền phải thu":1500000,"Ngày đến hạn":"15/10/2026"}]),
        "Nhat_ky":pd.DataFrame([{"Ngày hạch toán":"03/11/2026","Tài khoản Nợ":"1121","Tài khoản Có":"131","Số tiền":1500000,"Diễn giải":"Thu tiền khách hàng"}])
    }
    export("⬇ Tải bộ file mẫu dùng thử",sample,"Bo_file_mau_TroLyKeToan.xlsx","demo")
    st.markdown("**Tài liệu tham khảo:**")
    st.markdown("- [Hướng dẫn nhập khẩu Excel MISA SME 2026](https://helpsme.misa.vn/2026/kb/mot-so-cau-hoi-thuong-gap-lien-quan-den-ngan-hang-va-bao-cao-ngan-hang/)")
    st.markdown("- [Cơ sở dữ liệu quốc gia về văn bản pháp luật](https://vbpl.moj.gov.vn/)")

with tabs[1]:
    st.subheader("Đối soát sao kê ngân hàng & ghép hóa đơn trả gộp")
    sample_cols = st.columns(2)
    with sample_cols[0]: download_sample_excel("⬇ File mẫu sao kê", "fr01", "sample_fr01_bank", "Sao_ke")
    with sample_cols[1]: download_sample_excel("⬇ File mẫu công nợ", "fr01", "sample_fr01_ar", "Cong_no")
    left,right = st.columns(2)
    bank = left.file_uploader("1. Sao kê ngân hàng",["xlsx","xls","csv"],key=upload_key("bank"))
    ar = right.file_uploader("2. Danh sách công nợ phải thu",["xlsx","xls","csv"],key=upload_key("ar"))
    threshold = st.slider("Ngưỡng khớp từ khóa đối tác",0.60,1.00,0.80,0.01)
    epsilon = st.number_input("Sai số ghép hóa đơn (VNĐ)",0,100000,1000,100)
    if st.button("🔎 Thực hiện đối soát",type="primary",disabled=not(bank and ar)):
        try:
            bank_df = read_upload(bank,["Ngày giao dịch","Số tiền ghi Có","Nội dung giao dịch"])
            ar_df = read_upload(ar,["Tên khách hàng","Số hóa đơn","Số tiền phải thu"])
            matched,unmatched = reconcile_bank(bank_df,ar_df,threshold,int(epsilon))
            st.session_state["data_reconcile"] = matched
            st.session_state["data_unmatched_ar"] = unmatched
            st.session_state["data_outgoing"] = build_staging(bank_df,"99")
            st.success(f"Đã xử lý {len(matched)} giao dịch ghi Có và phân loại gợi ý các khoản ghi Nợ.")
        except Exception as exc:
            st.error(f"Không thể đối soát: {exc}")
    result = st.session_state.get("data_reconcile")
    if result is not None:
        a,b,c = st.columns(3)
        a.metric("Khớp 1-1",int((result["Kết quả"]=="Khớp 1-1").sum()))
        b.metric("Khớp trả gộp",int((result["Kết quả"]=="Khớp trả gộp (1-N)").sum()))
        c.metric("Chưa xác định",int((result["Kết quả"]=="Chưa xác định").sum()))
        for label in ["Khớp 1-1","Khớp trả gộp (1-N)","Chưa xác định"]:
            with st.expander(f"{label} · {int((result['Kết quả']==label).sum())}",expanded=label=="Chưa xác định"):
                st.dataframe(result[result["Kết quả"]==label],use_container_width=True,hide_index=True)
        st.markdown("**Hóa đơn còn chưa ghép**")
        st.dataframe(st.session_state["data_unmatched_ar"],use_container_width=True,hide_index=True)
        export("⬇ Tải kết quả đối soát",{"Ket_qua":result,"Hoa_don_chua_ghep":st.session_state["data_unmatched_ar"]},"Ket_qua_doi_soat.xlsx","reconcile_export")
        outgoing = st.session_state.get("data_outgoing")
        if outgoing is not None:
            with st.expander("Gợi ý phân loại các khoản tiền ra"):
                st.dataframe(outgoing[outgoing["Tiền chi"]>0],use_container_width=True,hide_index=True)
                st.caption("Định khoản chỉ là gợi ý theo từ khóa; chưa tự xác định VAT hoặc đối tượng hạch toán.")

with tabs[2]:
    st.subheader("Kiểm soát hóa đơn XML & nợ chứng từ")
    sample_cols = st.columns(2)
    with sample_cols[0]: download_sample_bytes("⬇ ZIP hóa đơn XML mẫu", sample_xml_zip(), "HoaDon_XML_Mau.zip", "application/zip", "sample_fr02_xml")
    with sample_cols[1]: download_sample_excel("⬇ Nhật ký thanh toán mẫu", "fr02", "sample_fr02_payments", "Payment_Log")
    st.caption("MST người mua trong XML mẫu: 0109999999 · Tên người mua: Cong ty khach hang mau. Chữ ký trong file là placeholder, không phải chữ ký số hợp lệ.")
    tax = st.text_input("Mã số thuế doanh nghiệp",key="co_tax")
    company = st.text_input("Tên doanh nghiệp theo đăng ký",key="co_name")
    xml = st.file_uploader("Hóa đơn XML hoặc ZIP chứa nhiều XML",["xml","zip"],key=upload_key("xml"))
    pay = st.file_uploader("Nhật ký thanh toán / sao kê tiền ra (không bắt buộc)",["xlsx","xls","csv"],key=upload_key("payments"))
    if st.button("🧾 Kiểm tra hóa đơn và nợ chứng từ",type="primary",disabled=not xml):
        try:
            invoices = parse_invoice_upload(xml.name,xml.getvalue(),tax,company)
            if pay:
                payment_df = read_upload(pay,["Số tiền ghi Nợ","Nội dung","Ngày giao dịch"])
                invoice_status,missing = reconcile_payables(invoices,payment_df)
                st.session_state["data_invoices"] = invoice_status
                st.session_state["data_missing_docs"] = missing
            else:
                st.session_state["data_invoices"] = invoices
                st.session_state["data_missing_docs"] = pd.DataFrame()
            st.success(f"Đã đọc {len(invoices)} hóa đơn.")
        except Exception as exc:
            st.error(f"Không thể kiểm tra hóa đơn: {exc}")
    invoices = st.session_state.get("data_invoices")
    if invoices is not None:
        st.markdown("**Danh sách hóa đơn và trạng thái chứng từ**")
        m1,m2,m3 = st.columns(3)
        m1.metric("Đủ chứng từ",int((invoices["Trạng thái chứng từ"]=="Đủ chứng từ").sum()) if "Trạng thái chứng từ" in invoices else 0)
        m2.metric("Thanh toán một phần",int((invoices["Trạng thái chứng từ"]=="Thanh toán một phần").sum()) if "Trạng thái chứng từ" in invoices else 0)
        m3.metric("Hóa đơn treo",int((invoices["Trạng thái chứng từ"]=="Hóa đơn treo").sum()) if "Trạng thái chứng từ" in invoices else 0)
        st.dataframe(invoices,use_container_width=True,hide_index=True)
        missing = st.session_state.get("data_missing_docs",pd.DataFrame())
        if not missing.empty:
            st.warning(f"Có {len(missing)} khoản chi chưa ghép được hóa đơn – cần rà soát nợ chứng từ.")
            st.dataframe(missing,use_container_width=True,hide_index=True)
        export("⬇ Tải báo cáo hóa đơn và nợ chứng từ",{"Hoa_don":invoices,"No_chung_tu":missing},"Bao_cao_hoa_don_no_chung_tu.xlsx","invoice_export")
    st.warning("Parser kiểm tra trường dữ liệu và dấu hiệu có cấu trúc chữ ký; chưa xác thực mật mã chữ ký số hoặc tra cứu trạng thái hóa đơn tại cơ quan Thuế.")

with tabs[3]:
    st.subheader("Nhập một lần – xuất đa đích")
    download_sample_excel("⬇ File mẫu dữ liệu trung gian", "fr03", "sample_fr03_staging", "Du_lieu_giao_dich")
    source = st.file_uploader("Tải nhật ký thu-chi / dữ liệu đã đối soát",["xlsx","xls","csv"],key=upload_key("staging_file"))
    standard = st.selectbox("Chế độ kế toán tham chiếu",["99","133","200-transition"],
        format_func=lambda x:{"99":"Thông tư 99/2025/TT-BTC (năm tài chính bắt đầu từ 2026)","133":"Thông tư 133/2016/TT-BTC (DN nhỏ và vừa đủ điều kiện)","200-transition":"TT200 – chỉ kỳ tài chính chuyển tiếp phù hợp"}[x])
    if st.button("Chuẩn hóa dữ liệu",disabled=not source):
        try:
            st.session_state["data_staging"] = build_staging(read_upload(source,["Ngày giao dịch","Tiền vào","Tiền ra","Nội dung"]),standard)
            st.session_state.pop("data_exports",None)
        except Exception as exc:
            st.error(str(exc))
    staging = st.session_state.get("data_staging")
    if staging is not None:
        edited = st.data_editor(staging,use_container_width=True,num_rows="dynamic",key="staging_editor")
        st.session_state["data_staging"] = edited
        if st.button("📦 Tạo báo cáo nội bộ và dữ liệu trung gian",type="primary"):
            internal = edited.copy()
            importable = edited[~edited["Chưa đủ chứng từ"].fillna(False).astype(bool)].copy()
            st.session_state["data_exports"] = {"internal":internal,"import":importable}
        exports = st.session_state.get("data_exports")
        if exports:
            a,b = st.columns(2)
            with a:
                st.metric("Dòng báo cáo nội bộ",len(exports["internal"]))
                export("⬇ Báo cáo thu chi nội bộ",{"Bao_cao_thu_chi":exports["internal"]},"Bao_Cao_Thu_Chi_Noi_Bo.xlsx","internal_export")
            with b:
                st.metric("Dòng dữ liệu trung gian đủ chứng từ",len(exports["import"]))
                export("⬇ Dữ liệu trung gian",{"Du_lieu_ke_toan":exports["import"]},"Du_Lieu_Trung_Gian.xlsx","staging_export")
        st.divider()
        st.markdown("**Tạo file theo mẫu import chính thức**")
        st.caption("MISA yêu cầu từng loại chứng từ dùng một file mẫu riêng. Tải mẫu đúng loại chứng từ trong phần mềm, sau đó đưa vào đây để giữ nguyên tên cột và định dạng.")
        target = st.selectbox("Loại chứng từ",["thu_tien_gui","chi_tien_gui","chuyen_tien_noi_bo"],format_func=lambda x:{"thu_tien_gui":"Thu tiền gửi","chi_tien_gui":"Chi tiền gửi","chuyen_tien_noi_bo":"Chuyển tiền nội bộ"}[x])
        if target == "chuyen_tien_noi_bo":
            st.caption("Trước khi xuất, sửa Loại chứng từ thành Chuyển tiền nội bộ và điền Tài khoản đi/Tài khoản đến trên từng dòng.")
        template = st.file_uploader("Tải mẫu Excel import từ MISA/VACOM",["xlsx"],key=upload_key("accounting_template"))
        default_prefix = {"thu_tien_gui":"PTG","chi_tien_gui":"PCG","chuyen_tien_noi_bo":"CTNB"}[target]
        voucher_prefix = st.text_input("Tiền tố số chứng từ",value=default_prefix)
        voucher_start = st.number_input("Số thứ tự chứng từ bắt đầu (kiểm tra để tránh trùng)",min_value=1,value=1,step=1)
        if st.button("Ánh xạ dữ liệu vào file mẫu",disabled=not(template and voucher_prefix.strip())):
            try:
                rows_for_import = edited if st.session_state.get("active_subscription") else edited.head(50)
                mapped,missing_fields = map_staging_to_template(rows_for_import,template.getvalue(),target,voucher_prefix,int(voucher_start))
                st.session_state["result_mapped_template"] = mapped
                st.session_state["result_template_generated_premium"] = bool(st.session_state.get("active_subscription"))
                st.session_state["result_template_missing"] = missing_fields
                st.success("Đã tạo file trên cấu trúc mẫu được tải lên.")
            except Exception as exc:
                st.error(str(exc))
        if st.session_state.get("result_mapped_template") and not st.session_state.get("active_subscription") and st.session_state.get("result_template_generated_premium"):
            st.session_state.pop("result_mapped_template",None)
            st.session_state.pop("result_template_missing",None)
            st.session_state.pop("result_template_generated_premium",None)
        if st.session_state.get("result_mapped_template"):
            if not st.session_state.get("active_subscription"):
                st.caption("Gói khách: file import theo mẫu giới hạn 50 dòng đầu.")
            if st.session_state.get("result_template_missing"):
                st.warning("Một số cột mẫu chưa có dữ liệu ánh xạ: "+", ".join(st.session_state["result_template_missing"]))
            export_bytes("⬇ Tải file đã ánh xạ theo mẫu",st.session_state["result_mapped_template"],f"Import_{target}.xlsx","mapped_template_export")
    st.info("Không tự tuyên bố file trung gian tương thích 100% với mọi phiên bản. Với MISA SME 2026, mỗi loại chứng từ cần đúng file mẫu riêng; kế toán phải kiểm tra trước khi nhập khẩu.")

with tabs[4]:
    st.subheader("Quản trị tuổi nợ & tin nhắn nhắc việc")
    download_sample_excel("⬇ File mẫu công nợ và ngày đến hạn", "fr04", "sample_fr04_aging", "Cong_no")
    f = st.file_uploader("Danh sách công nợ có ngày đến hạn",["xlsx","xls","csv"],key=upload_key("aging_file"))
    if st.button("Tính tuổi nợ",disabled=not f):
        try:
            st.session_state["data_aging"] = aging_report(read_upload(f,["Ngày đến hạn","Tên khách hàng","Số tiền"]))
        except Exception as exc:
            st.error(str(exc))
    aging = st.session_state.get("data_aging")
    if aging is not None:
        counts = aging["Mức độ"].value_counts()
        cols = st.columns(4)
        for col,label in zip(cols,["Sắp đến hạn","Quá hạn nhẹ","Quá hạn nghiêm trọng","Chưa đến hạn"]):
            col.metric(label,int(counts.get(label,0)))
        st.dataframe(aging[["Đối tác","Số hóa đơn","Số tiền","Ngày đến hạn chuẩn","Số ngày quá hạn","Mức độ"]],use_container_width=True,hide_index=True)
        for i,row in aging.iterrows():
            with st.expander(f"{row['Đối tác']} · {row['Mức độ']} · {row['Số tiền']:,.0f} VNĐ"):
                st.text_area("Tin nhắn nhắc khách trả công nợ",row["Tin nhắn nhắc công nợ"],key=f"ar_{i}")
                st.text_area("Tin nhắn yêu cầu nhà cung cấp gửi hóa đơn",row["Tin nhắn yêu cầu hóa đơn"],key=f"ap_{i}")
                st.text_area("Tin nhắn nhắc nhân viên hoàn ứng",row["Tin nhắn nhắc hoàn ứng"],key=f"staff_{i}")
        export("⬇ Tải báo cáo tuổi nợ",{"Tuoi_no":aging},"Bao_cao_tuoi_no.xlsx","aging_export")

with tabs[5]:
    st.subheader("Kiểm toán nhanh trước kỳ thuế – Pre-audit")
    sample_cols = st.columns(3)
    with sample_cols[0]: download_sample_excel("⬇ Nhật ký chung mẫu", "fr05", "sample_fr05_journal", "Nhat_ky_chung")
    with sample_cols[1]: download_sample_excel("⬇ Bảng cân đối mẫu", "fr05", "sample_fr05_tb", "Trial_Balance")
    with sample_cols[2]: download_sample_excel("⬇ Bảng kê VAT mẫu", "fr05", "sample_fr05_vat", "Bang_ke_VAT")
    journal = st.file_uploader("Sổ Nhật ký chung",["xlsx","xls","csv"],key=upload_key("journal"))
    tb = st.file_uploader("Bảng cân đối phát sinh / Trial Balance",["xlsx","xls","csv"],key=upload_key("trial_balance"))
    register = st.file_uploader("Bảng kê hóa đơn mua vào/bán ra",["xlsx","xls","csv"],key=upload_key("register"))
    opening = st.number_input("Số dư đầu kỳ TK 1111 (VNĐ)",min_value=0.0,step=100000.0)
    cash_threshold = st.number_input("Ngưỡng rà soát thanh toán tiền mặt (VNĐ; 0 = không chạy)",min_value=0,value=0,step=1000000)
    period_end = st.date_input("Ngày kết thúc kỳ kiểm tra",value=date.today())
    if st.button("🩺 Chạy 6 nhóm kiểm tra",type="primary",disabled=not journal):
        try:
            j = read_upload(journal,["Ngày hạch toán","Tài khoản Nợ","Tài khoản Có","Số tiền"])
            tb_df = read_upload(tb,["Tài khoản","Tổng phát sinh Nợ","Tổng phát sinh Có"]) if tb else None
            reg = read_upload(register,["Tiền thuế","Thuế GTGT"]) if register else None
            st.session_state["data_audit"] = advanced_pre_audit(j,tb_df,reg,opening,period_end,int(cash_threshold) if cash_threshold else None)
        except Exception as exc:
            st.error(str(exc))
    audit = st.session_state.get("data_audit")
    if audit is not None:
        if audit.empty:
            st.success("Không phát hiện cảnh báo theo các quy tắc hiện có.")
        else:
            st.warning(f"Có {len(audit)} dòng cảnh báo cần rà soát.")
            st.dataframe(audit,use_container_width=True,hide_index=True)
        export("⬇ Tải báo cáo Pre-audit",{"Canh_bao":audit},"Bao_cao_Pre_Audit.xlsx","audit_export")
    st.caption("Các quy tắc là cảnh báo sơ bộ, không tự kết luận vi phạm thuế. Ngưỡng/điều kiện thanh toán không dùng tiền mặt phải xét theo văn bản, kỳ và loại thuế áp dụng.")

with tabs[6]:
    st.subheader("Báo cáo quản trị nhanh cho Giám đốc")
    sample_cols = st.columns(4)
    with sample_cols[0]: download_sample_excel("⬇ Sao kê mẫu", "fr06", "sample_fr06_cash", "Sao_ke")
    with sample_cols[1]: download_sample_excel("⬇ Sổ chi tiết mẫu", "fr06", "sample_fr06_gl", "So_chi_tiet")
    with sample_cols[2]: download_sample_excel("⬇ Phải thu mẫu", "fr06", "sample_fr06_ar", "Phai_thu")
    with sample_cols[3]: download_sample_excel("⬇ Phải trả mẫu", "fr06", "sample_fr06_ap", "Phai_tra")
    cash_file = st.file_uploader("Sao kê thu-chi ngày/tháng",["xlsx","xls","csv"],key=upload_key("report_cash"))
    opening = st.number_input("Số dư đầu kỳ tiền gửi/quỹ (VNĐ)",min_value=0.0,step=100000.0,key="report_opening")
    if st.button("Tổng hợp dòng tiền",disabled=not cash_file):
        try:
            st.session_state["data_cashflow"] = cashflow_summary(read_upload(cash_file,["Số tiền ghi Có","Số tiền ghi Nợ","Tiền vào","Tiền ra"]),opening)
        except Exception as exc:
            st.error(str(exc))
    summary = st.session_state.get("data_cashflow")
    if summary:
        a,b,c,d = st.columns(4)
        a.metric("Tổng thu",f"{summary['Tổng thu']:,.0f} đ")
        b.metric("Tổng chi",f"{summary['Tổng chi']:,.0f} đ")
        c.metric("Dòng tiền thuần",f"{summary['Dòng tiền thuần']:,.0f} đ")
        d.metric("Tồn cuối kỳ",f"{summary['Tồn cuối kỳ']:,.0f} đ")
        message = (f"📊 BÁO CÁO DÒNG TIỀN\n💰 Tồn đầu kỳ: {summary['Tồn đầu kỳ']:,.0f} VNĐ\n📥 Tổng thu: {summary['Tổng thu']:,.0f} VNĐ\n📤 Tổng chi: {summary['Tổng chi']:,.0f} VNĐ\n📈 Dòng tiền thuần: {summary['Dòng tiền thuần']:,.0f} VNĐ\n🏦 Tồn cuối kỳ: {summary['Tồn cuối kỳ']:,.0f} VNĐ")
        st.text_area("Nội dung gửi Zalo cho Giám đốc",message,height=170)
        export("⬇ Tải báo cáo dòng tiền",{"Tong_hop":pd.DataFrame([summary])},"Bao_cao_dong_tien.xlsx","cashflow_export")
    st.divider()
    st.markdown("**Báo cáo lãi lỗ tháng (P&L ước tính)**")
    gl = st.file_uploader("Sổ chi tiết / Nhật ký chung có ngày, tài khoản Nợ, Có và số tiền",["xlsx","xls","csv"],key=upload_key("pnl_gl"))
    pnl_start = st.date_input("Từ ngày",value=date.today().replace(day=1),key="pnl_start")
    pnl_end = st.date_input("Đến ngày",value=date.today(),key="pnl_end")
    ar_file = st.file_uploader("Bảng công nợ phải thu (không bắt buộc)",["xlsx","xls","csv"],key=upload_key("pnl_ar"))
    ap_file = st.file_uploader("Bảng công nợ phải trả (không bắt buộc)",["xlsx","xls","csv"],key=upload_key("pnl_ap"))
    if st.button("Lập P&L và Top 5 công nợ",disabled=not gl):
        try:
            journal_df = read_upload(gl,["Tài khoản Nợ","Tài khoản Có","Số tiền"])
            ar_df = read_upload(ar_file,["Tên khách hàng","Còn phải thu"]) if ar_file else None
            ap_df = read_upload(ap_file,["Tên nhà cung cấp","Còn phải trả"]) if ap_file else None
            st.session_state["data_pnl"] = build_executive_pnl(journal_df,ar_df,ap_df,pnl_start,pnl_end)
        except Exception as exc:
            st.error(str(exc))
    pnl = st.session_state.get("data_pnl")
    if pnl:
        a,b,c = st.columns(3)
        a.metric("Doanh thu thuần",f"{pnl['Doanh thu thuần']:,.0f} đ")
        b.metric("Giá vốn",f"{pnl['Giá vốn']:,.0f} đ")
        c.metric("Lợi nhuận ước tính",f"{pnl['Lợi nhuận ước tính']:,.0f} đ")
        st.dataframe(pd.DataFrame([{"Chỉ tiêu":k,"Giá trị":v} for k,v in pnl.items() if isinstance(v,(int,float))]),use_container_width=True,hide_index=True)
        left,right = st.columns(2)
        with left:
            st.markdown("**Top 5 phải thu**")
            st.dataframe(pd.DataFrame(pnl["Top 5 phải thu"]),use_container_width=True,hide_index=True)
        with right:
            st.markdown("**Top 5 phải trả**")
            st.dataframe(pd.DataFrame(pnl["Top 5 phải trả"]),use_container_width=True,hide_index=True)
        st.caption(pnl["Ghi chú"])
        export("⬇ Tải báo cáo P&L",{"P&L":pd.DataFrame([{"Chỉ tiêu":k,"Giá trị":v} for k,v in pnl.items() if isinstance(v,(int,float))]),"Top_phai_thu":pd.DataFrame(pnl["Top 5 phải thu"]),"Top_phai_tra":pd.DataFrame(pnl["Top 5 phải trả"])},"Bao_cao_PnL_Quan_Tri.xlsx","pnl_export")

        if summary:
            try:
                pdf_data = management_report_pdf(summary,pnl)
                download_sample_bytes("⬇ Tải Dashboard quản trị 1 trang (PDF)",pdf_data,
                    "Dashboard_Quan_Tri_Dong_Tien.pdf","application/pdf","cashflow_pdf_export")
            except Exception as exc:
                st.warning(f"Chưa tạo được PDF dashboard: {exc}")

with tabs[7]:
    st.subheader("Cố vấn nghiệp vụ & pháp lý thuế có kiểm chứng")
    download_sample_bytes("⬇ Tải câu hỏi mẫu", sample_legal_questions(), "Cau_hoi_mau_co_van_thue.txt", "text/plain; charset=utf-8", "sample_fr07_questions")
    st.warning("Chỉ tài liệu có metadata hiệu lực và được người quản trị xác nhận mới được tìm kiếm. Hệ thống không tự xác minh hiệu lực của file tải lên.")
    st.caption("Câu hỏi gửi đến AI (nếu đã cấu hình) chỉ được che một số chuỗi định danh bằng quy tắc tự động. Hãy tự bỏ tên khách hàng, số hợp đồng, địa chỉ và dữ liệu mật trước khi tra cứu.")
    legal_admin_ids = {x.strip() for x in get_setting("LEGAL_ADMIN_USER_IDS","").split(",") if x.strip()}
    is_legal_admin = bool(current_user and current_user.id in legal_admin_ids)
    if is_legal_admin:
      with st.expander("Nạp văn bản pháp luật chính thức vào kho dùng chung"):
        legal_file = st.file_uploader("PDF có text / DOCX / TXT / MD",["pdf","docx","txt","md"],key=upload_key("legal_file"))
        number = st.text_input("Số hiệu văn bản",key="legal_number")
        title = st.text_input("Tên văn bản",key="legal_title")
        article = st.text_input("Điều/khoản (nếu biết)",key="legal_article")
        effective_from = st.date_input("Ngày hiệu lực từ",value=date(2026,1,1),key="legal_from")
        effective_to = st.date_input("Ngày hết hiệu lực (để trống nếu chưa xác định)",value=None,key="legal_to")
        status = st.selectbox("Trạng thái do người quản trị xác nhận",["Chưa xác minh","Còn hiệu lực","Hết hiệu lực"])
        source_url = st.text_input("URL nguồn chính thức")
        if st.button("Xuất bản văn bản vào kho dùng chung",disabled=not(legal_file and number and title and source_url)):
            if not admin_client:
                st.error("Cần cấu hình SUPABASE_SECRET_KEY ở phía server và chạy schema.sql để lưu kho văn bản dùng chung.")
            else:
                try:
                    metadata={"number":number,"title":title,"article":article,"effective_from":effective_from.isoformat(),"effective_to":effective_to.isoformat() if effective_to else None,"status":status,"source_url":source_url}
                    publish_legal_document(admin_client,legal_file.name,legal_file.getvalue(),metadata,current_user.id)
                    st.session_state["legal_documents"] = load_published_legal_documents(admin_client)
                    st.success(f"Đã xuất bản {number} vào kho pháp lý dùng chung.")
                except Exception as exc:
                    st.error(f"Không xuất bản được văn bản: {exc}")
    else:
        st.info("Nạp văn bản vào kho chỉ dành cho tài khoản quản trị được cấu hình trong LEGAL_ADMIN_USER_IDS.")
    docs = st.session_state.get("legal_documents",[])
    legal_index = []
    if docs:
        try:
            legal_index = build_legal_index(docs)
            st.caption(f"Đã nạp {len(docs)} văn bản từ kho pháp lý dùng chung; {len(legal_index)} đoạn đủ điều kiện tìm kiếm theo metadata.")
        except Exception as exc:
            st.error(f"Không tạo được kho tra cứu: {exc}")
    question = st.text_area("Câu hỏi nghiệp vụ kế toán/thuế",placeholder="Ví dụ: Hồ sơ cần có khi thanh toán chi phí vận chuyển?")
    if st.button("Tra cứu căn cứ pháp lý",type="primary",disabled=not question.strip()):
        masked_question = mask_sensitive(question)
        matches = retrieve_legal_context(legal_index,masked_question,5)
        st.session_state["result_legal_matches"] = matches
        api_key = get_setting("OPENAI_API_KEY","")
        if matches and api_key:
            try:
                st.session_state["result_legal_answer"] = answer_with_openai(masked_question,matches,api_key,get_setting("OPENAI_MODEL","gpt-4o-mini"))
            except Exception as exc:
                st.session_state["result_legal_answer"] = f"Không gọi được mô hình AI: {exc}. Chỉ hiển thị trích đoạn nguồn."
        elif not matches:
            st.session_state["result_legal_answer"] = "Không tìm thấy căn cứ phù hợp trong kho tài liệu đã nạp. Chưa thể kết luận."
        else:
            st.session_state["result_legal_answer"] = "Chưa cấu hình OPENAI_API_KEY. Chỉ hiển thị trích đoạn để người dùng tự đối chiếu, không tự tạo kết luận."
    answer = st.session_state.get("result_legal_answer")
    matches = st.session_state.get("result_legal_matches",[])
    if answer:
        st.markdown("**Kết quả tra cứu**")
        st.write(answer)
        if matches:
            st.markdown("**Đoạn nguồn được truy xuất**")
            for i,item in enumerate(matches,1):
                with st.expander(f"[{i}] {item['number']} · {item['title']} · {item['chunk_id']}"):
                    st.write(item["text"])
                    st.caption(f"Hiệu lực từ {item['effective_from']} · Điểm tương đồng {item['score']:.3f}")
                    st.link_button("Mở văn bản nguồn",item["source_url"])
            checklist = make_checklist_xlsx(question,answer,matches)
            export_bytes("⬇ Tải Phiếu Checklist Chứng từ / Căn cứ pháp lý",checklist,"Phieu_Checklist_Chung_Tu.xlsx","legal_checklist")
    st.markdown("**Danh mục văn bản tham chiếu đã rà soát sơ bộ**")
    st.markdown("- [Thông tư 99/2025/TT-BTC – hiệu lực từ 01/01/2026](https://vbpl.moj.gov.vn/bocongthuong/Pages/vbpq-van-ban-goc.aspx?ItemID=187356)")
    st.markdown("- [Thông tư 133/2016/TT-BTC – chế độ kế toán doanh nghiệp nhỏ và vừa](https://vanban.chinhphu.vn/default.aspx?docid=186679&hiddenHeaderFooter=1&pageid=27160)")
    st.markdown("- [Nghị định 254/2026/NĐ-CP – hóa đơn điện tử, hiệu lực từ 01/07/2026](https://vanban.chinhphu.vn/?docid=218689&pageid=27160&typegroupid=4)")
    st.markdown("- [Luật Quản lý thuế 108/2025/QH15 – hiệu lực từ 01/07/2026](https://vanban.chinhphu.vn/?docid=216541&orggroupid=1&pageid=27160)")
    st.markdown("- [Nghị định 252/2026/NĐ-CP – hướng dẫn Luật Quản lý thuế](https://vanban.chinhphu.vn/?docid=218690&orggroupid=2&pageid=27160)")
    st.markdown("- [Thông tư 91/2026/TT-BTC – hướng dẫn hóa đơn điện tử](https://vanban.chinhphu.vn/?docid=219006&pageid=27160)")
    st.markdown("- [Luật Thuế TNDN 67/2025/QH15](https://vanban.chinhphu.vn/?classid=1&docid=214607&pageid=27160&typ=)")
    st.markdown("- [Luật 09/2026/QH16 – sửa đổi các luật thuế](https://vanban.chinhphu.vn/?docid=218095&pageid=27160&typegroupid=3)")

with tabs[8]:
    st.subheader("Tài khoản, gói cước & thanh toán VietQR")
    download_sample_excel("⬇ Tải bảng mẫu gói dịch vụ và MST", "fr08", "sample_fr08_plans")
    st.dataframe(pd.DataFrame([
        {"Gói":"Khách vãng lai","Giá":"0 VNĐ","Quyền lợi":"Xem toàn bộ thống kê · tải tối đa 50 dòng đầu mỗi sheet"},
        {"Gói":"Kế toán Nội bộ","Giá":"69.000 VNĐ/tháng","Quyền lợi":"Không giới hạn dòng · 01 MST"},
        {"Gói":"Kế toán Dịch vụ","Giá":"249.000 VNĐ/tháng","Quyền lợi":"Không giới hạn dòng · nhiều MST"},
    ]),hide_index=True,use_container_width=True)
    if client is None:
        st.warning("Chưa có SUPABASE_URL và SUPABASE_PUBLISHABLE_KEY. Tạo project Supabase, chạy supabase/schema.sql rồi cấu hình biến môi trường.")
    elif current_user:
        st.write(f"Tài khoản: **{current_user.email or current_user.id}**")
        if subscription:
            st.success(f"Gói đang hoạt động: {PLAN_LABELS.get(subscription['plan'],subscription['plan'])} · Hết hạn: {subscription['expires_at']}")
            current_codes = subscription.get("tax_codes") or []
            code_text = st.text_input("Mã số thuế được quản lý (phân tách bằng dấu phẩy)",value=", ".join(current_codes))
            if st.button("Lưu danh sách mã số thuế"):
                try:
                    codes = [x.strip() for x in code_text.split(",") if x.strip()]
                    updated = client.rpc("set_subscription_tax_codes",{"p_tax_codes":codes}).execute()
                    st.success("Đã cập nhật danh sách mã số thuế.")
                    st.rerun()
                except Exception as exc:
                    st.error(f"Không cập nhật được mã số thuế: {exc}")
        else:
            st.info("Chưa có thuê bao trả phí đang hoạt động.")
        if st.button("Đăng xuất"):
            st.session_state.pop("auth_session",None)
            st.session_state.pop("active_subscription",None)
            st.rerun()
        plan_choice = st.selectbox("Chọn gói muốn thanh toán",["internal","service"],format_func=lambda x:PLAN_LABELS[x])
        if st.button("Tạo đơn VietQR"):
            try:
                order = client.rpc("create_payment_order",{"p_plan":plan_choice}).execute().data
                order = order[0] if isinstance(order,list) else order
                st.session_state["last_payment_order"] = order
            except Exception as exc:
                st.error(f"Không tạo được đơn thanh toán. Kiểm tra schema Supabase và quyền RPC: {exc}")
        order = st.session_state.get("last_payment_order")
        if order:
            st.metric("Số tiền cần thanh toán",f"{int(order['amount']):,} VNĐ")
            st.code(order["payment_code"])
            bank = get_setting("PAYMENT_BANK","")
            account = get_setting("PAYMENT_ACCOUNT","")
            holder = get_setting("PAYMENT_ACCOUNT_HOLDER","")
            require_sevqr = get_setting("PAYMENT_REQUIRE_SEVQR","false").lower()=="true"
            if bank and account:
                qr_url = build_sepay_qr_url(bank,account,int(order["amount"]),order["payment_code"],holder,require_sevqr)
                st.image(qr_url,caption="Quét mã bằng ứng dụng ngân hàng. Giữ nguyên nội dung chuyển khoản.",width=280)
                st.caption("Đơn hết hạn sau 30 phút. Khi webhook SePay xác nhận đúng mã và số tiền, hệ thống kích hoạt gói 30 ngày.")
            else:
                st.warning("Chưa cấu hình PAYMENT_BANK, PAYMENT_ACCOUNT và PAYMENT_ACCOUNT_HOLDER; chưa thể tạo QR thật.")
            payment_status_widget(client,order["payment_code"])
    else:
        login_tab,signup_tab = st.tabs(["Đăng nhập","Tạo tài khoản"])
        with login_tab:
            with st.form("login_form"):
                email = st.text_input("Email")
                password = st.text_input("Mật khẩu",type="password")
                submitted = st.form_submit_button("Đăng nhập")
            if submitted:
                try:
                    response = sign_in(client,email,password)
                    save_session(response.session)
                    st.success("Đăng nhập thành công.")
                    st.rerun()
                except Exception as exc:
                    st.error(f"Không đăng nhập được: {exc}")
        with signup_tab:
            with st.form("signup_form"):
                new_email = st.text_input("Email đăng ký")
                new_password = st.text_input("Mật khẩu",type="password")
                full_name = st.text_input("Họ tên")
                submitted = st.form_submit_button("Tạo tài khoản")
            if submitted:
                try:
                    response = client.auth.sign_up({"email":new_email,"password":new_password,"options":{"data":{"full_name":full_name}}})
                    if response.session:
                        save_session(response.session)
                        st.success("Tạo tài khoản và đăng nhập thành công.")
                        st.rerun()
                    else:
                        st.success("Đã gửi email xác thực. Vui lòng xác nhận email rồi đăng nhập.")
                except Exception as exc:
                    st.error(f"Không tạo được tài khoản: {exc}")
    st.caption("Webhook thanh toán chạy độc lập bằng lệnh uvicorn billing_api:app. Không đưa Supabase Secret Key hoặc SePay API Key vào giao diện Streamlit.")



with tabs[9]:
    st.subheader("AI OCR chứng từ PDF/ảnh & mapping đối chiếu đa nguồn")
    st.caption("Đọc hóa đơn, sao kê, ủy nhiệm chi và ảnh chụp nội dung chuyển tiền; chuẩn hóa dữ liệu rồi gợi ý ghép chứng từ.")
    st.warning(
        "Quyền riêng tư: khi chạy OCR, nội dung PDF/ảnh được gửi trực tiếp tới OpenAI API theo tài khoản API đã cấu hình. "
        "Hệ thống không ghi file xuống ổ đĩa, không tạo File ID lưu trữ trên OpenAI và không lưu chứng từ kế toán vào Supabase. "
        "Chỉ tiếp tục khi bạn được phép chia sẻ chứng từ cho nhà cung cấp AI."
    )
    api_key = get_setting("OPENAI_API_KEY", "")
    model_name = get_setting("OPENAI_MODEL", "gpt-4o-mini")
    if not api_key:
        st.info("Chưa cấu hình OPENAI_API_KEY trong Streamlit Secrets. Có thể dùng phần đối chiếu Excel phía dưới; OCR AI sẽ mở khi có khóa API.")
    consent = st.checkbox(
        "Tôi đồng ý gửi nội dung các tệp được chọn tới OpenAI API để nhận diện chứng từ.",
        value=False, key="ai_document_external_consent"
    )
    detail = st.radio(
        "Mức đọc trang PDF", ["high", "low"], horizontal=True, index=0,
        help="High phù hợp chứng từ scan/chữ nhỏ nhưng có thể tốn token hơn."
    )
    ai_files = st.file_uploader(
        "1. Tải nhiều chứng từ PDF/ảnh (hóa đơn, sao kê, UNC, ảnh chuyển tiền)",
        type=["pdf", "png", "jpg", "jpeg"], accept_multiple_files=True,
        key=upload_key("ai_documents")
    )
    bank_file = st.file_uploader(
        "2. Bảng kê ngân hàng Excel/CSV (khuyến nghị nếu có dữ liệu điện tử)",
        type=["xlsx", "xls", "csv"], key=upload_key("ai_bank_statement")
    )
    if st.button(
        "🤖 Đọc chứng từ bằng AI",
        type="primary", disabled=not (api_key and consent and ai_files)
    ):
        if len(ai_files) > 15:
            st.error("Mỗi lượt tối đa 15 tệp. Hãy chia nhỏ lô chứng từ.")
        else:
            extracted = []
            failures = []
            with st.spinner("AI đang đọc chứng từ và trích xuất dữ liệu..."):
                for uploaded in ai_files:
                    try:
                        extracted.append(extract_document(
                            uploaded.name, uploaded.getvalue(), api_key, model_name, detail
                        ))
                    except Exception as exc:
                        failures.append(f"{uploaded.name}: {exc}")
            inv_df, tx_df, summary_df = documents_to_dataframes(extracted)
            if bank_file:
                try:
                    bank_df = read_upload(bank_file, [
                        "Ngày giao dịch", "Số tiền ghi Nợ", "Số tiền ghi Có", "Nội dung giao dịch"
                    ])
                    bank_tx = normalize_bank_statement(bank_df)
                    if not bank_tx.empty:
                        tx_df = pd.concat([tx_df, bank_tx], ignore_index=True)
                except Exception as exc:
                    failures.append(f"Bảng kê ngân hàng: {exc}")
            st.session_state["ai_document_summary"] = summary_df
            st.session_state["ai_invoice_records"] = inv_df
            st.session_state["ai_transaction_records"] = tx_df
            st.session_state["ai_document_failures"] = failures
            st.session_state.pop("ai_document_mapping", None)
            st.success(f"Đã đọc thành công {len(extracted)}/{len(ai_files)} tệp.")
    failures = st.session_state.get("ai_document_failures", [])
    for failure in failures:
        st.warning(failure)
    summary_df = st.session_state.get("ai_document_summary")
    inv_df = st.session_state.get("ai_invoice_records")
    tx_df = st.session_state.get("ai_transaction_records")
    if summary_df is not None:
        st.markdown("**Kết quả nhận diện từng tệp**")
        st.dataframe(summary_df, use_container_width=True, hide_index=True)
        if inv_df is not None and not inv_df.empty:
            st.markdown("**Hóa đơn trích xuất – kiểm tra/sửa trước khi mapping**")
            inv_edited = st.data_editor(inv_df, use_container_width=True, hide_index=True, key="ai_invoice_editor")
            st.session_state["ai_invoice_records"] = inv_edited
        if tx_df is not None and not tx_df.empty:
            st.markdown("**Giao dịch thanh toán trích xuất – kiểm tra/sửa trước khi mapping**")
            tx_edited = st.data_editor(tx_df, use_container_width=True, hide_index=True, key="ai_transaction_editor")
            st.session_state["ai_transaction_records"] = tx_edited
        st.caption("Các trường có độ tin cậy thấp hoặc bằng chứng OCR không rõ phải được kiểm tra trên chứng từ gốc. AI không tự xác nhận tính hợp lệ pháp lý/chữ ký số.")
        if inv_df is not None and not inv_df.empty and tx_df is not None and not tx_df.empty:
            if st.button("🔗 Mapping hóa đơn với sao kê/UNC", key="ai_run_mapping"):
                try:
                    invoice_result, mapping_result, transaction_result = map_invoice_payments(
                        st.session_state["ai_invoice_records"],
                        st.session_state["ai_transaction_records"]
                    )
                    if not mapping_result.empty:
                        mapping_result["Kế toán xác nhận"] = False
                    st.session_state["ai_invoice_payment_result"] = invoice_result
                    st.session_state["ai_document_mapping"] = mapping_result
                    st.session_state["ai_transaction_payment_result"] = transaction_result
                    st.success("Đã tạo gợi ý mapping. Chưa ghi sổ và chưa xác nhận thanh toán.")
                except Exception as exc:
                    st.error(f"Không mapping được: {exc}")
    invoice_payment_result = st.session_state.get("ai_invoice_payment_result")
    mapping_result = st.session_state.get("ai_document_mapping")
    transaction_payment_result = st.session_state.get("ai_transaction_payment_result")
    if invoice_payment_result is not None:
        st.markdown("**Tình trạng thanh toán theo hóa đơn**")
        st.dataframe(invoice_payment_result, use_container_width=True, hide_index=True)
    if mapping_result is not None:
        if mapping_result.empty:
            st.info("Chưa tìm thấy giao dịch đủ căn cứ để mapping tự động.")
        else:
            st.markdown("**Các cặp mapping đề xuất – kế toán xác nhận thủ công**")
            reviewed = st.data_editor(
                mapping_result, use_container_width=True, hide_index=True,
                key="ai_mapping_editor",
                column_config={"Kế toán xác nhận": st.column_config.CheckboxColumn(
                    "Kế toán xác nhận", help="Chỉ đánh dấu sau khi đã soát chứng từ gốc."
                )}
            )
            if st.button("✓ Ghi nhận xác nhận trong phiên", key="ai_confirm_mapping"):
                reviewed.loc[reviewed["Kế toán xác nhận"].fillna(False), "Trạng thái"] = "Đã xác nhận bởi kế toán"
                st.session_state["ai_document_mapping"] = reviewed
                st.success("Đã ghi nhận xác nhận trong phiên hiện tại; chưa ghi dữ liệu vào sổ kế toán.")
    if transaction_payment_result is not None:
        with st.expander("Chi tiết phân bổ từng giao dịch"):
            st.dataframe(transaction_payment_result, use_container_width=True, hide_index=True)
    if invoice_payment_result is not None:
        export(
            "⬇ Tải báo cáo OCR & mapping",
            {
                "Hoa_don": invoice_payment_result,
                "Mapping": st.session_state.get("ai_document_mapping", pd.DataFrame()),
                "Giao_dich": transaction_payment_result if transaction_payment_result is not None else pd.DataFrame(),
                "Nhan_dien": summary_df if summary_df is not None else pd.DataFrame()
            },
            "Bao_cao_AI_OCR_Mapping.xlsx", "ai_document_export"
        )

    st.divider()
    st.markdown("**Đối chiếu hai bảng kê hóa đơn Excel**")
    st.caption("Dùng cho bảng kê quản trị mua vào và danh sách hóa đơn tải từ cổng Thuế. Hệ thống gộp nhiều dòng thuế suất của cùng hóa đơn trước khi so sánh.")
    c1, c2 = st.columns(2)
    with c1:
        register_a = st.file_uploader(
            "Bảng kê hóa đơn quản trị", type=["xlsx", "xls", "csv"],
            key=upload_key("ai_register_bookkeeping")
        )
    with c2:
        register_b = st.file_uploader(
            "Danh sách hóa đơn từ cổng Thuế", type=["xlsx", "xls", "csv"],
            key=upload_key("ai_register_tax_portal")
        )
    register_epsilon = st.number_input(
        "Sai số làm tròn cho phép (VNĐ)", min_value=0, max_value=10000,
        value=2, step=1, key="ai_register_epsilon"
    )
    if st.button("📑 Đối chiếu hai bảng kê", disabled=not (register_a and register_b)):
        try:
            left_df = read_upload(register_a, ["Số hóa đơn", "Mã số thuế người bán", "Giá trị HHDV"])
            right_df = read_upload(register_b, ["Số hóa đơn", "MST người bán", "Tổng tiền"])
            register_result, register_counts = reconcile_invoice_registers(
                left_df, right_df, int(register_epsilon)
            )
            st.session_state["ai_register_result"] = register_result
            st.session_state["ai_register_counts"] = register_counts
            st.success("Đã đối chiếu hai bảng kê sau khi chuẩn hóa số hóa đơn và gộp dòng thuế suất.")
        except Exception as exc:
            st.error(f"Không đối chiếu được: {exc}")
    register_result = st.session_state.get("ai_register_result")
    register_counts = st.session_state.get("ai_register_counts")
    if register_result is not None:
        st.markdown("**Tổng hợp kết quả đối chiếu**")
        cols = st.columns(4)
        for col, label in zip(cols, ["Khớp hoàn toàn", "Nghi vấn sai MST/chi nhánh", "Lệch giá trị", "Thiếu ở danh sách hóa đơn"]):
            col.metric(label, int((register_result["Trạng thái"] == label).sum()))
        st.dataframe(register_result, use_container_width=True, hide_index=True)
        if register_counts:
            st.caption("Số liệu tính trên hóa đơn đã gộp theo số hóa đơn + MST + ký hiệu; không tính từng dòng thuế suất là một hóa đơn.")
        export(
            "⬇ Tải kết quả đối chiếu bảng kê",
            {"Doi_chieu": register_result, "Tong_hop": pd.DataFrame(
                [{"Chỉ tiêu": key, "Số lượng": value} for key, value in register_counts.items()]
            )},
            "Bao_cao_doi_chieu_bang_ke_hoa_don.xlsx", "ai_register_export"
        )

st.divider()
st.caption("Trợ lý Kế toán SME · MVP mở rộng theo SRS v1.0 · Kế toán cần kiểm tra kết quả trước khi ghi sổ/kê khai.")
