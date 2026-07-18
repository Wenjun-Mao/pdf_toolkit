from __future__ import annotations

from pdf_toolkit.jobs import create_job, get_job, list_recent_jobs, update_job_fields
from pdf_toolkit.models import JobStatus


def test_home_exposes_confirmed_clear_recent_jobs_action(open_app_client, sample_merge_pdfs) -> None:
    create_job("merge", "Merge PDFs", [sample_merge_pdfs[0]])

    response = open_app_client.get("/")

    assert response.status_code == 200
    assert "Clear Recent Jobs" in response.text
    assert 'action="/jobs/clear"' in response.text
    assert 'method="post"' in response.text
    assert "Clear all recent jobs and delete their generated files?" in response.text


def test_clear_recent_jobs_submission_removes_all_jobs(open_app_client, sample_merge_pdfs) -> None:
    job = create_job("merge", "Merge PDFs", [sample_merge_pdfs[0]])
    update_job_fields(job.id, status=JobStatus.COMPLETED.value)

    response = open_app_client.post("/jobs/clear", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/"
    assert list_recent_jobs() == []


def test_clear_recent_jobs_submission_reports_active_job(open_app_client, sample_merge_pdfs) -> None:
    job = create_job("merge", "Active", [sample_merge_pdfs[0]])

    response = open_app_client.post("/jobs/clear")

    assert response.status_code == 400
    assert "1 job is active" in response.text
    assert "Practical PDF tooling, one queue away." in response.text
    assert "Recent Jobs" in response.text
    assert "Clear Recent Jobs" in response.text
    assert get_job(job.id) is not None
