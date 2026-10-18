from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import urlencode

import fitz

from pdf_toolkit.jobs import list_recent_jobs
from pdf_toolkit.models import JobStatus


def _upload_tuple(path: Path) -> tuple[str, tuple[str, bytes, str]]:
    return ("files", (path.name, path.read_bytes(), "application/pdf"))


def _upload_merge_job(app_client, paths: list[Path]) -> tuple[str, list[str]]:
    response = app_client.post(
        "/tools/merge/upload",
        files=[_upload_tuple(path) for path in paths],
    )

    assert response.status_code == 200
    assert "Set Merge Order" in response.text
    match = re.search(r"(/tools/merge/[0-9a-f-]+/submit)", response.text)
    assert match is not None
    file_ids = re.findall(r'data-ordered-file-id="([^"]+)"', response.text)
    assert len(file_ids) == len(paths)
    return match.group(1), file_ids


def _post_ordered_files(app_client, submit_url: str, file_ids: list[str]):
    return app_client.post(
        submit_url,
        content=urlencode([("ordered_file_ids", file_id) for file_id in file_ids]),
        headers={"content-type": "application/x-www-form-urlencoded"},
    )


def test_merge_tool_page_starts_with_upload_then_review(app_client) -> None:
    response = app_client.get("/tools/merge")

    assert response.status_code == 200
    assert 'hx-post="/tools/merge/upload"' in response.text
    assert "Review Order" in response.text


def test_merge_reviewed_order_controls_output_page_order(app_client, sample_merge_pdfs: list[Path]) -> None:
    submit_url, file_ids = _upload_merge_job(app_client, sample_merge_pdfs)

    response = _post_ordered_files(app_client, submit_url, list(reversed(file_ids)))

    assert response.status_code == 200
    assert "Download merged.pdf" in response.text
    merge_jobs = [job for job in list_recent_jobs() if job.tool_name == "merge"]
    assert len(merge_jobs) == 1
    merge_job = merge_jobs[0]
    assert merge_job.status == JobStatus.COMPLETED.value
    assert [Path(path).name for path in merge_job.input_paths] == list(reversed(file_ids))

    with fitz.open(merge_job.output_path) as document:
        page_text = [page.get_text().strip() for page in document]
    assert page_text == ["Second PDF / Page 1", "First PDF / Page 1", "First PDF / Page 2"]


def test_merge_submit_rejects_unknown_file_id(app_client, sample_merge_pdfs: list[Path]) -> None:
    submit_url, file_ids = _upload_merge_job(app_client, sample_merge_pdfs)

    response = _post_ordered_files(app_client, submit_url, [file_ids[0], "missing.pdf"])

    assert response.status_code == 400
    assert "Submitted file order does not match uploaded files." in response.text


def test_merge_submit_rejects_repeated_submit(app_client, sample_merge_pdfs: list[Path]) -> None:
    submit_url, file_ids = _upload_merge_job(app_client, sample_merge_pdfs)

    first_response = _post_ordered_files(app_client, submit_url, file_ids)
    second_response = _post_ordered_files(app_client, submit_url, file_ids)

    assert first_response.status_code == 200
    assert second_response.status_code == 400
    assert "Merge job is not awaiting review." in second_response.text
