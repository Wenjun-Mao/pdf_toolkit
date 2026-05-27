# Mixed to PDF Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a `mixed-to-pdf` tool that combines PDFs and images into one PDF in a user-selected whole-file order.

**Architecture:** Add a focused `pdf_ops` operation that normalizes image inputs into temporary one-page PDF segments and preserves PDF inputs as segments. Wire it through CLI, jobs, and a two-step web workflow where upload creates an `awaiting_settings` job and the review form submits the final order before queueing processing.

**Tech Stack:** Python 3.12, FastAPI, Jinja2, HTMX, Alpine.js, PyMuPDF, Pillow, tenacity, pytest, uv.

---

## File Structure

- Create `pdf_toolkit/pdf_ops/mixed_to_pdf.py`: core mixed PDF assembly and input classification.
- Modify `pdf_toolkit/pdf_ops/__init__.py`: export `mixed_files_to_pdf`.
- Modify `tests/test_pdf_ops.py`: core operation tests.
- Create `tests/test_cli.py`: CLI dispatch test.
- Modify `pdf_toolkit/cli.py`: add `mixed-to-pdf` command.
- Create `tests/test_jobs.py`: inline job integration test.
- Modify `pdf_toolkit/jobs.py`: add enqueue, runner, and job implementation for mixed output.
- Create `pdf_toolkit/web/rendering.py`: shared job serialization and partial rendering helpers extracted from `web/app.py`.
- Modify `pdf_toolkit/web/app.py`: import shared rendering helpers, register mixed routes, add registry entry, remove moved helper code.
- Create `pdf_toolkit/web/mixed_to_pdf.py`: upload/review/submit route registration and order validation.
- Create `pdf_toolkit/web/templates/partials/forms/mixed_to_pdf.html`: initial upload form.
- Create `pdf_toolkit/web/templates/partials/forms/mixed_to_pdf_review.html`: order review and image option form.
- Modify `pdf_toolkit/web/templates/base.html`: add nav link.
- Modify `pdf_toolkit/web/templates/tool_page.html`: add a Mixed to PDF note.
- Modify `pdf_toolkit/web/templates/partials/job_card.html`: avoid scan-specific awaiting text for mixed draft jobs.
- Modify `pdf_toolkit/web/static/app.css`: styles for review rows and select controls.
- Create `tests/test_web_mixed_to_pdf.py`: web workflow tests.
- Modify `README.md`: feature list and CLI usage.

---

### Task 1: Core Operation Tests

**Files:**
- Modify: `tests/test_pdf_ops.py`

- [ ] **Step 1: Add failing tests for mixed PDF assembly**

Update the imports at the top of `tests/test_pdf_ops.py`:

```python
from pathlib import Path

import fitz
import pytest
from PIL import Image, ImageDraw

from pdf_toolkit.pdf_ops import (
    extract_embedded_images,
    extract_pages,
    id_halves_to_pdf,
    images_to_pdf,
    merge_pdfs,
    mixed_files_to_pdf,
    split_pdf,
)
```

Append these tests after `test_images_to_pdf_letter_fill_crops_without_distortion`:

```python
def test_mixed_files_to_pdf_preserves_pdf_image_pdf_order(
    tmp_path: Path,
    sample_merge_pdfs: list[Path],
    sample_image_inputs: list[Path],
) -> None:
    output_path = tmp_path / "mixed.pdf"

    mixed_files_to_pdf(
        [sample_merge_pdfs[1], sample_image_inputs[0], sample_merge_pdfs[0]],
        output_path,
    )

    with fitz.open(output_path) as output_doc:
        assert output_doc.page_count == 4
        assert "Second PDF / Page 1" in output_doc[0].get_text()
        assert output_doc[1].get_images(full=True)
        assert "First PDF / Page 1" in output_doc[2].get_text()
        assert "First PDF / Page 2" in output_doc[3].get_text()


def test_mixed_files_to_pdf_applies_image_page_options(
    tmp_path: Path,
    sample_image_inputs: list[Path],
) -> None:
    output_path = tmp_path / "mixed-image-options.pdf"

    mixed_files_to_pdf(
        sample_image_inputs[:1],
        output_path,
        page_size="letter",
        margin_mm=12.7,
        placement="fit",
    )

    with fitz.open(output_path) as output_doc:
        page = output_doc[0]
        assert output_doc.page_count == 1
        assert round(page.rect.width, 2) == 612.0
        assert round(page.rect.height, 2) == 792.0
        image_xref = page.get_images(full=True)[0][0]
        image_rect = page.get_image_rects(image_xref)[0]
        assert round(image_rect.width, 2) == 480.0
        assert round(image_rect.height, 2) == 720.0


def test_mixed_files_to_pdf_rejects_unsupported_inputs(tmp_path: Path) -> None:
    unsupported_path = tmp_path / "notes.docx"
    unsupported_path.write_bytes(b"not a supported input")

    with pytest.raises(ValueError, match="Unsupported mixed PDF input: notes.docx"):
        mixed_files_to_pdf([unsupported_path], tmp_path / "mixed.pdf")
```

- [ ] **Step 2: Run the focused tests and verify they fail**

Run:

```powershell
uv run pytest tests/test_pdf_ops.py -k mixed -q
```

Expected: collection fails with an import error for `mixed_files_to_pdf`.

---

### Task 2: Core Operation Implementation

**Files:**
- Create: `pdf_toolkit/pdf_ops/mixed_to_pdf.py`
- Modify: `pdf_toolkit/pdf_ops/__init__.py`
- Test: `tests/test_pdf_ops.py`

- [ ] **Step 1: Create the mixed PDF operation**

Create `pdf_toolkit/pdf_ops/mixed_to_pdf.py`:

```python
from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory

import fitz
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from .images_to_pdf import images_to_pdf
from .merge import merge_pdfs

PDF_EXTENSIONS = {".pdf"}
IMAGE_EXTENSIONS = {
    ".bmp",
    ".gif",
    ".jpeg",
    ".jpg",
    ".png",
    ".tif",
    ".tiff",
    ".webp",
}


@retry(
    retry=retry_if_exception_type((RuntimeError, OSError, ValueError)),
    wait=wait_exponential(multiplier=0.25, min=0.25, max=2),
    stop=stop_after_attempt(3),
    reraise=True,
)
def mixed_files_to_pdf(
    source_paths: list[Path],
    output_path: Path,
    *,
    fallback_dpi: int = 300,
    jpeg_quality: int = 95,
    page_size: str = "original",
    margin_mm: float = 0.0,
    placement: str = "fit",
) -> Path:
    """Normalize PDFs and images into ordered PDF segments, then write one PDF."""

    if not source_paths:
        raise ValueError("At least one PDF or image file is required.")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix="mixed-to-pdf-") as temp_dir_name:
        temp_dir = Path(temp_dir_name)
        pdf_segments: list[Path] = []

        for index, source_path in enumerate(source_paths, start=1):
            input_kind = classify_mixed_input(source_path)
            if input_kind == "pdf":
                _validate_pdf(source_path)
                pdf_segments.append(source_path)
                continue

            segment_path = temp_dir / f"{index:03d}-image.pdf"
            images_to_pdf(
                [source_path],
                segment_path,
                fallback_dpi=fallback_dpi,
                jpeg_quality=jpeg_quality,
                page_size=page_size,
                margin_mm=margin_mm,
                placement=placement,
            )
            pdf_segments.append(segment_path)

        _write_pdf_segments(pdf_segments, output_path)

    return output_path


def classify_mixed_input(source_path: Path) -> str:
    suffix = source_path.suffix.lower()
    if suffix in PDF_EXTENSIONS:
        return "pdf"
    if suffix in IMAGE_EXTENSIONS:
        return "image"
    raise ValueError(f"Unsupported mixed PDF input: {source_path.name}. Use PDF or image files.")


def _validate_pdf(source_path: Path) -> None:
    if not source_path.exists():
        raise FileNotFoundError(f"PDF file was not found: {source_path}")

    try:
        with fitz.open(source_path) as document:
            if document.page_count < 1:
                raise ValueError(f"PDF contains no pages: {source_path.name}")
    except (RuntimeError, ValueError) as exc:
        raise ValueError(f"Could not read PDF file {source_path.name}: {exc}") from exc


def _write_pdf_segments(pdf_segments: list[Path], output_path: Path) -> None:
    if len(pdf_segments) == 1:
        _resave_pdf_segment(pdf_segments[0], output_path)
        return

    merge_pdfs(pdf_segments, output_path)


def _resave_pdf_segment(source_path: Path, output_path: Path) -> None:
    with fitz.open(source_path) as source_document:
        output_document = fitz.open()
        try:
            output_document.insert_pdf(source_document)
            output_document.save(output_path, garbage=4, deflate=True)
        finally:
            output_document.close()
```

- [ ] **Step 2: Export the operation**

In `pdf_toolkit/pdf_ops/__init__.py`, add the import:

```python
from .mixed_to_pdf import mixed_files_to_pdf
```

Add `"mixed_files_to_pdf"` to `__all__`:

```python
    "mixed_files_to_pdf",
```

- [ ] **Step 3: Run the core mixed tests**

Run:

```powershell
uv run pytest tests/test_pdf_ops.py -k mixed -q
```

Expected: all mixed tests pass.

- [ ] **Step 4: Run the full PDF operation tests**

Run:

```powershell
uv run pytest tests/test_pdf_ops.py tests/test_ranges.py -q
```

Expected: all selected tests pass.

- [ ] **Step 5: Commit the core operation**

Run:

```powershell
git add pdf_toolkit/pdf_ops/mixed_to_pdf.py pdf_toolkit/pdf_ops/__init__.py tests/test_pdf_ops.py
git commit -m "Add mixed PDF core operation"
```

---

### Task 3: CLI Integration

**Files:**
- Create: `tests/test_cli.py`
- Modify: `pdf_toolkit/cli.py`

- [ ] **Step 1: Add a failing CLI dispatch test**

Create `tests/test_cli.py`:

```python
from __future__ import annotations

import sys
from pathlib import Path

from pdf_toolkit import cli


def test_mixed_to_pdf_cli_dispatches_order_and_image_options(
    monkeypatch,
    tmp_path: Path,
    capsys,
) -> None:
    output_path = tmp_path / "mixed.pdf"
    first_pdf = tmp_path / "first.pdf"
    image_path = tmp_path / "image.png"
    second_pdf = tmp_path / "second.pdf"
    first_pdf.write_bytes(b"%PDF-1.7\n")
    image_path.write_bytes(b"image")
    second_pdf.write_bytes(b"%PDF-1.7\n")
    calls: dict[str, object] = {}

    def fake_mixed_files_to_pdf(
        source_paths: list[Path],
        output_path_arg: Path,
        *,
        fallback_dpi: int,
        jpeg_quality: int,
        page_size: str,
        margin_mm: float,
        placement: str,
    ) -> Path:
        calls["source_paths"] = source_paths
        calls["output_path"] = output_path_arg
        calls["fallback_dpi"] = fallback_dpi
        calls["jpeg_quality"] = jpeg_quality
        calls["page_size"] = page_size
        calls["margin_mm"] = margin_mm
        calls["placement"] = placement
        output_path_arg.write_bytes(b"%PDF-1.7\n%%EOF\n")
        return output_path_arg

    monkeypatch.setattr(cli, "mixed_files_to_pdf", fake_mixed_files_to_pdf)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "pdfkit",
            "mixed-to-pdf",
            str(output_path),
            str(first_pdf),
            str(image_path),
            str(second_pdf),
            "--fallback-dpi",
            "240",
            "--jpeg-quality",
            "91",
            "--page-size",
            "letter",
            "--margin-mm",
            "6.5",
            "--placement",
            "fill",
        ],
    )

    cli.main()

    assert calls["source_paths"] == [first_pdf, image_path, second_pdf]
    assert calls["output_path"] == output_path
    assert calls["fallback_dpi"] == 240
    assert calls["jpeg_quality"] == 91
    assert calls["page_size"] == "letter"
    assert calls["margin_mm"] == 6.5
    assert calls["placement"] == "fill"
    assert str(output_path.resolve()) in capsys.readouterr().out
```

- [ ] **Step 2: Run the CLI test and verify it fails**

Run:

```powershell
uv run pytest tests/test_cli.py -q
```

Expected: the test fails because `pdf_toolkit.cli` does not import or dispatch `mixed_files_to_pdf`.

- [ ] **Step 3: Add the CLI command**

In `pdf_toolkit/cli.py`, add `mixed_files_to_pdf` to the existing multi-line `from .pdf_ops import` block:

```python
    mixed_files_to_pdf,
```

Add this parser after the `images-to-pdf` parser:

```python
    mixed_parser = subparsers.add_parser("mixed-to-pdf", help="Combine PDFs and images into one PDF.")
    mixed_parser.add_argument("output", type=Path)
    mixed_parser.add_argument("inputs", nargs="+", type=Path)
    mixed_parser.add_argument("--fallback-dpi", type=int, default=300)
    mixed_parser.add_argument("--jpeg-quality", type=int, default=95)
    mixed_parser.add_argument("--page-size", type=str, default="original", choices=["original", "a4", "letter"])
    mixed_parser.add_argument("--margin-mm", type=float, default=0.0)
    mixed_parser.add_argument("--placement", type=str, default="fit", choices=["fit", "fill"])
```

Add this branch in `main()` after the `images-to-pdf` branch:

```python
    if args.command == "mixed-to-pdf":
        mixed_files_to_pdf(
            args.inputs,
            args.output,
            fallback_dpi=args.fallback_dpi,
            jpeg_quality=args.jpeg_quality,
            page_size=args.page_size,
            margin_mm=args.margin_mm,
            placement=args.placement,
        )
        print(args.output.resolve())
        return
```

- [ ] **Step 4: Run CLI tests**

Run:

```powershell
uv run pytest tests/test_cli.py -q
```

Expected: the CLI test passes.

- [ ] **Step 5: Commit CLI integration**

Run:

```powershell
git add pdf_toolkit/cli.py tests/test_cli.py
git commit -m "Add mixed PDF CLI command"
```

---

### Task 4: Job Integration

**Files:**
- Create: `tests/test_jobs.py`
- Modify: `pdf_toolkit/jobs.py`

- [ ] **Step 1: Add a failing inline job test**

Create `tests/test_jobs.py`:

```python
from __future__ import annotations

from pathlib import Path

import fitz

from pdf_toolkit.jobs import create_job, enqueue_mixed_to_pdf, get_job


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
```

- [ ] **Step 2: Run the job test and verify it fails**

Run:

```powershell
uv run pytest tests/test_jobs.py -q
```

Expected: collection fails because `enqueue_mixed_to_pdf` does not exist.

- [ ] **Step 3: Add job wiring**

In `pdf_toolkit/jobs.py`, add `mixed_files_to_pdf` to the existing multi-line `from .pdf_ops import` block:

```python
    mixed_files_to_pdf,
```

Add this enqueue function after `enqueue_images_to_pdf`:

```python
def enqueue_mixed_to_pdf(job_id: str, settings: Settings | None = None):
    active_settings = settings or get_settings()
    return dispatch_job(run_mixed_to_pdf_job, job_id, settings=active_settings)
```

Add this runner after `run_images_to_pdf_job`:

```python
def run_mixed_to_pdf_job(job_id: str) -> str:
    return _run_job(job_id, JobStatus.PROCESSING, _mixed_to_pdf_job_impl)
```

Add this implementation after `_images_to_pdf_job_impl`:

```python
def _mixed_to_pdf_job_impl(job: Job) -> Path:
    output_path = build_result_path(job.id, "mixed.pdf")
    mixed_files_to_pdf(
        [Path(path) for path in job.input_paths],
        output_path,
        fallback_dpi=int(job.params_json.get("fallback_dpi", 300)),
        jpeg_quality=int(job.params_json.get("jpeg_quality", 95)),
        page_size=str(job.params_json.get("page_size", "original")),
        margin_mm=float(job.params_json.get("margin_mm", 0.0)),
        placement=str(job.params_json.get("placement", "fit")),
    )
    return output_path
```

- [ ] **Step 4: Run job tests**

Run:

```powershell
uv run pytest tests/test_jobs.py -q
```

Expected: the job test passes.

- [ ] **Step 5: Commit job integration**

Run:

```powershell
git add pdf_toolkit/jobs.py tests/test_jobs.py
git commit -m "Add mixed PDF job processing"
```

---

### Task 5: Extract Web Rendering Helpers

**Files:**
- Create: `pdf_toolkit/web/rendering.py`
- Modify: `pdf_toolkit/web/app.py`
- Test: `tests/test_auth_and_jobs.py`

- [ ] **Step 1: Run current web tests as a refactor baseline**

Run:

```powershell
uv run pytest tests/test_auth_and_jobs.py -q
```

Expected: current web tests pass before helper extraction.

- [ ] **Step 2: Create shared rendering helpers**

Create `pdf_toolkit/web/rendering.py`:

```python
from __future__ import annotations

from pathlib import Path

from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from ..jobs import get_job
from ..models import Job, JobStatus
from ..settings import Settings


def serialize_job(job: Job) -> dict:
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
    return templates.TemplateResponse(
        request,
        "partials/job_card.html",
        {"job": serialize_job(job)},
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
```

- [ ] **Step 3: Update `web/app.py` to use shared helpers**

Add this import near the other local web imports:

```python
from .rendering import render_job_card, render_notice, serialize_job
```

Replace each `_render_job_card(` call with `render_job_card(`.

Replace each `_render_notice(` call with `render_notice(`.

Remove these now-unused imports from `pdf_toolkit/web/app.py`:

```python
from ..models import Job, JobStatus
```

Add this import because `scan_cleanup_process_submit` still passes a status string through `update_job_fields` only indirectly through job functions:

```python
from ..models import JobStatus
```

If `JobStatus` is unused after the replacement, remove the `JobStatus` import too.

Delete these functions from `pdf_toolkit/web/app.py` after confirming all references point to `rendering.py`:

```python
def serialize_job(job: Job) -> dict:
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
        "awaiting_settings": job.status == JobStatus.AWAITING_SETTINGS.value,
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
    return payload
```

Delete `_render_job_card` and `_render_notice` from `pdf_toolkit/web/app.py`; their full replacements are in `pdf_toolkit/web/rendering.py`.

- [ ] **Step 4: Update the awaiting settings template branch**

In `pdf_toolkit/web/templates/partials/job_card.html`, replace:

```html
    {% if job.awaiting_settings %}
        <p class="muted">Analysis is ready. Review previews and cleanup settings before running the final pass.</p>
        <a class="primary-button link-button" href="/jobs/{{ job.id }}">Open Analysis</a>
```

with:

```html
    {% if job.awaiting_scan_settings %}
        <p class="muted">Analysis is ready. Review previews and cleanup settings before running the final pass.</p>
        <a class="primary-button link-button" href="/jobs/{{ job.id }}">Open Analysis</a>
    {% elif job.awaiting_settings %}
        <p class="muted">This job is waiting for review before processing.</p>
```

- [ ] **Step 5: Run web tests after the refactor**

Run:

```powershell
uv run pytest tests/test_auth_and_jobs.py -q
```

Expected: current web tests still pass.

- [ ] **Step 6: Commit the web helper extraction**

Run:

```powershell
git add pdf_toolkit/web/app.py pdf_toolkit/web/rendering.py pdf_toolkit/web/templates/partials/job_card.html
git commit -m "Extract web rendering helpers"
```

---

### Task 6: Web Workflow Tests

**Files:**
- Create: `tests/test_web_mixed_to_pdf.py`

- [ ] **Step 1: Add failing tests for the two-step web workflow**

Create `tests/test_web_mixed_to_pdf.py`:

```python
from __future__ import annotations

import re
from pathlib import Path


def _upload_tuple(path: Path) -> tuple[str, tuple[str, bytes, str]]:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        content_type = "application/pdf"
    elif suffix in {".jpg", ".jpeg"}:
        content_type = "image/jpeg"
    else:
        content_type = "image/png"
    return ("files", (path.name, path.read_bytes(), content_type))


def _upload_mixed_job(app_client, paths: list[Path]) -> str:
    response = app_client.post(
        "/tools/mixed-to-pdf/upload",
        files=[_upload_tuple(path) for path in paths],
    )
    assert response.status_code == 200
    assert "Build Mixed PDF" in response.text
    match = re.search(r"/tools/mixed-to-pdf/([0-9a-f-]+)/submit", response.text)
    assert match is not None
    return match.group(1)


def test_mixed_to_pdf_tool_page_is_available(app_client) -> None:
    response = app_client.get("/tools/mixed-to-pdf")

    assert response.status_code == 200
    assert "Mixed to PDF" in response.text
    assert 'accept="application/pdf,image/*"' in response.text


def test_mixed_to_pdf_upload_review_and_submit_completes_inline(
    app_client,
    sample_merge_pdfs: list[Path],
    sample_image_inputs: list[Path],
) -> None:
    ordered_uploads = [sample_merge_pdfs[0], sample_image_inputs[0], sample_merge_pdfs[1]]
    job_id = _upload_mixed_job(app_client, ordered_uploads)

    response = app_client.post(
        f"/tools/mixed-to-pdf/{job_id}/submit",
        data=[
            ("ordered_file_ids", f"02-{sample_image_inputs[0].name}"),
            ("ordered_file_ids", f"01-{sample_merge_pdfs[0].name}"),
            ("ordered_file_ids", f"03-{sample_merge_pdfs[1].name}"),
            ("fallback_dpi", "300"),
            ("jpeg_quality", "95"),
            ("page_size", "letter"),
            ("margin_mm", "12.7"),
            ("placement", "fit"),
        ],
    )

    assert response.status_code == 200
    assert "Download mixed.pdf" in response.text


def test_mixed_to_pdf_submit_rejects_unknown_file_id(
    app_client,
    sample_merge_pdfs: list[Path],
    sample_image_inputs: list[Path],
) -> None:
    job_id = _upload_mixed_job(app_client, [sample_merge_pdfs[0], sample_image_inputs[0]])

    response = app_client.post(
        f"/tools/mixed-to-pdf/{job_id}/submit",
        data=[
            ("ordered_file_ids", "missing.pdf"),
            ("fallback_dpi", "300"),
            ("jpeg_quality", "95"),
            ("page_size", "original"),
            ("margin_mm", "0"),
            ("placement", "fit"),
        ],
    )

    assert response.status_code == 400
    assert "Submitted file order does not match uploaded files." in response.text
```

- [ ] **Step 2: Run web mixed tests and verify they fail**

Run:

```powershell
uv run pytest tests/test_web_mixed_to_pdf.py -q
```

Expected: tests fail because `/tools/mixed-to-pdf` and the mixed upload routes do not exist.

---

### Task 7: Web Workflow Implementation

**Files:**
- Create: `pdf_toolkit/web/mixed_to_pdf.py`
- Create: `pdf_toolkit/web/templates/partials/forms/mixed_to_pdf.html`
- Create: `pdf_toolkit/web/templates/partials/forms/mixed_to_pdf_review.html`
- Modify: `pdf_toolkit/web/app.py`
- Modify: `pdf_toolkit/web/templates/base.html`
- Modify: `pdf_toolkit/web/templates/tool_page.html`
- Modify: `pdf_toolkit/web/static/app.css`
- Test: `tests/test_web_mixed_to_pdf.py`

- [ ] **Step 1: Create route registration for mixed workflow**

Create `pdf_toolkit/web/mixed_to_pdf.py`:

```python
from __future__ import annotations

import re
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from loguru import logger

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
        try:
            if not files:
                raise ValueError("Select at least one PDF or image file.")
            job = create_job("mixed-to-pdf", "Mixed to PDF", [], settings=active_settings)
            stored_paths = await persist_uploads(job.id, files, active_settings)
            if not stored_paths:
                raise ValueError("Select at least one PDF or image file.")
            for stored_path in stored_paths:
                classify_mixed_input(stored_path)
            update_job_fields(
                job.id,
                active_settings,
                status=JobStatus.AWAITING_SETTINGS.value,
                input_paths=[str(path) for path in stored_paths],
            )
            return _render_review(request, templates, job.id, stored_paths)
        except Exception as exc:
            logger.exception("Mixed-to-PDF upload failed")
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
```

- [ ] **Step 2: Add the initial upload form**

Create `pdf_toolkit/web/templates/partials/forms/mixed_to_pdf.html`:

```html
<form
    class="stacked-form"
    hx-post="/tools/mixed-to-pdf/upload"
    hx-target="#job-panel"
    hx-swap="innerHTML"
    hx-encoding="multipart/form-data"
>
    <label>
        PDF and Image Files
        <input type="file" name="files" accept="application/pdf,image/*" multiple required>
    </label>
    <button class="primary-button" type="submit">Review Order</button>
</form>
```

- [ ] **Step 3: Add the order review partial**

Create `pdf_toolkit/web/templates/partials/forms/mixed_to_pdf_review.html`:

```html
<form
    class="stacked-form"
    hx-post="/tools/mixed-to-pdf/{{ job_id }}/submit"
    hx-target="#job-panel"
    hx-swap="innerHTML"
    x-data="{
        items: {{ items | tojson }},
        move(index, direction) {
            const target = index + direction;
            if (target < 0 || target >= this.items.length) {
                return;
            }
            const item = this.items.splice(index, 1)[0];
            this.items.splice(target, 0, item);
        }
    }"
>
    <div class="section-header">
        <h2>Review Order</h2>
        <p class="muted">Use the controls to set the whole-file order.</p>
    </div>

    <div class="order-list">
        <template x-for="(item, index) in items" :key="item.id">
            <div class="order-row">
                <input type="hidden" name="ordered_file_ids" :value="item.id">
                <span class="order-index" x-text="index + 1"></span>
                <div class="order-meta">
                    <strong x-text="item.name"></strong>
                    <span x-text="item.kind"></span>
                </div>
                <div class="order-actions">
                    <button class="ghost-button mini-button" type="button" x-on:click="move(index, -1)" x-bind:disabled="index === 0">Up</button>
                    <button class="ghost-button mini-button" type="button" x-on:click="move(index, 1)" x-bind:disabled="index === items.length - 1">Down</button>
                </div>
            </div>
        </template>
    </div>

    <label>
        Fallback DPI
        <input type="number" name="fallback_dpi" min="72" max="600" step="1" value="300">
    </label>
    <label>
        JPEG Quality
        <input type="number" name="jpeg_quality" min="70" max="100" step="1" value="95">
    </label>
    <label>
        Page Size
        <select name="page_size">
            <option value="original" selected>Original</option>
            <option value="a4">A4</option>
            <option value="letter">Letter</option>
        </select>
    </label>
    <label>
        Margin (mm)
        <input type="number" name="margin_mm" min="0" max="50" step="0.5" value="0">
    </label>
    <label>
        Placement
        <select name="placement">
            <option value="fit" selected>Fit</option>
            <option value="fill">Fill</option>
        </select>
    </label>
    <button class="primary-button" type="submit">Build Mixed PDF</button>
</form>
```

- [ ] **Step 4: Register the mixed tool in `web/app.py`**

Add this import:

```python
from .mixed_to_pdf import register_mixed_to_pdf_routes
```

Add this entry to `TOOL_REGISTRY` after `merge`:

```python
    "mixed-to-pdf": {
        "title": "Mixed to PDF",
        "description": "Combine PDFs and images into one PDF after reviewing the whole-file order.",
        "form_template": "partials/forms/mixed_to_pdf.html",
    },
```

Call the route registration immediately after the static files mount:

```python
    register_mixed_to_pdf_routes(app, templates, active_settings)
```

- [ ] **Step 5: Update navigation and notes**

In `pdf_toolkit/web/templates/base.html`, add this nav link after Merge:

```html
                <a href="/tools/mixed-to-pdf">Mixed to PDF</a>
```

In `pdf_toolkit/web/templates/tool_page.html`, add this list item in the notes list:

```html
            <li>`Mixed to PDF` accepts PDFs and images, then lets you review whole-file order before building the output.</li>
```

- [ ] **Step 6: Add review UI styles**

In `pdf_toolkit/web/static/app.css`, replace:

```css
input,
button,
summary {
    font: inherit;
}

input {
    width: 100%;
    padding: 0.8rem 0.95rem;
    border-radius: 14px;
    border: 1px solid rgba(31, 42, 55, 0.16);
    background: rgba(255, 255, 255, 0.82);
}
```

with:

```css
input,
button,
summary,
select {
    font: inherit;
}

input,
select {
    width: 100%;
    padding: 0.8rem 0.95rem;
    border-radius: 14px;
    border: 1px solid rgba(31, 42, 55, 0.16);
    background: rgba(255, 255, 255, 0.82);
}
```

Add these styles after `.job-header, .section-header`:

```css
.order-list {
    display: grid;
    gap: 0.65rem;
}

.order-row {
    display: grid;
    grid-template-columns: auto minmax(0, 1fr) auto;
    align-items: center;
    gap: 0.8rem;
    padding: 0.75rem;
    border: 1px solid rgba(31, 42, 55, 0.12);
    border-radius: 16px;
    background: rgba(255, 255, 255, 0.58);
}

.order-index {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 2rem;
    height: 2rem;
    border-radius: 999px;
    background: rgba(31, 42, 55, 0.08);
    font-weight: 700;
}

.order-meta {
    display: grid;
    gap: 0.15rem;
    min-width: 0;
}

.order-meta strong,
.order-meta span {
    overflow-wrap: anywhere;
}

.order-actions {
    display: flex;
    gap: 0.4rem;
}

.mini-button {
    padding: 0.45rem 0.7rem;
}

button:disabled {
    cursor: not-allowed;
    opacity: 0.45;
}
```

- [ ] **Step 7: Run mixed web tests**

Run:

```powershell
uv run pytest tests/test_web_mixed_to_pdf.py -q
```

Expected: mixed web tests pass.

- [ ] **Step 8: Run existing web tests**

Run:

```powershell
uv run pytest tests/test_auth_and_jobs.py tests/test_web_mixed_to_pdf.py -q
```

Expected: existing and new web tests pass.

- [ ] **Step 9: Commit web workflow**

Run:

```powershell
git add pdf_toolkit/web/app.py pdf_toolkit/web/mixed_to_pdf.py pdf_toolkit/web/static/app.css pdf_toolkit/web/templates/base.html pdf_toolkit/web/templates/tool_page.html pdf_toolkit/web/templates/partials/forms/mixed_to_pdf.html pdf_toolkit/web/templates/partials/forms/mixed_to_pdf_review.html tests/test_web_mixed_to_pdf.py
git commit -m "Add mixed PDF web workflow"
```

---

### Task 8: Documentation and Final Verification

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Update README features**

In `README.md`, add this bullet after `Merge 2+ PDFs into one output`:

```markdown
- Combine PDFs and images into one ordered PDF
```

- [ ] **Step 2: Update README CLI usage**

In the CLI usage block, add this command after the merge command:

```powershell
uv run pdfkit mixed-to-pdf mixed.pdf file1.pdf photo.jpg file2.pdf --page-size letter --margin-mm 12.7 --placement fit
```

- [ ] **Step 3: Run the full test suite**

Run:

```powershell
uv run pytest -q
```

Expected: all tests pass.

- [ ] **Step 4: Run a CLI smoke command**

Run:

```powershell
uv run pdfkit mixed-to-pdf data/smoke/mixed.pdf data/smoke/imgs-letter-fit.pdf data/imgs/1.png --page-size letter --margin-mm 12.7 --placement fit
```

Expected: command prints the absolute path to `data/smoke/mixed.pdf`.

- [ ] **Step 5: Start the local web app for visual verification**

Run:

```powershell
$env:PDFKIT_RUN_JOBS_INLINE = "true"
uv run pdfkit-web
```

Expected: the server starts on the configured local port. Open `/tools/mixed-to-pdf`, upload one PDF and one image, confirm the review list appears, move an item, submit, and confirm the output card offers `Download mixed.pdf`.

- [ ] **Step 6: Commit docs and verification touch-up**

Run:

```powershell
git add README.md
git commit -m "Document mixed PDF usage"
```

---

## Self-Review Notes

- Spec coverage: core PDF and image assembly is covered by Tasks 1-2; CLI by Task 3; job processing by Task 4; web upload/review/submit by Tasks 5-7; README and verification by Task 8.
- Scope check: Microsoft Word and page-level assembly are not included in implementation tasks.
- Type consistency: the public function is `mixed_files_to_pdf`; the web route registration function is `register_mixed_to_pdf_routes`; the job entry point is `enqueue_mixed_to_pdf`.
