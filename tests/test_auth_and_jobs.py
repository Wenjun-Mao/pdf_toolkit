from __future__ import annotations

from pathlib import Path

from pdf_toolkit.models import Job, JobStatus
from pdf_toolkit.web.rendering import serialize_job


def test_serialize_job_marks_scan_awaiting_settings_separately() -> None:
    scan_payload = serialize_job(
        Job(
            id="scan-job",
            tool_name="scan-cleanup-analysis",
            display_name="Analyze Scan Cleanup",
            status=JobStatus.AWAITING_SETTINGS.value,
        )
    )
    mixed_payload = serialize_job(
        Job(
            id="mixed-job",
            tool_name="mixed-to-pdf",
            display_name="Mixed to PDF",
            status=JobStatus.AWAITING_SETTINGS.value,
        )
    )

    assert scan_payload["awaiting_settings"] is True
    assert scan_payload["awaiting_scan_settings"] is True
    assert mixed_payload["awaiting_settings"] is True
    assert mixed_payload["awaiting_scan_settings"] is False


def test_login_required_redirects_to_login(app_client) -> None:
    unauthenticated = app_client.__class__(app_client.app)
    response = unauthenticated.get("/", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_login_optional_allows_home_access(open_app_client) -> None:
    response = open_app_client.get("/")
    assert response.status_code == 200
    assert "PDF Kit" in response.text


def test_login_optional_redirects_login_page_to_home(open_app_client) -> None:
    response = open_app_client.get("/login", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/"


def test_merge_submission_completes_without_login(open_app_client, sample_merge_pdfs: list[Path]) -> None:
    files = [
        ("files", (sample_merge_pdfs[0].name, sample_merge_pdfs[0].read_bytes(), "application/pdf")),
        ("files", (sample_merge_pdfs[1].name, sample_merge_pdfs[1].read_bytes(), "application/pdf")),
    ]
    response = open_app_client.post("/tools/merge/submit", files=files)

    assert response.status_code == 200
    assert "Download merged.pdf" in response.text


def test_merge_submission_completes_inline(app_client, sample_merge_pdfs: list[Path]) -> None:
    files = [
        ("files", (sample_merge_pdfs[0].name, sample_merge_pdfs[0].read_bytes(), "application/pdf")),
        ("files", (sample_merge_pdfs[1].name, sample_merge_pdfs[1].read_bytes(), "application/pdf")),
    ]
    response = app_client.post("/tools/merge/submit", files=files)

    assert response.status_code == 200
    assert "Download merged.pdf" in response.text


def test_images_to_pdf_submission_completes_inline(app_client, sample_image_inputs: list[Path]) -> None:
    files = [
        (
            "files",
            (
                image_path.name,
                image_path.read_bytes(),
                "image/jpeg" if image_path.suffix.lower() in {".jpg", ".jpeg"} else "image/png",
            ),
        )
        for image_path in sample_image_inputs
    ]
    response = app_client.post(
        "/tools/images-to-pdf/submit",
        data={
            "fallback_dpi": "300",
            "jpeg_quality": "95",
            "page_size": "letter",
            "margin_mm": "12.7",
            "placement": "fit",
        },
        files=files,
    )

    assert response.status_code == 200
    assert "Download images.pdf" in response.text


def test_id_halves_to_pdf_submission_completes_inline(app_client, sample_image_inputs: list[Path]) -> None:
    top_image = sample_image_inputs[0]
    bottom_image = sample_image_inputs[1]
    files = [
        (
            "top_image",
            (
                top_image.name,
                top_image.read_bytes(),
                "image/jpeg" if top_image.suffix.lower() in {".jpg", ".jpeg"} else "image/png",
            ),
        ),
        (
            "bottom_image",
            (
                bottom_image.name,
                bottom_image.read_bytes(),
                "image/jpeg" if bottom_image.suffix.lower() in {".jpg", ".jpeg"} else "image/png",
            ),
        ),
    ]
    response = app_client.post(
        "/tools/id-halves-to-pdf/submit",
        data={
            "fallback_dpi": "300",
            "jpeg_quality": "95",
        },
        files=files,
    )

    assert response.status_code == 200
    assert "Download id-halves.pdf" in response.text
