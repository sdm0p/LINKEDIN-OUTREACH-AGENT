import io

from fastapi.testclient import TestClient

from app.main import app


def _minimal_pdf() -> bytes:
    """Bytes starting with %PDF — enough for the upload validator; extraction
    itself is not exercised here (that path needs the LLM)."""
    return b"%PDF-1.4 minimal test file"


def test_health():
    with TestClient(app) as client:
        res = client.get("/api/health")
        assert res.status_code == 200
        assert res.json() == {"ok": True}


def test_resume_state_empty(tmp_path, monkeypatch):
    monkeypatch.setattr("app.config.settings.data_dir", tmp_path)
    with TestClient(app) as client:
        res = client.get("/api/resume")
        assert res.status_code == 200
        body = res.json()
        assert body["has_resume"] is False
        assert body["my_yoe"] == 0


def test_upload_rejects_non_pdf(tmp_path, monkeypatch):
    monkeypatch.setattr("app.config.settings.data_dir", tmp_path)
    with TestClient(app) as client:
        res = client.post(
            "/api/resume/upload",
            files={"file": ("resume.txt", io.BytesIO(b"not a pdf"), "text/plain")},
        )
        assert res.status_code == 400
        assert "PDF" in res.json()["detail"]


def test_upload_rejects_fake_pdf_before_llm(tmp_path, monkeypatch):
    monkeypatch.setattr("app.config.settings.data_dir", tmp_path)
    with TestClient(app) as client:
        res = client.post(
            "/api/resume/upload",
            files={"file": ("resume.pdf", io.BytesIO(b"garbage"), "application/pdf")},
        )
        assert res.status_code == 400
        assert "does not look like a valid PDF" in res.json()["detail"]


def test_reparse_without_resume_is_400(tmp_path, monkeypatch):
    monkeypatch.setattr("app.config.settings.data_dir", tmp_path)
    with TestClient(app) as client:
        res = client.post("/api/resume/reparse")
        assert res.status_code == 400


def test_settings_reports_provider(tmp_path, monkeypatch):
    monkeypatch.setattr("app.config.settings.data_dir", tmp_path)
    monkeypatch.setattr("app.config.settings.gemini_api_key", None)
    # Deterministic prerequisites regardless of the host's Docker state.
    monkeypatch.setattr(
        "app.routers.settings.prerequisites",
        lambda: {"docker_installed": False, "session_dir_present": False},
    )
    with TestClient(app) as client:
        res = client.get("/api/settings")
        assert res.status_code == 200
        body = res.json()
        assert body["llm"]["provider"] == "gemini"
        assert body["llm"]["configured"] is False
        assert body["linkedin"]["status"] == "unavailable"
