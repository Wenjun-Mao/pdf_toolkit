from __future__ import annotations

import re
from pathlib import Path

import pdf_toolkit.web.app as web_app
from pdf_toolkit.models import Job, JobStatus
from pdf_toolkit.pdf_ops import analyze_scan_pdf
from pdf_toolkit.jobs import create_job, get_job, update_job_fields
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


def test_scan_cleanup_review_form_exposes_output_controls(
    open_app_client,
    sample_scan_pdf: Path,
    tmp_path: Path,
) -> None:
    analysis = analyze_scan_pdf(sample_scan_pdf, tmp_path / "previews")
    analysis_payload = analysis.to_json()
    for page_payload in analysis_payload["pages"]:
        page_payload["preview_path"] = Path(page_payload["preview_path"]).name
    job = create_job(
        "scan-cleanup-analysis",
        "Analyze Scan Cleanup",
        [sample_scan_pdf],
    )
    update_job_fields(
        job.id,
        status=JobStatus.AWAITING_SETTINGS.value,
        artifact_json={"analysis": analysis_payload},
    )

    response = open_app_client.get(f"/jobs/{job.id}")

    assert response.status_code == 200
    assert 'name="dpi_cap"' in response.text
    assert 'value="300"' in response.text
    assert 'name="jpeg_quality"' in response.text
    assert 'value="92"' in response.text
    assert "What do these settings do?" in response.text
    assert 'class="settings-help-grid"' in response.text
    assert response.text.count('class="settings-help-item"') == 5
    assert "Strength controls how aggressively background tint and noise are neutralized." in response.text
    assert "White Point controls which light pixels become pure white." in response.text
    assert "Contrast boosts local foreground separation." in response.text
    assert "Max DPI caps the render resolution for each page." in response.text
    assert "JPEG Quality controls output compression." in response.text
    assert "Optional Page Overrides" not in response.text
    assert 'name="page_1_strength"' not in response.text


def test_scan_cleanup_partial_embeds_review_when_analysis_is_ready(
    open_app_client,
    sample_scan_pdf: Path,
    tmp_path: Path,
) -> None:
    analysis = analyze_scan_pdf(sample_scan_pdf, tmp_path / "previews")
    analysis_payload = analysis.to_json()
    for page_payload in analysis_payload["pages"]:
        page_payload["preview_path"] = Path(page_payload["preview_path"]).name
    job = create_job(
        "scan-cleanup-analysis",
        "Analyze Scan Cleanup",
        [sample_scan_pdf],
    )
    update_job_fields(
        job.id,
        status=JobStatus.AWAITING_SETTINGS.value,
        artifact_json={"analysis": analysis_payload},
    )

    response = open_app_client.get(
        f"/jobs/{job.id}?partial=1",
        headers={"HX-Request": "true"},
    )

    assert response.status_code == 200
    assert f'id="job-{job.id}"' in response.text
    assert "Review analysis and launch cleanup" in response.text
    assert "Run Cleanup" in response.text
    assert "Preview for page 1" in response.text
    assert "Open Analysis" not in response.text


def test_scan_cleanup_process_submission_stores_output_controls(
    open_app_client,
    sample_scan_pdf: Path,
    tmp_path: Path,
    monkeypatch,
) -> None:
    analysis = analyze_scan_pdf(sample_scan_pdf, tmp_path / "previews")
    analysis_payload = analysis.to_json()
    for page_payload in analysis_payload["pages"]:
        page_payload["preview_path"] = Path(page_payload["preview_path"]).name
    analysis_job = create_job(
        "scan-cleanup-analysis",
        "Analyze Scan Cleanup",
        [sample_scan_pdf],
    )
    update_job_fields(
        analysis_job.id,
        status=JobStatus.AWAITING_SETTINGS.value,
        artifact_json={"analysis": analysis_payload},
    )
    monkeypatch.setattr(web_app, "enqueue_scan_process", lambda job_id, settings: None)

    response = open_app_client.post(
        f"/tools/scan-cleanup/{analysis_job.id}/process",
        data={
            "strength": "0.7",
            "white_point": "244",
            "contrast": "1.1",
            "dpi_cap": "450",
            "jpeg_quality": "92",
        },
    )

    match = re.search(r'id="job-([^"]+)"', response.text)
    assert response.status_code == 200
    assert match is not None
    process_job = get_job(match.group(1))
    assert process_job is not None
    assert process_job.params_json["defaults"]["dpi_cap"] == 450
    assert process_job.params_json["defaults"]["jpeg_quality"] == 92
