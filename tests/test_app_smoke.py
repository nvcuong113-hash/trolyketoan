from pathlib import Path
from streamlit.testing.v1 import AppTest


def test_streamlit_app_smoke_without_secrets():
    app_path=Path(__file__).resolve().parents[1]/"app.py"
    at=AppTest.from_file(str(app_path),default_timeout=30).run()
    assert not at.exception
