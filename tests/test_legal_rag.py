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


def test_legal_storage_rejects_invalid_effective_date_order():
    from legal_storage import publish_legal_document
    class NeverUpload:
        class Storage:
            def from_(self, bucket):
                raise AssertionError("Should validate metadata before upload")
        storage=Storage()
    try:
        publish_legal_document(NeverUpload(),"law.txt",b"law",{
            "number":"TT-TEST","title":"Test","effective_from":"2026-05-01",
            "effective_to":"2026-04-30","status":"Chưa xác minh",
            "source_url":"https://vanban.chinhphu.vn/test"
        },"00000000-0000-0000-0000-000000000000")
        assert False
    except ValueError as exc:
        assert "không được trước" in str(exc)



def test_legal_answer_uses_gemini_and_only_supplied_sources(monkeypatch):
    import legal_rag

    captured = {}

    def fake_create_interaction(**kwargs):
        captured.update(kwargs)
        return {"output_text": "Kết luận có điều kiện [1]."}

    monkeypatch.setattr(legal_rag, "create_interaction", fake_create_interaction)
    sources = [{
        "number": "TT-TEST", "title": "Văn bản kiểm thử", "article": "Điều 1",
        "text": "Doanh nghiệp lưu chứng từ kế toán.", "source_url": "https://vanban.chinhphu.vn/test"
    }]
    answer = legal_rag.answer_with_gemini(
        "Lưu chứng từ thế nào?", sources, "gemini-key", "gemini-3.5-flash-lite"
    )
    assert answer == "Kết luận có điều kiện [1]."
    assert captured["model"] == "gemini-3.5-flash-lite"
    prompt = captured["input_parts"][0]["text"]
    assert "Chỉ được kết luận dựa trên các trích đoạn nguồn" in prompt
    assert "TT-TEST" in prompt
    assert "https://vanban.chinhphu.vn/test" in prompt


def test_legal_answer_without_sources_does_not_call_gemini(monkeypatch):
    import legal_rag

    def fail_if_called(**kwargs):
        raise AssertionError("Gemini must not be called without retrieved legal sources")

    monkeypatch.setattr(legal_rag, "create_interaction", fail_if_called)
    answer = legal_rag.answer_with_gemini("Question", [], "gemini-key")
    assert "không đưa ra kết luận" in answer
