from __future__ import annotations

import hashlib
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, Response
from fastapi.templating import Jinja2Templates
from starlette.datastructures import FormData

from ..jobs import get_job
from ..pdf_ops import CleanupSettings, ScanAnalysis, render_cleaned_page_preview
from ..settings import Settings
from ..storage import job_preview_dir
from .scan_cleanup_forms import parse_page_overrides, parse_scan_defaults, settings_for_page


def register_scan_cleanup_preview_routes(
    app: FastAPI,
    templates: Jinja2Templates,
    settings: Settings,
) -> None:
    @app.post("/tools/scan-cleanup/{analysis_job_id}/preview", response_class=HTMLResponse)
    async def scan_cleanup_preview_submit(request: Request, analysis_job_id: str):
        analysis_job = get_job(analysis_job_id, settings)
        if analysis_job is None:
            raise HTTPException(status_code=404, detail="Analysis job not found.")
        analysis_payload = analysis_job.artifact_json.get("analysis")
        if not analysis_payload:
            raise HTTPException(status_code=400, detail="Analysis data is not ready yet.")

        form = await request.form()
        analysis = ScanAnalysis.from_json(analysis_payload)
        try:
            page_number = _parse_preview_page(form, analysis.page_count)
        except ValueError as exc:
            return _render_validation_notice(request, templates, str(exc))

        try:
            defaults = parse_scan_defaults(form, settings)
            page_overrides = parse_page_overrides(form)
        except (TypeError, ValueError):
            return _render_validation_notice(request, templates, "Preview settings must be numeric.")

        preview_settings = settings_for_page(defaults, page_overrides, page_number)
        preview_dir = job_preview_dir(analysis_job.id, settings)
        processed_filename = _processed_preview_filename(
            page_number,
            preview_settings,
            settings.preview_width_px,
        )
        processed_path = preview_dir / processed_filename

        if not processed_path.exists():
            render_cleaned_page_preview(
                source_path=Path(analysis_job.input_paths[0]),
                output_path=processed_path,
                analysis=analysis,
                page_number=page_number,
                settings=preview_settings,
                preview_width_px=settings.preview_width_px,
            )

        page_payload = analysis_payload["pages"][page_number - 1]
        return templates.TemplateResponse(
            request,
            "partials/scan_cleanup_compare.html",
            {
                "page_number": page_number,
                "settings": preview_settings,
                "original_url": f"/previews/{analysis_job.id}/{page_payload['preview_path']}",
                "processed_url": f"/previews/{analysis_job.id}/{processed_filename}",
            },
        )


def _parse_preview_page(form: FormData, page_count: int) -> int:
    raw_page_number = form.get("preview_page", "1")
    try:
        page_number = int(raw_page_number)
    except (TypeError, ValueError) as exc:
        raise ValueError("Preview page must be a whole number.") from exc
    if page_number < 1 or page_number > page_count:
        raise ValueError(f"Preview page must be between 1 and {page_count}.")
    return page_number


def _render_validation_notice(
    request: Request,
    templates: Jinja2Templates,
    message: str,
) -> Response:
    return templates.TemplateResponse(
        request,
        "partials/notice.html",
        {"message": message, "kind": "error"},
        status_code=400,
    )


def _processed_preview_filename(
    page_number: int,
    preview_settings: CleanupSettings,
    preview_width_px: int,
) -> str:
    cache_key = "|".join(
        [
            f"{preview_settings.strength:.4f}",
            str(preview_settings.white_point),
            f"{preview_settings.contrast:.4f}",
            str(preview_settings.dpi_cap),
            str(preview_settings.jpeg_quality),
            str(preview_width_px),
        ]
    )
    digest = hashlib.sha1(cache_key.encode("utf-8")).hexdigest()[:12]
    return f"processed-page-{page_number:03d}-{digest}.jpg"
