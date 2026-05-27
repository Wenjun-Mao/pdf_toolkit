from __future__ import annotations

import re
from pathlib import Path

import fitz
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from loguru import logger
from PIL import Image, UnidentifiedImageError

from ..jobs import create_job, enqueue_mixed_to_pdf, get_job, update_job_fields
from ..models import Job, JobStatus
from ..pdf_ops.mixed_to_pdf import classify_mixed_input
from ..settings import Settings
from ..storage import persist_uploads
from .rendering import render_job_card, render_notice

UPLOAD_PREFIX_PATTERN = re.compile(r"^\d{2}-")


def register_mixed_to_pdf_routes(
    app: FastAPI,
    templates: Jinja2Templates,
    active_settings: Settings,
) -> None:
    @app.post("/tools/mixed-to-pdf/upload", response_class=HTMLResponse)
    async def mixed_to_pdf_upload(request: Request, files: list[UploadFile] | None = File(None)):
        job: Job | None = None
        try:
            if not files:
                raise ValueError("Select at least one PDF or image file.")
            job = create_job("mixed-to-pdf", "Mixed to PDF", [], settings=active_settings)
            update_job_fields(
                job.id,
                active_settings,
                status=JobStatus.AWAITING_SETTINGS.value,
            )
            stored_paths = await persist_uploads(job.id, files, active_settings)
            if not stored_paths:
                raise ValueError("Select at least one PDF or image file.")
            for stored_path in stored_paths:
                _validate_mixed_upload(stored_path)
            update_job_fields(
                job.id,
                active_settings,
                status=JobStatus.AWAITING_SETTINGS.value,
                input_paths=[str(path) for path in stored_paths],
            )
            return _render_review(request, templates, job.id, stored_paths)
        except Exception as exc:
            logger.exception("Mixed-to-PDF upload failed")
            if job is not None:
                update_job_fields(
                    job.id,
                    active_settings,
                    status=JobStatus.FAILED.value,
                    error_message=str(exc),
                )
            return render_notice(request, templates, str(exc), status_code=400)

    @app.post("/tools/mixed-to-pdf/{job_id}/submit", response_class=HTMLResponse)
    async def mixed_to_pdf_submit(
        request: Request,
        job_id: str,
        ordered_file_ids: list[str] | None = Form(None),
        fallback_dpi: int = Form(300),
        jpeg_quality: int = Form(95),
        page_size: str = Form("original"),
        margin_mm: float = Form(0.0),
        placement: str = Form("fit"),
    ):
        job = get_job(job_id, active_settings)
        if job is None:
            raise HTTPException(status_code=404, detail="Mixed PDF job not found.")
        if job.tool_name != "mixed-to-pdf":
            raise HTTPException(status_code=400, detail="Job is not a mixed PDF job.")
        if job.status != JobStatus.AWAITING_SETTINGS.value:
            return render_notice(
                request,
                templates,
                "Mixed PDF job is not awaiting review.",
                status_code=400,
            )

        try:
            ordered_paths = _resolve_ordered_paths(job, ordered_file_ids or [])
            update_job_fields(
                job.id,
                active_settings,
                status=JobStatus.QUEUED.value,
                input_paths=[str(path) for path in ordered_paths],
                params_json={
                    "fallback_dpi": fallback_dpi,
                    "jpeg_quality": jpeg_quality,
                    "page_size": page_size,
                    "margin_mm": margin_mm,
                    "placement": placement,
                },
            )
            enqueue_mixed_to_pdf(job.id, active_settings)
            return render_job_card(request, templates, job.id, active_settings)
        except Exception as exc:
            logger.exception("Mixed-to-PDF submit failed")
            return render_notice(request, templates, str(exc), status_code=400)


def _render_review(
    request: Request,
    templates: Jinja2Templates,
    job_id: str,
    stored_paths: list[Path],
) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "partials/forms/mixed_to_pdf_review.html",
        {
            "job_id": job_id,
            "items": [_build_review_item(path) for path in stored_paths],
        },
    )


def _build_review_item(path: Path) -> dict[str, str]:
    input_kind = classify_mixed_input(path)
    return {
        "id": path.name,
        "name": UPLOAD_PREFIX_PATTERN.sub("", path.name),
        "kind": "PDF" if input_kind == "pdf" else "Image",
    }


def _resolve_ordered_paths(job: Job, ordered_file_ids: list[str]) -> list[Path]:
    path_by_id = {Path(path).name: Path(path) for path in job.input_paths}
    if len(path_by_id) != len(job.input_paths):
        raise ValueError("Uploaded files have duplicate IDs.")
    if len(ordered_file_ids) != len(path_by_id) or set(ordered_file_ids) != set(path_by_id):
        raise ValueError("Submitted file order does not match uploaded files.")
    return [path_by_id[file_id] for file_id in ordered_file_ids]


def _validate_mixed_upload(path: Path) -> None:
    input_kind = classify_mixed_input(path)
    if input_kind == "pdf":
        _validate_pdf_upload(path)
        return
    _validate_image_upload(path)


def _validate_pdf_upload(path: Path) -> None:
    try:
        with fitz.open(path) as document:
            if document.page_count < 1:
                raise ValueError("Uploaded PDF must contain at least one page.")
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError("Uploaded PDF could not be read.") from exc


def _validate_image_upload(path: Path) -> None:
    try:
        with Image.open(path) as image:
            image.verify()
    except (OSError, UnidentifiedImageError) as exc:
        raise ValueError("Uploaded image could not be read.") from exc
