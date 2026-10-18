from __future__ import annotations

from io import BytesIO
from pathlib import Path

import fitz
import pytest
from PIL import Image

import pdf_toolkit.web.organize_pages as organizer_web
from pdf_toolkit.jobs import create_job, get_job, list_recent_jobs
from pdf_toolkit.models import JobStatus


def upload_pdf(client, source: Path):
    response = client.post(
        "/tools/organize-pages/upload", files={"file": (source.name, source.read_bytes(), "application/pdf")},
    )
    assert response.status_code == 200
    assert 'x-data="pageOrganizer"' in response.text
    job = next(job for job in list_recent_jobs() if job.tool_name == "organize-pages")
    return response, job


def test_tool_is_discoverable_on_home_and_navigation(open_app_client) -> None:
    home = open_app_client.get("/")
    tool = open_app_client.get("/tools/organize-pages")
    assert 'href="/tools/organize-pages"' in home.text
    assert "Organize Pages" in home.text
    assert "Open PDF" in tool.text
    assert 'hx-post="/tools/organize-pages/upload"' in tool.text


def test_upload_is_resumable_and_does_not_render_pages(open_app_client, sample_merge_pdfs, monkeypatch) -> None:
    def unexpected_render(*args, **kwargs):
        pytest.fail("Uploading must not render the whole PDF.")
    monkeypatch.setattr(organizer_web, "render_organizer_page", unexpected_render)
    response, job = upload_pdf(open_app_client, sample_merge_pdfs[0])
    assert job.status == JobStatus.AWAITING_SETTINGS.value
    assert job.artifact_json["page_count"] == 2
    assert not job.output_path
    assert "Continue Editing" in open_app_client.get("/").text
    assert 'x-data="pageOrganizer"' in open_app_client.get(f"/jobs/{job.id}").text
    assert 'data-page-count="2"' in response.text


def test_save_uses_selected_order_and_allows_a_single_page(open_app_client, sample_merge_pdfs) -> None:
    for order in [[2, 1], [2]]:
        _, job = upload_pdf(open_app_client, sample_merge_pdfs[0])
        response = open_app_client.post(f"/tools/organize-pages/{job.id}/submit", data={"page_numbers": order})
        assert response.status_code == 200
        assert "Download organized.pdf" in response.text
        download = open_app_client.get(f"/downloads/{job.id}")
        assert download.status_code == 200
        with fitz.open(stream=download.content, filetype="pdf") as result:
            assert [page.get_text().strip() for page in result] == [f"First PDF / Page {number}" for number in order]


@pytest.mark.parametrize("order", [[], [1, 1], [0], [3]])
def test_invalid_order_preserves_editable_session(open_app_client, sample_merge_pdfs, order) -> None:
    _, job = upload_pdf(open_app_client, sample_merge_pdfs[0])
    response = open_app_client.post(f"/tools/organize-pages/{job.id}/submit", data={"page_numbers": order})
    assert response.status_code == 400
    unchanged = get_job(job.id)
    assert unchanged.status == JobStatus.AWAITING_SETTINGS.value
    assert not unchanged.output_path


def test_queue_submission_can_only_happen_once(open_app_client, sample_merge_pdfs, monkeypatch) -> None:
    submitted = []
    monkeypatch.setattr(organizer_web, "enqueue_organize_pages", lambda job_id, settings: submitted.append(job_id))
    _, job = upload_pdf(open_app_client, sample_merge_pdfs[0])
    endpoint = f"/tools/organize-pages/{job.id}/submit"
    assert open_app_client.post(endpoint, data={"page_numbers": [2, 1]}).status_code == 200
    assert open_app_client.post(endpoint, data={"page_numbers": [1, 2]}).status_code == 409
    assert submitted == [job.id]
    assert get_job(job.id).params_json["page_numbers"] == [2, 1]


def test_thumbnail_is_bounded_and_restricted_to_organizer_job(open_app_client, sample_merge_pdfs) -> None:
    _, job = upload_pdf(open_app_client, sample_merge_pdfs[0])
    response = open_app_client.get(f"/tools/organize-pages/{job.id}/pages/2")
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    with Image.open(BytesIO(response.content)) as image:
        assert max(image.size) <= 361
    assert open_app_client.get(f"/tools/organize-pages/{job.id}/pages/0").status_code == 404
    assert open_app_client.get(f"/tools/organize-pages/{job.id}/pages/3").status_code == 404
    other = create_job("merge", "Other job", sample_merge_pdfs)
    assert open_app_client.get(f"/tools/organize-pages/{other.id}/pages/1").status_code == 404


def test_cancel_releases_pending_job_and_disallows_saving(open_app_client, sample_merge_pdfs) -> None:
    _, job = upload_pdf(open_app_client, sample_merge_pdfs[0])
    response = open_app_client.post(f"/tools/organize-pages/{job.id}/cancel")
    assert response.status_code == 200
    assert get_job(job.id).status == JobStatus.CANCELLED.value
    assert open_app_client.post(f"/tools/organize-pages/{job.id}/submit", data={"page_numbers": [1]}).status_code == 409


def test_unreadable_upload_shows_error_and_ends_job(open_app_client) -> None:
    response = open_app_client.post(
        "/tools/organize-pages/upload", files={"file": ("bad.pdf", b"not a pdf", "application/pdf")},
    )
    assert response.status_code == 200
    assert "could not be opened" in response.text
    assert list_recent_jobs()[0].status == JobStatus.FAILED.value


def test_organizer_routes_require_login(app_client, sample_merge_pdfs) -> None:
    _, job = upload_pdf(app_client, sample_merge_pdfs[0])
    unauthenticated = app_client.__class__(app_client.app)
    assert unauthenticated.get(f"/tools/organize-pages/{job.id}/pages/1", follow_redirects=False).status_code == 303
    assert unauthenticated.post(f"/tools/organize-pages/{job.id}/cancel", follow_redirects=False).status_code == 303
