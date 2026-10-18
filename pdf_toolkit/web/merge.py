from __future__ import annotations

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from loguru import logger

from ..jobs import create_job, enqueue_merge, get_job, update_job_fields
from ..models import Job, JobStatus
from ..settings import Settings
from ..storage import persist_uploads
from .file_ordering import build_ordered_file_item, resolve_ordered_paths
from .rendering import render_job_card, render_notice


def register_merge_routes(
    app: FastAPI,
    templates: Jinja2Templates,
    active_settings: Settings,
) -> None:
    @app.post("/tools/merge/upload", response_class=HTMLResponse)
    async def merge_upload(request: Request, files: list[UploadFile] | None = File(None)):
        job: Job | None = None
        try:
            if not files or len(files) < 2:
                raise ValueError("Select at least two PDF files to merge.")
            job = create_job("merge", "Merge PDFs", [], settings=active_settings)
            update_job_fields(
                job.id,
                active_settings,
                status=JobStatus.AWAITING_SETTINGS.value,
            )
            stored_paths = await persist_uploads(job.id, files, active_settings)
            if len(stored_paths) < 2:
                raise ValueError("Select at least two PDF files to merge.")
            update_job_fields(
                job.id,
                active_settings,
                input_paths=[str(path) for path in stored_paths],
            )
            return templates.TemplateResponse(
                request,
                "partials/forms/merge_review.html",
                {
                    "job_id": job.id,
                    "items": [build_ordered_file_item(path, "PDF") for path in stored_paths],
                },
            )
        except Exception as exc:
            logger.exception("Merge upload failed")
            if job is not None:
                update_job_fields(
                    job.id,
                    active_settings,
                    status=JobStatus.FAILED.value,
                    error_message=str(exc),
                )
            return render_notice(request, templates, str(exc), status_code=400)

    @app.post("/tools/merge/{job_id}/submit", response_class=HTMLResponse)
    async def merge_submit(
        request: Request,
        job_id: str,
        ordered_file_ids: list[str] | None = Form(None),
    ):
        job = get_job(job_id, active_settings)
        if job is None:
            raise HTTPException(status_code=404, detail="Merge job not found.")
        if job.tool_name != "merge":
            raise HTTPException(status_code=400, detail="Job is not a merge job.")
        if job.status != JobStatus.AWAITING_SETTINGS.value:
            return render_notice(
                request,
                templates,
                "Merge job is not awaiting review.",
                status_code=400,
            )

        try:
            ordered_paths = resolve_ordered_paths(job.input_paths, ordered_file_ids or [])
            update_job_fields(
                job.id,
                active_settings,
                status=JobStatus.QUEUED.value,
                input_paths=[str(path) for path in ordered_paths],
            )
            enqueue_merge(job.id, active_settings)
            return render_job_card(request, templates, job.id, active_settings)
        except Exception as exc:
            logger.exception("Merge submit failed")
            return render_notice(request, templates, str(exc), status_code=400)
