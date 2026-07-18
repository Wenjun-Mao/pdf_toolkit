from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from threading import Event, Lock, Thread

import fitz
import pytest
from sqlalchemy.orm import Query, Session

from pdf_toolkit import jobs
from pdf_toolkit.models import JobStatus
from pdf_toolkit.pdf_ops import CleanupSettings, analyze_scan_pdf
from pdf_toolkit.jobs import (
    ActiveJobsError,
    clear_jobs_and_artifacts,
    create_job,
    enqueue_mixed_to_pdf,
    get_job,
    list_recent_jobs,
    update_job_fields,
)
from pdf_toolkit.storage import job_contact_sheet_dir, job_preview_dir, job_result_dir, job_upload_dir


def _job_storage_dirs(job_id: str) -> list[Path]:
    return [
        job_upload_dir(job_id),
        job_result_dir(job_id),
        job_preview_dir(job_id),
        job_contact_sheet_dir(job_id),
    ]


def test_mixed_to_pdf_job_completes_inline(
    app_client,
    sample_merge_pdfs: list[Path],
    sample_image_inputs: list[Path],
) -> None:
    job = create_job(
        "mixed-to-pdf",
        "Mixed to PDF",
        [sample_merge_pdfs[1], sample_image_inputs[0], sample_merge_pdfs[0]],
        params_json={
            "fallback_dpi": 300,
            "jpeg_quality": 95,
            "page_size": "original",
            "margin_mm": 0.0,
            "placement": "fit",
        },
    )

    enqueue_mixed_to_pdf(job.id)

    completed_job = get_job(job.id)
    assert completed_job is not None
    assert completed_job.status == "completed"
    assert completed_job.output_path is not None
    assert Path(completed_job.output_path).name == "mixed.pdf"

    with fitz.open(completed_job.output_path) as output_doc:
        assert output_doc.page_count == 4
        assert "Second PDF / Page 1" in output_doc[0].get_text()
        assert output_doc[1].get_images(full=True)
        assert "First PDF / Page 1" in output_doc[2].get_text()


@pytest.mark.parametrize(
    "clearable_status",
    [
        JobStatus.COMPLETED.value,
        JobStatus.FAILED.value,
        JobStatus.EXPIRED.value,
    ],
)
def test_clear_jobs_and_artifacts_removes_rows_and_job_storage(
    app_client,
    sample_merge_pdfs: list[Path],
    clearable_status: str,
) -> None:
    job = create_job("merge", "Merge PDFs", [sample_merge_pdfs[0]])
    update_job_fields(job.id, status=clearable_status)
    storage_dirs = _job_storage_dirs(job.id)
    for index, storage_dir in enumerate(storage_dirs, start=1):
        (storage_dir / f"artifact-{index}.txt").write_text("noise", encoding="utf-8")

    cleared_count = clear_jobs_and_artifacts()

    assert cleared_count == 1
    assert list_recent_jobs() == []
    assert sample_merge_pdfs[0].exists()
    for storage_dir in storage_dirs:
        assert not storage_dir.exists()


@pytest.mark.parametrize(
    "active_status",
    [
        JobStatus.QUEUED.value,
        JobStatus.ANALYZING.value,
        JobStatus.AWAITING_SETTINGS.value,
        JobStatus.PROCESSING.value,
    ],
)
def test_clear_jobs_and_artifacts_refuses_to_clear_when_a_job_is_active(
    app_client,
    sample_merge_pdfs: list[Path],
    active_status: str,
) -> None:
    completed_job = create_job("merge", "Completed", [sample_merge_pdfs[0]])
    update_job_fields(completed_job.id, status=JobStatus.COMPLETED.value)
    active_job = create_job("merge", "Active", [sample_merge_pdfs[1]])
    update_job_fields(active_job.id, status=active_status)
    storage_dirs = _job_storage_dirs(completed_job.id) + _job_storage_dirs(active_job.id)
    for index, storage_dir in enumerate(storage_dirs, start=1):
        (storage_dir / f"artifact-{index}.txt").write_text("preserve", encoding="utf-8")

    with pytest.raises(ActiveJobsError, match="1 job is active"):
        clear_jobs_and_artifacts()

    assert get_job(completed_job.id) is not None
    assert get_job(active_job.id) is not None
    for storage_dir in storage_dirs:
        assert storage_dir.exists()


def test_clear_jobs_and_artifacts_restores_staged_files_when_database_delete_fails(
    app_client,
    sample_merge_pdfs: list[Path],
    monkeypatch,
) -> None:
    job = create_job("merge", "Completed", [sample_merge_pdfs[0]])
    update_job_fields(job.id, status=JobStatus.COMPLETED.value)
    storage_dirs = _job_storage_dirs(job.id)
    for index, storage_dir in enumerate(storage_dirs, start=1):
        (storage_dir / f"artifact-{index}.txt").write_text("restore", encoding="utf-8")

    def fail_database_delete(self, *args, **kwargs):
        raise OSError("database delete failed")

    monkeypatch.setattr(Query, "delete", fail_database_delete)

    with pytest.raises(OSError, match="database delete failed"):
        clear_jobs_and_artifacts()

    assert get_job(job.id) is not None
    for storage_dir in storage_dirs:
        assert storage_dir.exists()


def test_clear_jobs_and_artifacts_serializes_concurrent_cleanup_requests(
    app_client,
    sample_merge_pdfs: list[Path],
    monkeypatch,
) -> None:
    job = create_job("merge", "Completed", [sample_merge_pdfs[0]])
    update_job_fields(job.id, status=JobStatus.COMPLETED.value)
    storage_dirs = _job_storage_dirs(job.id)
    for index, storage_dir in enumerate(storage_dirs, start=1):
        (storage_dir / f"artifact-{index}.txt").write_text("remove", encoding="utf-8")

    real_staged_job_artifacts = jobs.staged_job_artifacts
    first_request_staged = Event()
    second_begin_attempted = Event()
    release_first_request = Event()
    second_request_done = Event()
    call_count = 0
    call_count_lock = Lock()
    begin_count = 0
    begin_count_lock = Lock()
    results: list[int] = []
    errors: list[Exception] = []

    real_session_execute = Session.execute

    def track_cleanup_lock_attempt(self, statement, *args, **kwargs):
        nonlocal begin_count
        if str(statement).strip().upper() == "BEGIN IMMEDIATE":
            with begin_count_lock:
                begin_count += 1
                if begin_count == 2:
                    second_begin_attempted.set()
        return real_session_execute(self, statement, *args, **kwargs)

    @contextmanager
    def pause_first_staging(job_ids, settings=None):
        nonlocal call_count
        with call_count_lock:
            call_count += 1
            current_call = call_count
        with real_staged_job_artifacts(job_ids, settings):
            if current_call == 1:
                first_request_staged.set()
                assert release_first_request.wait(timeout=5)
            yield

    def run_cleanup(*, signal_done: bool = False) -> None:
        try:
            results.append(clear_jobs_and_artifacts())
        except Exception as exc:
            errors.append(exc)
        finally:
            if signal_done:
                second_request_done.set()

    monkeypatch.setattr(jobs, "staged_job_artifacts", pause_first_staging)
    monkeypatch.setattr(Session, "execute", track_cleanup_lock_attempt)
    first_thread = Thread(target=run_cleanup)
    second_thread = Thread(target=run_cleanup, kwargs={"signal_done": True})

    first_thread.start()
    assert first_request_staged.wait(timeout=5)
    second_thread.start()
    assert second_begin_attempted.wait(timeout=5)
    second_finished_before_release = second_request_done.is_set()
    release_first_request.set()
    first_thread.join(timeout=5)
    second_thread.join(timeout=5)

    assert not second_finished_before_release
    assert not first_thread.is_alive()
    assert not second_thread.is_alive()
    assert errors == []
    assert sorted(results) == [0, 1]
    assert list_recent_jobs() == []
    for storage_dir in storage_dirs:
        assert not storage_dir.exists()


def test_scan_process_job_uses_output_quality_settings(
    app_client,
    sample_scan_pdf: Path,
    tmp_path: Path,
    monkeypatch,
) -> None:
    analysis = analyze_scan_pdf(sample_scan_pdf, tmp_path / "previews")
    job = create_job(
        "scan-cleanup-process",
        "Process Scan Cleanup",
        [sample_scan_pdf],
        params_json={
            "analysis": analysis.to_json(),
            "defaults": {
                "strength": 0.7,
                "white_point": 244,
                "contrast": 1.1,
                "dpi_cap": 450,
                "jpeg_quality": 92,
            },
            "page_overrides": {},
        },
    )
    calls: dict[str, object] = {}

    def fake_clean_scanned_pdf(
        source_path: Path,
        output_path: Path,
        *,
        analysis,
        default_settings: CleanupSettings,
        page_overrides,
    ) -> Path:
        calls["source_path"] = source_path
        calls["output_path"] = output_path
        calls["analysis"] = analysis
        calls["default_settings"] = default_settings
        calls["page_overrides"] = page_overrides
        output_path.write_bytes(b"%PDF-1.7\n%%EOF\n")
        return output_path

    monkeypatch.setattr(jobs, "clean_scanned_pdf", fake_clean_scanned_pdf)

    jobs.run_scan_process_job(job.id)

    completed_job = get_job(job.id)
    default_settings = calls["default_settings"]
    assert completed_job is not None
    assert completed_job.status == JobStatus.COMPLETED.value
    assert calls["source_path"] == sample_scan_pdf
    assert default_settings.dpi_cap == 450
    assert default_settings.jpeg_quality == 92


def test_scan_process_job_sparse_page_overrides_inherit_defaults(
    app_client,
    sample_scan_pdf: Path,
    tmp_path: Path,
    monkeypatch,
) -> None:
    analysis = analyze_scan_pdf(sample_scan_pdf, tmp_path / "previews")
    job = create_job(
        "scan-cleanup-process",
        "Process Scan Cleanup",
        [sample_scan_pdf],
        params_json={
            "analysis": analysis.to_json(),
            "defaults": {
                "strength": 0.7,
                "white_point": 244,
                "contrast": 1.1,
                "dpi_cap": 450,
                "jpeg_quality": 92,
            },
            "page_overrides": {"1": {"strength": 0.8}},
        },
    )
    calls: dict[str, object] = {}

    def fake_clean_scanned_pdf(
        source_path: Path,
        output_path: Path,
        *,
        analysis,
        default_settings: CleanupSettings,
        page_overrides,
    ) -> Path:
        calls["page_overrides"] = page_overrides
        output_path.write_bytes(b"%PDF-1.7\n%%EOF\n")
        return output_path

    monkeypatch.setattr(jobs, "clean_scanned_pdf", fake_clean_scanned_pdf)

    jobs.run_scan_process_job(job.id)

    completed_job = get_job(job.id)
    page_settings = calls["page_overrides"][1]
    assert completed_job is not None
    assert completed_job.status == JobStatus.COMPLETED.value
    assert page_settings.strength == 0.8
    assert page_settings.white_point == 244
    assert page_settings.contrast == 1.1
    assert page_settings.dpi_cap == 450
    assert page_settings.jpeg_quality == 92
