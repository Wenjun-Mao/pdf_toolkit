from __future__ import annotations

from pathlib import Path

import fitz
import pytest
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw

from pdf_toolkit.settings import get_settings
from pdf_toolkit.web.app import create_app


def _build_client(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    require_login: bool,
) -> TestClient:
    data_dir = tmp_path / "data"
    monkeypatch.setenv("PDFKIT_DATA_DIR", str(data_dir))
    monkeypatch.setenv("PDFKIT_DATABASE_URL", f"sqlite:///{(data_dir / 'test.db').as_posix()}")
    monkeypatch.setenv("PDFKIT_REDIS_URL", "redis://localhost:6379/15")
    monkeypatch.setenv("PDFKIT_RUN_JOBS_INLINE", "true")
    monkeypatch.setenv("PDFKIT_ADMIN_USERNAME", "admin")
    monkeypatch.setenv("PDFKIT_ADMIN_PASSWORD", "test-password")
    monkeypatch.setenv("PDFKIT_SESSION_SECRET", "test-session-secret")
    monkeypatch.setenv("PDFKIT_REQUIRE_LOGIN", str(require_login).lower())
    get_settings.cache_clear()
    return TestClient(create_app())


@pytest.fixture
def repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


@pytest.fixture
def sample_merge_pdfs(tmp_path: Path) -> list[Path]:
    first_pdf = tmp_path / "first.pdf"
    second_pdf = tmp_path / "second.pdf"

    first_doc = fitz.open()
    page = first_doc.new_page()
    page.insert_text((72, 72), "First PDF / Page 1")
    page = first_doc.new_page()
    page.insert_text((72, 72), "First PDF / Page 2")
    first_doc.save(first_pdf)
    first_doc.close()

    second_doc = fitz.open()
    page = second_doc.new_page()
    page.insert_text((72, 72), "Second PDF / Page 1")
    second_doc.save(second_pdf)
    second_doc.close()

    return [first_pdf, second_pdf]


@pytest.fixture
def sample_scan_pdf(tmp_path: Path, sample_image_inputs: list[Path]) -> Path:
    output_path = tmp_path / "scan-source.pdf"
    document = fitz.open()
    page = document.new_page(width=300, height=225)
    page.insert_image(page.rect, filename=str(sample_image_inputs[1]))
    document.save(output_path)
    document.close()
    return output_path


@pytest.fixture
def sample_image_inputs(tmp_path: Path) -> list[Path]:
    rgb_path = tmp_path / "page-1.jpg"
    with Image.new("RGB", (600, 900), color=(240, 245, 255)) as rgb_image:
        drawer = ImageDraw.Draw(rgb_image)
        drawer.rectangle((80, 80, 520, 820), outline=(25, 60, 120), width=10)
        drawer.text((120, 140), "Image Page 1", fill=(20, 20, 20))
        rgb_image.save(rgb_path, format="JPEG", quality=95, dpi=(300, 300))

    rgba_path = tmp_path / "page-2.png"
    with Image.new("RGBA", (400, 300), color=(255, 255, 255, 0)) as rgba_image:
        drawer = ImageDraw.Draw(rgba_image)
        drawer.rounded_rectangle((20, 20, 380, 280), radius=32, fill=(220, 235, 255, 255))
        drawer.text((95, 130), "Transparent PNG", fill=(0, 0, 0, 255))
        rgba_image.save(rgba_path, format="PNG")

    return [rgb_path, rgba_path]


@pytest.fixture
def app_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    client = _build_client(tmp_path, monkeypatch, require_login=True)
    response = client.post(
        "/login",
        data={"username": "admin", "password": "test-password"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    yield client
    get_settings.cache_clear()


@pytest.fixture
def open_app_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    client = _build_client(tmp_path, monkeypatch, require_login=False)
    yield client
    get_settings.cache_clear()
