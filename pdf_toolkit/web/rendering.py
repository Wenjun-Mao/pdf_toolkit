from __future__ import annotations

from pathlib import Path

from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from ..jobs import get_job
from ..models import Job, JobStatus
from ..settings import Settings


def serialize_job(job: Job, settings: Settings | None = None) -> dict:
    awaiting_settings = job.status == JobStatus.AWAITING_SETTINGS.value
    payload = {
        "id": job.id,
        "tool_name": job.tool_name,
        "display_name": job.display_name,
        "status": job.status,
        "result_kind": job.result_kind,
        "error_message": job.error_message,
        "download_url": f"/downloads/{job.id}" if job.output_path else None,
        "download_name": Path(job.output_path).name if job.output_path else None,
        "created_at": job.created_at,
        "output_path": job.output_path,
        "can_poll": job.status in {
            JobStatus.QUEUED.value,
            JobStatus.ANALYZING.value,
            JobStatus.PROCESSING.value,
        },
        "awaiting_settings": awaiting_settings,
        "awaiting_scan_settings": awaiting_settings and job.tool_name == "scan-cleanup-analysis",
    }
    analysis = (job.artifact_json or {}).get("analysis")
    if analysis:
        payload["analysis"] = {
            **analysis,
            "pages": [
                {
                    **page_payload,
                    "preview_url": f"/previews/{job.id}/{page_payload['preview_path']}",
                }
                for page_payload in analysis["pages"]
            ],
        }
    if (job.artifact_json or {}).get("manifest"):
        payload["manifest"] = job.artifact_json["manifest"]
    if settings is not None and payload["awaiting_scan_settings"]:
        payload["scan_defaults"] = {
            "strength": settings.scan_default_strength,
            "white_point": settings.scan_default_white_point,
            "contrast": settings.scan_default_contrast,
            "dpi_cap": settings.scan_default_dpi_cap,
            "jpeg_quality": settings.scan_default_jpeg_quality,
        }
    return payload


def render_job_card(
    request: Request,
    templates: Jinja2Templates,
    job_id: str,
    settings: Settings,
) -> HTMLResponse:
    job = get_job(job_id, settings)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found.")
    serialized_job = serialize_job(job, settings)
    template_name = (
        "partials/scan_cleanup_review.html"
        if serialized_job.get("awaiting_scan_settings") and serialized_job.get("analysis")
        else "partials/job_card.html"
    )
    return templates.TemplateResponse(
        request,
        template_name,
        {"job": serialized_job},
    )


def render_notice(
    request: Request,
    templates: Jinja2Templates,
    message: str,
    *,
    status_code: int = 200,
) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "partials/notice.html",
        {"message": message, "kind": "error"},
        status_code=status_code,
    )
