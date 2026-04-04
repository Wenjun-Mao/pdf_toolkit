from __future__ import annotations

from pathlib import Path
from typing import Any

from loguru import logger

from .db import session_scope
from .models import Job, JobStatus
from .pdf_ops import (
    CleanupSettings,
    ScanAnalysis,
    analyze_scan_pdf,
    clean_scanned_pdf,
    extract_embedded_images,
    extract_pages,
    id_halves_to_pdf,
    images_to_pdf,
    merge_pdfs,
    split_pdf,
)
from .queueing import dispatch_job
from .settings import Settings, get_settings
from .storage import (
    build_result_path,
    job_preview_dir,
    job_result_dir,
    zip_directory,
)


def create_job(
    tool_name: str,
    display_name: str,
    input_paths: list[Path],
    params_json: dict[str, Any] | None = None,
    *,
    result_kind: str = "file",
    settings: Settings | None = None,
) -> Job:
    active_settings = settings or get_settings()
    with session_scope(active_settings) as session:
        job = Job(
            tool_name=tool_name,
            display_name=display_name,
            input_paths=[str(path) for path in input_paths],
            params_json=params_json or {},
            result_kind=result_kind,
            status=JobStatus.QUEUED.value,
        )
        session.add(job)
        session.flush()
        session.refresh(job)
        return job


def get_job(job_id: str, settings: Settings | None = None) -> Job | None:
    active_settings = settings or get_settings()
    with session_scope(active_settings) as session:
        job = session.get(Job, job_id)
        if job is None:
            return None
        session.expunge(job)
        return job


def update_job_fields(job_id: str, settings: Settings | None = None, **updates: Any) -> None:
    active_settings = settings or get_settings()
    with session_scope(active_settings) as session:
        job = session.get(Job, job_id)
        if job is None:
            raise ValueError(f"Job {job_id} was not found.")
        for field_name, value in updates.items():
            setattr(job, field_name, value)


def list_recent_jobs(limit: int = 20, settings: Settings | None = None) -> list[Job]:
    active_settings = settings or get_settings()
    with session_scope(active_settings) as session:
        jobs = session.query(Job).order_by(Job.created_at.desc()).limit(limit).all()
        for job in jobs:
            session.expunge(job)
        return jobs


def enqueue_merge(job_id: str, settings: Settings | None = None):
    active_settings = settings or get_settings()
    return dispatch_job(run_merge_job, job_id, settings=active_settings)


def enqueue_split(job_id: str, settings: Settings | None = None):
    active_settings = settings or get_settings()
    return dispatch_job(run_split_job, job_id, settings=active_settings)


def enqueue_extract_pages(job_id: str, settings: Settings | None = None):
    active_settings = settings or get_settings()
    return dispatch_job(run_extract_pages_job, job_id, settings=active_settings)


def enqueue_extract_images(job_id: str, settings: Settings | None = None):
    active_settings = settings or get_settings()
    return dispatch_job(run_extract_images_job, job_id, settings=active_settings)


def enqueue_images_to_pdf(job_id: str, settings: Settings | None = None):
    active_settings = settings or get_settings()
    return dispatch_job(run_images_to_pdf_job, job_id, settings=active_settings)


def enqueue_id_halves_to_pdf(job_id: str, settings: Settings | None = None):
    active_settings = settings or get_settings()
    return dispatch_job(run_id_halves_to_pdf_job, job_id, settings=active_settings)


def enqueue_scan_analysis(job_id: str, settings: Settings | None = None):
    active_settings = settings or get_settings()
    return dispatch_job(run_scan_analysis_job, job_id, settings=active_settings)


def enqueue_scan_process(job_id: str, settings: Settings | None = None):
    active_settings = settings or get_settings()
    return dispatch_job(run_scan_process_job, job_id, settings=active_settings)


def run_merge_job(job_id: str) -> str:
    return _run_job(job_id, JobStatus.PROCESSING, _merge_job_impl)


def run_split_job(job_id: str) -> str:
    return _run_job(job_id, JobStatus.PROCESSING, _split_job_impl, complete=False)


def run_extract_pages_job(job_id: str) -> str:
    return _run_job(job_id, JobStatus.PROCESSING, _extract_pages_job_impl)


def run_extract_images_job(job_id: str) -> str:
    return _run_job(job_id, JobStatus.PROCESSING, _extract_images_job_impl, complete=False)


def run_images_to_pdf_job(job_id: str) -> str:
    return _run_job(job_id, JobStatus.PROCESSING, _images_to_pdf_job_impl)


def run_id_halves_to_pdf_job(job_id: str) -> str:
    return _run_job(job_id, JobStatus.PROCESSING, _id_halves_to_pdf_job_impl)


def run_scan_analysis_job(job_id: str) -> str:
    return _run_job(job_id, JobStatus.ANALYZING, _scan_analysis_job_impl, complete=False)


def run_scan_process_job(job_id: str) -> str:
    return _run_job(job_id, JobStatus.PROCESSING, _scan_process_job_impl)


def _set_job_status(job_id: str, status: JobStatus) -> Job:
    with session_scope() as session:
        job = session.get(Job, job_id)
        if job is None:
            raise ValueError(f"Job {job_id} was not found.")
        job.status = status.value
        session.flush()
        session.refresh(job)
        session.expunge(job)
        return job


def _complete_job(job_id: str, *, output_path: Path, result_kind: str | None = None) -> None:
    updates: dict[str, Any] = {
        "status": JobStatus.COMPLETED.value,
        "output_path": str(output_path),
    }
    if result_kind:
        updates["result_kind"] = result_kind
    update_job_fields(job_id, **updates)


def fail_job(job_id: str, error_message: str) -> None:
    logger.exception("Job {} failed: {}", job_id, error_message)
    update_job_fields(job_id, status=JobStatus.FAILED.value, error_message=error_message)


def _run_job(
    job_id: str,
    status: JobStatus,
    operation,
    *,
    complete: bool = True,
) -> str:
    try:
        job = _set_job_status(job_id, status)
        output = operation(job)
        if complete and isinstance(output, Path):
            _complete_job(job.id, output_path=output)
        return str(output)
    except Exception as exc:
        fail_job(job_id, str(exc))
        raise


def _merge_job_impl(job: Job) -> Path:
    output_path = build_result_path(job.id, "merged.pdf")
    merge_pdfs([Path(path) for path in job.input_paths], output_path)
    return output_path


def _split_job_impl(job: Job) -> Path:
    input_path = Path(job.input_paths[0])
    output_dir = job_result_dir(job.id)
    range_spec = job.params_json.get("range_spec")
    every_n = job.params_json.get("every_n")
    split_pdf(input_path, output_dir, range_spec=range_spec, every_n=every_n)
    archive_path = zip_directory(output_dir, output_dir / "split-output.zip")
    _complete_job(job.id, output_path=archive_path, result_kind="zip")
    return archive_path


def _extract_pages_job_impl(job: Job) -> Path:
    input_path = Path(job.input_paths[0])
    output_path = build_result_path(job.id, "extracted-pages.pdf")
    extract_pages(input_path, job.params_json["page_spec"], output_path)
    return output_path


def _extract_images_job_impl(job: Job) -> Path:
    input_path = Path(job.input_paths[0])
    output_dir = job_result_dir(job.id)
    manifest = extract_embedded_images(input_path, output_dir)
    update_job_fields(job.id, artifact_json={"manifest": manifest})
    archive_path = zip_directory(output_dir, output_dir / "embedded-images.zip")
    _complete_job(job.id, output_path=archive_path, result_kind="zip")
    return archive_path


def _images_to_pdf_job_impl(job: Job) -> Path:
    output_path = build_result_path(job.id, "images.pdf")
    images_to_pdf(
        [Path(path) for path in job.input_paths],
        output_path,
        fallback_dpi=int(job.params_json.get("fallback_dpi", 300)),
        jpeg_quality=int(job.params_json.get("jpeg_quality", 95)),
        page_size=str(job.params_json.get("page_size", "original")),
        margin_mm=float(job.params_json.get("margin_mm", 0.0)),
        placement=str(job.params_json.get("placement", "fit")),
    )
    return output_path


def _id_halves_to_pdf_job_impl(job: Job) -> Path:
    if len(job.input_paths) != 2:
        raise ValueError("ID halves conversion requires exactly two image inputs.")

    output_path = build_result_path(job.id, "id-halves.pdf")
    id_halves_to_pdf(
        Path(job.input_paths[0]),
        Path(job.input_paths[1]),
        output_path,
        fallback_dpi=int(job.params_json.get("fallback_dpi", 300)),
        jpeg_quality=int(job.params_json.get("jpeg_quality", 95)),
    )
    return output_path


def _scan_analysis_job_impl(job: Job) -> str:
    input_path = Path(job.input_paths[0])
    preview_dir = job_preview_dir(job.id)
    analysis = analyze_scan_pdf(
        input_path,
        preview_dir,
        preview_width_px=int(job.params_json.get("preview_width_px", 240)),
    )
    analysis_payload = analysis.to_json()
    for page_payload in analysis_payload["pages"]:
        page_payload["preview_path"] = Path(page_payload["preview_path"]).name
    update_job_fields(
        job.id,
        status=JobStatus.AWAITING_SETTINGS.value,
        artifact_json={"analysis": analysis_payload},
    )
    return analysis.source_path


def _scan_process_job_impl(job: Job) -> Path:
    analysis_payload = job.params_json.get("analysis")
    if not analysis_payload:
        raise ValueError("Scan cleanup job is missing its analysis payload.")
    analysis = ScanAnalysis.from_json(analysis_payload)
    output_path = build_result_path(job.id, "scan-cleaned.pdf")
    default_settings = CleanupSettings(
        strength=float(job.params_json["defaults"]["strength"]),
        white_point=int(job.params_json["defaults"]["white_point"]),
        contrast=float(job.params_json["defaults"]["contrast"]),
    )
    page_overrides = {
        int(page_number): CleanupSettings(
            strength=float(override["strength"]),
            white_point=int(override["white_point"]),
            contrast=float(override["contrast"]),
        )
        for page_number, override in job.params_json.get("page_overrides", {}).items()
    }
    clean_scanned_pdf(
        Path(job.input_paths[0]),
        output_path,
        analysis=analysis,
        default_settings=default_settings,
        page_overrides=page_overrides,
    )
    return output_path
