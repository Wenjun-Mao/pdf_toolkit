from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from loguru import logger

from ..auth import authenticate, clear_session_cookie, get_session_user, set_session_cookie
from ..db import init_db
from ..jobs import (
    create_job,
    enqueue_extract_images,
    enqueue_extract_pages,
    enqueue_id_halves_to_pdf,
    enqueue_images_to_pdf,
    enqueue_merge,
    enqueue_scan_analysis,
    enqueue_scan_process,
    enqueue_split,
    get_job,
    list_recent_jobs,
    update_job_fields,
)
from ..logging import configure_logging
from ..settings import Settings, get_settings
from ..storage import ensure_storage_dirs, persist_uploads
from .mixed_to_pdf import register_mixed_to_pdf_routes
from .rendering import render_job_card, render_notice, serialize_job
from .scan_cleanup_forms import parse_page_overrides, parse_scan_defaults
from .scan_cleanup_preview import register_scan_cleanup_preview_routes

TOOL_REGISTRY = {
    "merge": {
        "title": "Fuse PDFs",
        "description": "Combine two or more PDFs into a single file while preserving order.",
        "form_template": "partials/forms/merge.html",
    },
    "mixed-to-pdf": {
        "title": "Mixed to PDF",
        "description": "Combine PDFs and images into one PDF after reviewing the whole-file order.",
        "form_template": "partials/forms/mixed_to_pdf.html",
    },
    "split": {
        "title": "Split PDF",
        "description": "Slice one PDF into several outputs by explicit groups or every N pages.",
        "form_template": "partials/forms/split.html",
    },
    "extract-pages": {
        "title": "Extract Pages",
        "description": "Pull selected 1-based page ranges into a new PDF.",
        "form_template": "partials/forms/extract_pages.html",
    },
    "extract-images": {
        "title": "Extract Embedded Images",
        "description": "Export original embedded raster assets instead of taking screenshots.",
        "form_template": "partials/forms/extract_images.html",
    },
    "images-to-pdf": {
        "title": "Images to PDF",
        "description": "Combine multiple images into a single PDF with better sizing and encoding defaults.",
        "form_template": "partials/forms/images_to_pdf.html",
    },
    "id-halves-to-pdf": {
        "title": "ID Halves to PDF",
        "description": "Take the top half of one image and the bottom half of another, then output one PDF page.",
        "form_template": "partials/forms/id_halves_to_pdf.html",
    },
    "scan-cleanup": {
        "title": "Scan Cleanup",
        "description": "Preview scanned pages, tune cleanup strength, and preserve OCR/text layers.",
        "form_template": "partials/forms/scan_cleanup.html",
    },
}


def create_app(settings: Settings | None = None) -> FastAPI:
    active_settings = settings or get_settings()
    configure_logging(active_settings.debug)
    ensure_storage_dirs(active_settings)
    init_db(active_settings)

    app = FastAPI(title=active_settings.app_name, debug=active_settings.debug)
    templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
    templates.env.globals["require_login"] = active_settings.require_login
    app.mount("/static", StaticFiles(directory=str(Path(__file__).parent / "static")), name="static")
    register_mixed_to_pdf_routes(app, templates, active_settings)
    register_scan_cleanup_preview_routes(app, templates, active_settings)

    @app.middleware("http")
    async def require_login(request: Request, call_next):
        if not active_settings.require_login:
            return await call_next(request)
        public_paths = {"/login", "/health"}
        if request.url.path.startswith("/static") or request.url.path in public_paths:
            return await call_next(request)
        if get_session_user(request, active_settings) is None:
            return RedirectResponse("/login", status_code=303)
        return await call_next(request)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/login", response_class=HTMLResponse)
    async def login_page(request: Request):
        if not active_settings.require_login:
            return RedirectResponse("/", status_code=303)
        return templates.TemplateResponse(
            request,
            "login.html",
            {
                "app_name": active_settings.app_name,
                "error_message": None,
            },
        )

    @app.post("/login", response_class=HTMLResponse)
    async def login_submit(
        request: Request,
        username: str = Form(...),
        password: str = Form(...),
    ):
        if not active_settings.require_login:
            return RedirectResponse("/", status_code=303)
        if not authenticate(username, password, active_settings):
            return templates.TemplateResponse(
                request,
                "login.html",
                {
                    "app_name": active_settings.app_name,
                    "error_message": "Invalid credentials.",
                },
                status_code=400,
            )
        response = RedirectResponse("/", status_code=303)
        set_session_cookie(response, request, active_settings)
        return response

    @app.post("/logout")
    async def logout() -> Response:
        target_path = "/login" if active_settings.require_login else "/"
        response = RedirectResponse(target_path, status_code=303)
        clear_session_cookie(response)
        return response

    @app.get("/", response_class=HTMLResponse)
    async def home(request: Request):
        jobs = [serialize_job(job) for job in list_recent_jobs(settings=active_settings)]
        return templates.TemplateResponse(
            request,
            "home.html",
            {
                "app_name": active_settings.app_name,
                "tools": TOOL_REGISTRY,
                "jobs": jobs,
            },
        )

    @app.get("/tools/{tool_id}", response_class=HTMLResponse)
    async def tool_page(request: Request, tool_id: str):
        tool = TOOL_REGISTRY.get(tool_id)
        if tool is None:
            raise HTTPException(status_code=404, detail="Unknown tool.")
        return templates.TemplateResponse(
            request,
            "tool_page.html",
            {
                "app_name": active_settings.app_name,
                "tool_id": tool_id,
                "tool": tool,
            },
        )

    @app.post("/tools/merge/submit", response_class=HTMLResponse)
    async def merge_submit(request: Request, files: list[UploadFile] = File(...)):
        try:
            if len(files) < 2:
                raise ValueError("Select at least two PDF files to merge.")
            job = create_job("merge", "Merge PDFs", [], settings=active_settings)
            stored_paths = await persist_uploads(job.id, files, active_settings)
            update_job_fields(job.id, active_settings, input_paths=[str(path) for path in stored_paths])
            enqueue_merge(job.id, active_settings)
            return render_job_card(request, templates, job.id, active_settings)
        except Exception as exc:
            logger.exception("Merge submission failed")
            return render_notice(request, templates, str(exc), status_code=400)

    @app.post("/tools/split/submit", response_class=HTMLResponse)
    async def split_submit(
        request: Request,
        file: UploadFile = File(...),
        range_spec: str = Form(""),
        every_n: str = Form(""),
    ):
        try:
            normalized_range_spec = range_spec.strip() or None
            normalized_every_n = int(every_n) if every_n.strip() else None
            if not normalized_range_spec and not normalized_every_n:
                raise ValueError("Provide split ranges or an every-N value.")
            job = create_job(
                "split",
                "Split PDF",
                [],
                params_json={"range_spec": normalized_range_spec, "every_n": normalized_every_n},
                result_kind="zip",
                settings=active_settings,
            )
            stored_paths = await persist_uploads(job.id, [file], active_settings)
            update_job_fields(job.id, active_settings, input_paths=[str(path) for path in stored_paths])
            enqueue_split(job.id, active_settings)
            return render_job_card(request, templates, job.id, active_settings)
        except Exception as exc:
            logger.exception("Split submission failed")
            return render_notice(request, templates, str(exc), status_code=400)

    @app.post("/tools/extract-pages/submit", response_class=HTMLResponse)
    async def extract_pages_submit(
        request: Request,
        file: UploadFile = File(...),
        page_spec: str = Form(...),
    ):
        try:
            job = create_job(
                "extract-pages",
                "Extract Pages",
                [],
                params_json={"page_spec": page_spec},
                settings=active_settings,
            )
            stored_paths = await persist_uploads(job.id, [file], active_settings)
            update_job_fields(job.id, active_settings, input_paths=[str(path) for path in stored_paths])
            enqueue_extract_pages(job.id, active_settings)
            return render_job_card(request, templates, job.id, active_settings)
        except Exception as exc:
            logger.exception("Extract-pages submission failed")
            return render_notice(request, templates, str(exc), status_code=400)

    @app.post("/tools/extract-images/submit", response_class=HTMLResponse)
    async def extract_images_submit(request: Request, file: UploadFile = File(...)):
        try:
            job = create_job(
                "extract-images",
                "Extract Embedded Images",
                [],
                result_kind="zip",
                settings=active_settings,
            )
            stored_paths = await persist_uploads(job.id, [file], active_settings)
            update_job_fields(job.id, active_settings, input_paths=[str(path) for path in stored_paths])
            enqueue_extract_images(job.id, active_settings)
            return render_job_card(request, templates, job.id, active_settings)
        except Exception as exc:
            logger.exception("Extract-images submission failed")
            return render_notice(request, templates, str(exc), status_code=400)

    @app.post("/tools/images-to-pdf/submit", response_class=HTMLResponse)
    async def images_to_pdf_submit(
        request: Request,
        files: list[UploadFile] = File(...),
        fallback_dpi: int = Form(300),
        jpeg_quality: int = Form(95),
        page_size: str = Form("original"),
        margin_mm: float = Form(0.0),
        placement: str = Form("fit"),
    ):
        try:
            if not files:
                raise ValueError("Select at least one image file.")
            job = create_job(
                "images-to-pdf",
                "Images to PDF",
                [],
                params_json={
                    "fallback_dpi": fallback_dpi,
                    "jpeg_quality": jpeg_quality,
                    "page_size": page_size,
                    "margin_mm": margin_mm,
                    "placement": placement,
                },
                settings=active_settings,
            )
            stored_paths = await persist_uploads(job.id, files, active_settings)
            update_job_fields(job.id, active_settings, input_paths=[str(path) for path in stored_paths])
            enqueue_images_to_pdf(job.id, active_settings)
            return render_job_card(request, templates, job.id, active_settings)
        except Exception as exc:
            logger.exception("Images-to-PDF submission failed")
            return render_notice(request, templates, str(exc), status_code=400)

    @app.post("/tools/id-halves-to-pdf/submit", response_class=HTMLResponse)
    async def id_halves_to_pdf_submit(
        request: Request,
        top_image: UploadFile = File(...),
        bottom_image: UploadFile = File(...),
        fallback_dpi: int = Form(300),
        jpeg_quality: int = Form(95),
    ):
        try:
            job = create_job(
                "id-halves-to-pdf",
                "ID Halves to PDF",
                [],
                params_json={
                    "fallback_dpi": fallback_dpi,
                    "jpeg_quality": jpeg_quality,
                },
                settings=active_settings,
            )
            stored_paths = await persist_uploads(job.id, [top_image, bottom_image], active_settings)
            if len(stored_paths) != 2:
                raise ValueError("Select a top image and a bottom image.")
            update_job_fields(job.id, active_settings, input_paths=[str(path) for path in stored_paths])
            enqueue_id_halves_to_pdf(job.id, active_settings)
            return render_job_card(request, templates, job.id, active_settings)
        except Exception as exc:
            logger.exception("ID halves submission failed")
            return render_notice(request, templates, str(exc), status_code=400)

    @app.post("/tools/scan-cleanup/submit", response_class=HTMLResponse)
    async def scan_cleanup_submit(request: Request, file: UploadFile = File(...)):
        try:
            job = create_job(
                "scan-cleanup-analysis",
                "Analyze Scan Cleanup",
                [],
                params_json={"preview_width_px": active_settings.preview_width_px},
                settings=active_settings,
            )
            stored_paths = await persist_uploads(job.id, [file], active_settings)
            update_job_fields(job.id, active_settings, input_paths=[str(path) for path in stored_paths])
            enqueue_scan_analysis(job.id, active_settings)
            return render_job_card(request, templates, job.id, active_settings)
        except Exception as exc:
            logger.exception("Scan analysis submission failed")
            return render_notice(request, templates, str(exc), status_code=400)

    @app.post("/tools/scan-cleanup/{analysis_job_id}/process", response_class=HTMLResponse)
    async def scan_cleanup_process_submit(request: Request, analysis_job_id: str):
        analysis_job = get_job(analysis_job_id, active_settings)
        if analysis_job is None:
            raise HTTPException(status_code=404, detail="Analysis job not found.")
        analysis_payload = analysis_job.artifact_json.get("analysis")
        if not analysis_payload:
            raise HTTPException(status_code=400, detail="Analysis data is not ready yet.")

        form = await request.form()
        default_settings = parse_scan_defaults(form, active_settings)
        defaults = {
            "strength": default_settings.strength,
            "white_point": default_settings.white_point,
            "contrast": default_settings.contrast,
            "dpi_cap": default_settings.dpi_cap,
            "jpeg_quality": default_settings.jpeg_quality,
        }
        page_overrides = parse_page_overrides(form)
        process_job = create_job(
            "scan-cleanup-process",
            "Process Scan Cleanup",
            [Path(analysis_job.input_paths[0])],
            params_json={
                "analysis": analysis_payload,
                "defaults": defaults,
                "page_overrides": page_overrides,
            },
            settings=active_settings,
        )
        enqueue_scan_process(process_job.id, active_settings)
        return render_job_card(request, templates, process_job.id, active_settings)

    @app.get("/jobs/{job_id}", response_class=HTMLResponse)
    async def job_detail(request: Request, job_id: str):
        job = get_job(job_id, active_settings)
        if job is None:
            raise HTTPException(status_code=404, detail="Job not found.")
        context = {
            "app_name": active_settings.app_name,
            "job": serialize_job(job),
        }
        if request.headers.get("hx-request") == "true" or request.query_params.get("partial") == "1":
            return render_job_card(request, templates, job_id, active_settings)
        return templates.TemplateResponse(request, "job_detail.html", context)

    @app.get("/downloads/{job_id}")
    async def download_result(job_id: str):
        job = get_job(job_id, active_settings)
        if job is None or not job.output_path:
            raise HTTPException(status_code=404, detail="Result not found.")
        return FileResponse(job.output_path, filename=Path(job.output_path).name)

    @app.get("/previews/{job_id}/{filename}")
    async def preview_artifact(job_id: str, filename: str):
        preview_path = active_settings.previews_dir / job_id / filename
        if not preview_path.exists():
            raise HTTPException(status_code=404, detail="Preview not found.")
        return FileResponse(preview_path)

    return app

def run() -> None:
    import uvicorn

    settings = get_settings()
    uvicorn.run(
        "pdf_toolkit.web.app:create_app",
        host=settings.host,
        port=settings.port,
        factory=True,
        reload=settings.debug,
    )
