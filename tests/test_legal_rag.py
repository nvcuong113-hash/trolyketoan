from datetime import date
from legal_rag import build_legal_index, retrieve_legal_context
from legal_storage import trusted_official_url


def test_legal_rag_only_uses_current_official_documents():
    docs=[
        {"filename":"active.txt","content":"Doanh nghiệp phải lưu trữ chứng từ kế toán theo quy định.","metadata":{
            "number":"TT-TEST","title":"Văn bản kiểm thử","article":"Điều 1","effective_from":"2026-01-01",
            "effective_to":None,"status":"Còn hiệu lực","source_url":"https://vanban.chinhphu.vn/test"}},
        {"filename":"expired.txt","content":"Không sử dụng văn bản đã hết hiệu lực.","metadata":{
            "number":"OLD-TEST","title":"Văn bản hết hiệu lực","article":"Điều 2","effective_from":"2020-01-01",
            "effective_to":"2021-01-01","status":"Còn hiệu lực","source_url":"https://vanban.chinhphu.vn/old"}},
        {"filename":"fake.txt","content":"Nguồn giả không được dùng.","metadata":{
            "number":"FAKE","title":"Nguồn không chính thức","article":"","effective_from":"2026-01-01",
            "effective_to":None,"status":"Còn hiệu lực","source_url":"https://example.com/fake"}},
    ]
    index=build_legal_index(docs)
    assert index and all(x["number"]=="TT-TEST" for x in index)
    results=retrieve_legal_context(index,"lưu trữ chứng từ kế toán")
    assert results and results[0]["number"]=="TT-TEST"


def test_legal_corpus_accepts_only_official_https_domains():
    assert trusted_official_url("https://vanban.chinhphu.vn/doc")
    assert trusted_official_url("https://vbpl.moj.gov.vn/doc")
    assert not trusted_official_url("http://vanban.chinhphu.vn/doc")
    assert not trusted_official_url("https://example.com/law")
