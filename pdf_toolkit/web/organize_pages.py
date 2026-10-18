from __future__ import annotations

from pathlib import Path
from typing import Literal

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, Response
from fastapi.templating import Jinja2Templates
from loguru import logger
from starlette.concurrency import run_in_threadpool

from ..db import session_scope
from ..jobs import create_job, enqueue_organize_pages, get_job, update_job_fields
from ..models import Job, JobStatus
from ..pdf_ops.organize_pages import pdf_page_count, render_organizer_page, validate_page_order
from ..settings import Settings
from ..storage import persist_uploads
from .rendering import render_job_card


def _organizer_job(job_id: str, settings: Settings) -> Job:
    job = get_job(job_id, settings)
    if job is None or job.tool_name != "organize-pages":
        raise HTTPException(status_code=404, detail="Organize Pages job not found.")
    return job


def _transition_review(job_id: str, settings: Settings, status: JobStatus, **updates) -> None:
    with session_scope(settings) as session:
        claimed = (
            session.query(Job)
            .filter(Job.id == job_id, Job.status == JobStatus.AWAITING_SETTINGS.value)
            .update({"status": status.value, **updates}, synchronize_session=False)
        )
        if claimed != 1:
            raise HTTPException(status_code=409, detail="This PDF is no longer awaiting edits.")


def register_organize_pages_routes(
    app: FastAPI,
    templates: Jinja2Templates,
    active_settings: Settings,
) -> None:
    @app.post("/tools/organize-pages/upload", response_class=HTMLResponse)
    async def organize_upload(request: Request, file: UploadFile = File(...)):
        job: Job | None = None
        try:
            if file.size is not None and file.size > active_settings.max_upload_mb * 1024 * 1024:
                raise ValueError(f"The PDF must be smaller than {active_settings.max_upload_mb} MB.")
            source_name = (file.filename or "document.pdf").replace("\\", "/").rsplit("/", 1)[-1]
            job = create_job("organize-pages", "Organize Pages", [], settings=active_settings)
            paths = await persist_uploads(job.id, [file], active_settings)
            page_count = await run_in_threadpool(pdf_page_count, paths[0])
            update_job_fields(
                job.id,
                active_settings,
                input_paths=[str(paths[0])],
                status=JobStatus.AWAITING_SETTINGS.value,
                artifact_json={"page_count": page_count, "source_name": source_name},
            )
            return render_job_card(request, templates, job.id, active_settings)
        except ValueError as exc:
            if job is not None:
                update_job_fields(job.id, active_settings, status=JobStatus.FAILED.value, error_message=str(exc))
            # Keep the upload form visible when displaying an expected input error.
            return templates.TemplateResponse(
                request,
                "partials/notice.html",
                {"message": str(exc), "kind": "error"},
            )
        except Exception:
            logger.exception("Organize Pages upload failed")
            if job is not None:
                update_job_fields(
                    job.id, active_settings, status=JobStatus.FAILED.value,
                    error_message="The PDF could not be opened. Please try again.",
                )
            raise HTTPException(status_code=500, detail="The PDF could not be opened. Please try again.")

    @app.get("/tools/organize-pages/{job_id}/pages/{page_number}")
    def organize_page_image(job_id: str, page_number: int, size: Literal["thumbnail", "preview"] = "thumbnail"):
        job = _organizer_job(job_id, active_settings)
        page_count = job.artifact_json.get("page_count", 0)
        if not 1 <= page_number <= page_count:
            raise HTTPException(status_code=404, detail="Page not found.")
        if job.status == JobStatus.CANCELLED.value:
            raise HTTPException(status_code=409, detail="This editing session was cancelled.")
        image = render_organizer_page(
            Path(job.input_paths[0]), page_number, max_edge=360 if size == "thumbnail" else 1600,
        )
        return Response(image, media_type="image/png", headers={"Cache-Control": "private, max-age=3600"})

    @app.post("/tools/organize-pages/{job_id}/submit", response_class=HTMLResponse)
    def organize_submit(request: Request, job_id: str, page_numbers: list[int] | None = Form(None)):
        job = _organizer_job(job_id, active_settings)
        try:
            validate_page_order(page_numbers or [], job.artifact_json.get("page_count", 0))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        _transition_review(job_id, active_settings, JobStatus.QUEUED, params_json={"page_numbers": page_numbers})
        try:
            enqueue_organize_pages(job_id, active_settings)
        except Exception:
            logger.exception("Organize Pages save failed")
            update_job_fields(
                job_id, active_settings, status=JobStatus.FAILED.value,
                error_message="Saving failed. Please reopen the source PDF and try again.",
            )
        return render_job_card(request, templates, job_id, active_settings)

    @app.post("/tools/organize-pages/{job_id}/cancel", response_class=HTMLResponse)
    def organize_cancel(request: Request, job_id: str):
        _organizer_job(job_id, active_settings)
        _transition_review(job_id, active_settings, JobStatus.CANCELLED)
        return render_job_card(request, templates, job_id, active_settings)
