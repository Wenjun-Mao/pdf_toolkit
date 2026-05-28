# Scan Cleanup Compare Preview Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an in-browser scan-cleanup preview loop that shows the original page beside a processed preview for the currently selected page and settings.

**Architecture:** Keep the final cleanup job unchanged. Add a lightweight single-page preview renderer that uses the same cleanup algorithm as final output, stores cached preview JPEGs under the existing job preview folder, and expose it through an HTMX endpoint on the scan-cleanup review UI. The UI starts with page 1, lets users pick another page, and updates the processed side without creating or downloading a full PDF.

**Tech Stack:** Python 3.12, FastAPI, Jinja2, HTMX, Alpine.js, PyMuPDF, OpenCV, Pillow, pytest, uv.

---

## Best UX Shape

The side-by-side idea is the right direction. The lowest-bloat version is:

- A `Preview page` selector inside the existing scan cleanup settings form.
- A two-column `Original` / `Processed` comparison panel above the existing full-page thumbnail gallery.
- An automatic initial preview for page 1 when the analysis screen opens.
- Process and render only the selected page for the processed-side preview; never generate a full cleaned PDF or process every page just to update this preview.
- A manual `Update preview` button for knob changes, so typing in numeric inputs does not continuously re-render pages.
- Auto-refresh on page selector changes, because page changes are intentional and infrequent.
- Cached processed preview images keyed by page plus normalized settings, so repeated checks with the same values are instant.

Do not add a full page reordering UI, image diff mode, zoom/pan, or full-document preview in this pass. Those belong later if the basic loop proves useful.

---

## File Structure

- Modify `pdf_toolkit/pdf_ops/scan_cleanup.py`: add a public helper that renders one cleaned preview image from one PDF page using the same cleanup pipeline as final output.
- Create `pdf_toolkit/web/scan_cleanup_forms.py`: share parsing of scan defaults, page overrides, and selected page settings between final processing and preview rendering.
- Create `pdf_toolkit/web/scan_cleanup_preview.py`: register the HTMX preview route and handle validation, cache keying, rendering, and partial response context.
- Modify `pdf_toolkit/web/app.py`: register the preview routes and use the shared form parser in the existing final-process route.
- Modify `pdf_toolkit/web/rendering.py`: include scan default values in serialized review jobs so the form and initial preview controls are populated from settings.
- Modify `pdf_toolkit/web/templates/partials/scan_settings_form.html`: add selected-page controls and preview update triggers inside the existing form.
- Create `pdf_toolkit/web/templates/partials/scan_cleanup_compare.html`: render original/processed comparison results.
- Modify `pdf_toolkit/web/templates/partials/scan_cleanup_review_body.html`: place the comparison area before the existing thumbnail gallery.
- Modify `pdf_toolkit/web/static/app.css`: add compact comparison layout, page selector row, and mobile stacking.
- Modify `tests/test_scan_cleanup.py`: cover the single-page processed preview renderer.
- Modify `tests/test_auth_and_jobs.py`: cover review UI controls and preview endpoint behavior.

---

### Task 1: Core Preview Renderer Test

**Files:**
- Modify: `tests/test_scan_cleanup.py`
- Test: `tests/test_scan_cleanup.py`

- [ ] **Step 1: Write the failing unit test**

Update the imports at the top of `tests/test_scan_cleanup.py` so `render_cleaned_page_preview` is imported:

```python
from pdf_toolkit.pdf_ops.scan_cleanup import (
    GOLDEN_SCAN_PAGES,
    CleanupSettings,
    render_cleaned_page_preview,
)
```

Add this test after `test_scan_analysis_default_preview_is_readable_width`:

```python
def test_render_cleaned_page_preview_outputs_single_page_image(
    tmp_path: Path,
    sample_scan_pdf: Path,
) -> None:
    analysis = analyze_scan_pdf(sample_scan_pdf, tmp_path / "previews")
    output_path = tmp_path / "processed-preview.jpg"

    result = render_cleaned_page_preview(
        source_path=sample_scan_pdf,
        output_path=output_path,
        analysis=analysis,
        page_number=1,
        settings=CleanupSettings(strength=0.7, white_point=244, contrast=1.1, dpi_cap=300, jpeg_quality=92),
        preview_width_px=720,
    )

    assert result == output_path
    assert output_path.exists()
    with Image.open(output_path) as preview_image:
        assert preview_image.width >= 600
        assert preview_image.mode == "L"
```

- [ ] **Step 2: Run the focused test and verify it fails**

Run:

```powershell
uv run pytest -q tests/test_scan_cleanup.py::test_render_cleaned_page_preview_outputs_single_page_image
```

Expected: FAIL with `ImportError` or `AttributeError` because `render_cleaned_page_preview` does not exist.

---

### Task 2: Core Preview Renderer Implementation

**Files:**
- Modify: `pdf_toolkit/pdf_ops/scan_cleanup.py`
- Test: `tests/test_scan_cleanup.py`

- [ ] **Step 1: Add the single-page preview helper**

In `pdf_toolkit/pdf_ops/scan_cleanup.py`, add this public function immediately after `clean_scanned_pdf`:

```python
def render_cleaned_page_preview(
    source_path: Path,
    output_path: Path,
    analysis: ScanAnalysis,
    page_number: int,
    settings: CleanupSettings,
    preview_width_px: int = 900,
) -> Path:
    if page_number < 1 or page_number > analysis.page_count:
        raise ValueError(f"Preview page must be between 1 and {analysis.page_count}.")

    normalized_settings = settings.normalized()
    page_info = analysis.pages[page_number - 1]
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with fitz.open(source_path) as document:
        page = document[page_number - 1]
        render_dpi = _resolve_preview_render_dpi(
            page,
            page_info,
            normalized_settings,
            preview_width_px,
        )
        working_image = _render_page_rgb(page, render_dpi)

    cleaned_image = _clean_page_image(working_image, normalized_settings)
    output_path.write_bytes(
        _encode_grayscale_jpeg(cleaned_image, quality=normalized_settings.jpeg_quality)
    )
    return output_path
```

Add this private helper near `_resolve_render_dpi`:

```python
def _resolve_preview_render_dpi(
    page: fitz.Page,
    page_info: PageAnalysis,
    settings: CleanupSettings,
    preview_width_px: int,
) -> int:
    page_width_inches = page.rect.width / 72.0
    if page_width_inches <= 0:
        return _resolve_render_dpi(page_info, settings)
    preview_dpi = max(round(preview_width_px / page_width_inches), 1)
    return min(_resolve_render_dpi(page_info, settings), preview_dpi)
```

- [ ] **Step 2: Run the focused test and verify it passes**

Run:

```powershell
uv run pytest -q tests/test_scan_cleanup.py::test_render_cleaned_page_preview_outputs_single_page_image
```

Expected: PASS.

- [ ] **Step 3: Commit the core renderer**

Run:

```powershell
git add pdf_toolkit/pdf_ops/scan_cleanup.py tests/test_scan_cleanup.py
git commit -m "feat: render scan cleanup page previews"
```

---

### Task 3: Shared Scan Cleanup Form Parsing

**Files:**
- Create: `pdf_toolkit/web/scan_cleanup_forms.py`
- Modify: `pdf_toolkit/web/app.py`
- Test: `tests/test_auth_and_jobs.py`

- [ ] **Step 1: Add parser tests for final defaults and page overrides**

Append this test to `tests/test_auth_and_jobs.py` after `test_scan_cleanup_process_submission_stores_output_controls`:

```python
def test_scan_cleanup_process_submission_stores_page_overrides(
    open_app_client,
    sample_scan_pdf: Path,
    tmp_path: Path,
    monkeypatch,
) -> None:
    analysis = analyze_scan_pdf(sample_scan_pdf, tmp_path / "previews")
    analysis_payload = analysis.to_json()
    for page_payload in analysis_payload["pages"]:
        page_payload["preview_path"] = Path(page_payload["preview_path"]).name
    analysis_job = create_job(
        "scan-cleanup-analysis",
        "Analyze Scan Cleanup",
        [sample_scan_pdf],
    )
    update_job_fields(
        analysis_job.id,
        status=JobStatus.AWAITING_SETTINGS.value,
        artifact_json={"analysis": analysis_payload},
    )
    monkeypatch.setattr(web_app, "enqueue_scan_process", lambda job_id, settings: None)

    response = open_app_client.post(
        f"/tools/scan-cleanup/{analysis_job.id}/process",
        data={
            "strength": "0.65",
            "white_point": "242",
            "contrast": "1.05",
            "dpi_cap": "300",
            "jpeg_quality": "92",
            "page_1_strength": "0.8",
            "page_1_white_point": "246",
            "page_1_contrast": "1.2",
        },
    )

    match = re.search(r'id="job-([^"]+)"', response.text)
    assert response.status_code == 200
    assert match is not None
    process_job = get_job(match.group(1))
    assert process_job is not None
    assert process_job.params_json["page_overrides"] == {
        "1": {"strength": 0.8, "white_point": 246, "contrast": 1.2}
    }
```

- [ ] **Step 2: Run the focused parser regression**

Run:

```powershell
uv run pytest -q tests/test_auth_and_jobs.py::test_scan_cleanup_process_submission_stores_page_overrides
```

Expected: PASS before refactor. This pins existing behavior before moving parsing code.

- [ ] **Step 3: Create the shared parser module**

Create `pdf_toolkit/web/scan_cleanup_forms.py`:

```python
from __future__ import annotations

from starlette.datastructures import FormData

from ..pdf_ops import CleanupSettings
from ..settings import Settings


def parse_scan_defaults(form: FormData, settings: Settings) -> CleanupSettings:
    return CleanupSettings(
        strength=float(form.get("strength", settings.scan_default_strength)),
        white_point=int(form.get("white_point", settings.scan_default_white_point)),
        contrast=float(form.get("contrast", settings.scan_default_contrast)),
        dpi_cap=int(form.get("dpi_cap", settings.scan_default_dpi_cap)),
        jpeg_quality=int(form.get("jpeg_quality", settings.scan_default_jpeg_quality)),
    ).normalized()


def parse_page_overrides(form: FormData) -> dict[str, dict[str, float | int]]:
    overrides: dict[str, dict[str, float | int]] = {}
    for key, value in form.items():
        if not key.startswith("page_") or value in ("", None):
            continue
        _, page_number, setting_name = key.split("_", maxsplit=2)
        overrides.setdefault(page_number, {})
        if setting_name == "white_point":
            overrides[page_number][setting_name] = int(value)
        else:
            overrides[page_number][setting_name] = float(value)
    return {
        page_number: override
        for page_number, override in overrides.items()
        if override
    }


def settings_for_page(
    defaults: CleanupSettings,
    page_overrides: dict[str, dict[str, float | int]],
    page_number: int,
) -> CleanupSettings:
    override = page_overrides.get(str(page_number), {})
    return CleanupSettings(
        strength=float(override.get("strength", defaults.strength)),
        white_point=int(override.get("white_point", defaults.white_point)),
        contrast=float(override.get("contrast", defaults.contrast)),
        dpi_cap=defaults.dpi_cap,
        jpeg_quality=defaults.jpeg_quality,
    ).normalized()
```

- [ ] **Step 4: Use the parser from the final process route**

In `pdf_toolkit/web/app.py`, add the import:

```python
from .scan_cleanup_forms import parse_page_overrides, parse_scan_defaults
```

Replace the defaults block in `scan_cleanup_process_submit`:

```python
        defaults = {
            "strength": float(form.get("strength", active_settings.scan_default_strength)),
            "white_point": int(form.get("white_point", active_settings.scan_default_white_point)),
            "contrast": float(form.get("contrast", active_settings.scan_default_contrast)),
            "dpi_cap": int(form.get("dpi_cap", active_settings.scan_default_dpi_cap)),
            "jpeg_quality": int(form.get("jpeg_quality", active_settings.scan_default_jpeg_quality)),
        }
        page_overrides = _parse_page_overrides(form)
```

with:

```python
        default_settings = parse_scan_defaults(form, active_settings)
        defaults = {
            "strength": default_settings.strength,
            "white_point": default_settings.white_point,
            "contrast": default_settings.contrast,
            "dpi_cap": default_settings.dpi_cap,
            "jpeg_quality": default_settings.jpeg_quality,
        }
        page_overrides = parse_page_overrides(form)
```

Remove the `_parse_page_overrides` function from the bottom of `pdf_toolkit/web/app.py`.

- [ ] **Step 5: Run the form parsing tests**

Run:

```powershell
uv run pytest -q tests/test_auth_and_jobs.py::test_scan_cleanup_process_submission_stores_output_controls tests/test_auth_and_jobs.py::test_scan_cleanup_process_submission_stores_page_overrides
```

Expected: 2 passed.

- [ ] **Step 6: Commit the parser refactor**

Run:

```powershell
git add pdf_toolkit/web/app.py pdf_toolkit/web/scan_cleanup_forms.py tests/test_auth_and_jobs.py
git commit -m "refactor: share scan cleanup form parsing"
```

---

### Task 4: Preview Endpoint

**Files:**
- Create: `pdf_toolkit/web/scan_cleanup_preview.py`
- Create: `pdf_toolkit/web/templates/partials/scan_cleanup_compare.html`
- Modify: `pdf_toolkit/web/app.py`
- Test: `tests/test_auth_and_jobs.py`

- [ ] **Step 1: Write the failing web test for preview generation**

Append this test to `tests/test_auth_and_jobs.py`:

```python
def test_scan_cleanup_preview_submission_returns_before_after_panel(
    open_app_client,
    sample_scan_pdf: Path,
    tmp_path: Path,
) -> None:
    analysis = analyze_scan_pdf(sample_scan_pdf, tmp_path / "previews")
    analysis_payload = analysis.to_json()
    for page_payload in analysis_payload["pages"]:
        page_payload["preview_path"] = Path(page_payload["preview_path"]).name
    analysis_job = create_job(
        "scan-cleanup-analysis",
        "Analyze Scan Cleanup",
        [sample_scan_pdf],
    )
    update_job_fields(
        analysis_job.id,
        status=JobStatus.AWAITING_SETTINGS.value,
        artifact_json={"analysis": analysis_payload},
    )

    response = open_app_client.post(
        f"/tools/scan-cleanup/{analysis_job.id}/preview",
        data={
            "preview_page": "1",
            "strength": "0.7",
            "white_point": "244",
            "contrast": "1.1",
            "dpi_cap": "300",
            "jpeg_quality": "92",
        },
    )

    assert response.status_code == 200
    assert "Original" in response.text
    assert "Processed" in response.text
    assert "Page 1" in response.text
    assert "/previews/" in response.text
    assert "processed-page-001-" in response.text
```

Append this validation test:

```python
def test_scan_cleanup_preview_rejects_invalid_page(
    open_app_client,
    sample_scan_pdf: Path,
    tmp_path: Path,
) -> None:
    analysis = analyze_scan_pdf(sample_scan_pdf, tmp_path / "previews")
    analysis_payload = analysis.to_json()
    for page_payload in analysis_payload["pages"]:
        page_payload["preview_path"] = Path(page_payload["preview_path"]).name
    analysis_job = create_job(
        "scan-cleanup-analysis",
        "Analyze Scan Cleanup",
        [sample_scan_pdf],
    )
    update_job_fields(
        analysis_job.id,
        status=JobStatus.AWAITING_SETTINGS.value,
        artifact_json={"analysis": analysis_payload},
    )

    response = open_app_client.post(
        f"/tools/scan-cleanup/{analysis_job.id}/preview",
        data={
            "preview_page": "99",
            "strength": "0.7",
            "white_point": "244",
            "contrast": "1.1",
            "dpi_cap": "300",
            "jpeg_quality": "92",
        },
    )

    assert response.status_code == 400
    assert "Preview page must be between 1 and 1." in response.text
```

- [ ] **Step 2: Run the endpoint tests and verify they fail**

Run:

```powershell
uv run pytest -q tests/test_auth_and_jobs.py::test_scan_cleanup_preview_submission_returns_before_after_panel tests/test_auth_and_jobs.py::test_scan_cleanup_preview_rejects_invalid_page
```

Expected: FAIL with 404 because the route is not registered.

- [ ] **Step 3: Create the comparison partial**

Create `pdf_toolkit/web/templates/partials/scan_cleanup_compare.html`:

```html
<div class="compare-status">
    <strong>Page {{ page_number }}</strong>
    <span>Strength {{ "%.2f"|format(settings.strength) }}</span>
    <span>White {{ settings.white_point }}</span>
    <span>Contrast {{ "%.2f"|format(settings.contrast) }}</span>
    <span>DPI cap {{ settings.dpi_cap }}</span>
    <span>JPEG {{ settings.jpeg_quality }}</span>
</div>
<div class="compare-grid">
    <figure class="compare-frame">
        <figcaption>Original</figcaption>
        <img src="{{ original_url }}" alt="Original page {{ page_number }}">
    </figure>
    <figure class="compare-frame">
        <figcaption>Processed</figcaption>
        <img src="{{ processed_url }}" alt="Processed page {{ page_number }}">
    </figure>
</div>
```

- [ ] **Step 4: Create the preview route module**

Create `pdf_toolkit/web/scan_cleanup_preview.py`:

```python
from __future__ import annotations

import hashlib
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from ..jobs import get_job
from ..pdf_ops import ScanAnalysis, render_cleaned_page_preview
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
        page_number = int(form.get("preview_page", "1"))
        if page_number < 1 or page_number > analysis.page_count:
            return templates.TemplateResponse(
                request,
                "partials/notice.html",
                {"message": f"Preview page must be between 1 and {analysis.page_count}.", "kind": "error"},
                status_code=400,
            )

        defaults = parse_scan_defaults(form, settings)
        page_overrides = parse_page_overrides(form)
        preview_settings = settings_for_page(defaults, page_overrides, page_number)
        preview_dir = job_preview_dir(analysis_job.id, settings)
        processed_filename = _processed_preview_filename(page_number, preview_settings)
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


def _processed_preview_filename(page_number: int, preview_settings) -> str:
    cache_key = "|".join(
        [
            f"{preview_settings.strength:.4f}",
            str(preview_settings.white_point),
            f"{preview_settings.contrast:.4f}",
            str(preview_settings.dpi_cap),
            str(preview_settings.jpeg_quality),
        ]
    )
    digest = hashlib.sha1(cache_key.encode("utf-8")).hexdigest()[:12]
    return f"processed-page-{page_number:03d}-{digest}.jpg"
```

- [ ] **Step 5: Register the preview route**

In `pdf_toolkit/web/app.py`, add the import:

```python
from .scan_cleanup_preview import register_scan_cleanup_preview_routes
```

After `register_mixed_to_pdf_routes(app, templates, active_settings)`, add:

```python
    register_scan_cleanup_preview_routes(app, templates, active_settings)
```

- [ ] **Step 6: Run the endpoint tests and verify they pass**

Run:

```powershell
uv run pytest -q tests/test_auth_and_jobs.py::test_scan_cleanup_preview_submission_returns_before_after_panel tests/test_auth_and_jobs.py::test_scan_cleanup_preview_rejects_invalid_page
```

Expected: 2 passed.

- [ ] **Step 7: Commit the preview endpoint**

Run:

```powershell
git add pdf_toolkit/web/app.py pdf_toolkit/web/scan_cleanup_preview.py pdf_toolkit/web/templates/partials/scan_cleanup_compare.html tests/test_auth_and_jobs.py
git commit -m "feat: add scan cleanup preview endpoint"
```

---

### Task 5: Review UI Integration

**Files:**
- Modify: `pdf_toolkit/web/rendering.py`
- Modify: `pdf_toolkit/web/templates/partials/scan_settings_form.html`
- Modify: `pdf_toolkit/web/templates/partials/scan_cleanup_review_body.html`
- Modify: `tests/test_auth_and_jobs.py`

- [ ] **Step 1: Write the failing UI regression**

In `test_scan_cleanup_review_form_exposes_output_controls`, add these assertions after the existing JPEG quality assertions:

```python
    assert 'name="preview_page"' in response.text
    assert 'Update preview' in response.text
    assert f'hx-post="/tools/scan-cleanup/{job.id}/preview"' in response.text
    assert 'id="scan-preview-result"' in response.text
```

- [ ] **Step 2: Run the UI regression and verify it fails**

Run:

```powershell
uv run pytest -q tests/test_auth_and_jobs.py::test_scan_cleanup_review_form_exposes_output_controls
```

Expected: FAIL because the preview controls and target are not in the form yet.

- [ ] **Step 3: Serialize scan defaults for the review form**

In `pdf_toolkit/web/rendering.py`, update `serialize_job` to accept an optional `settings` argument:

```python
def serialize_job(job: Job, settings: Settings | None = None) -> dict:
```

Before `return payload`, add:

```python
    if settings is not None and payload["awaiting_scan_settings"]:
        payload["scan_defaults"] = {
            "strength": settings.scan_default_strength,
            "white_point": settings.scan_default_white_point,
            "contrast": settings.scan_default_contrast,
            "dpi_cap": settings.scan_default_dpi_cap,
            "jpeg_quality": settings.scan_default_jpeg_quality,
        }
```

Update `render_job_card` to call:

```python
    serialized_job = serialize_job(job, settings)
```

In `pdf_toolkit/web/app.py`, update full page context to call:

```python
            "job": serialize_job(job, active_settings),
```

- [ ] **Step 4: Add preview controls to the existing form**

In `pdf_toolkit/web/templates/partials/scan_settings_form.html`, replace the hard-coded values in the top inputs with serialized defaults:

```html
<input type="number" name="strength" step="0.05" min="0" max="1" value="{{ job.scan_defaults.strength }}">
<input type="number" name="white_point" step="1" min="215" max="252" value="{{ job.scan_defaults.white_point }}">
<input type="number" name="contrast" step="0.05" min="0.7" max="1.6" value="{{ job.scan_defaults.contrast }}">
<input type="number" name="dpi_cap" step="1" min="120" max="600" value="{{ job.scan_defaults.dpi_cap }}">
<input type="number" name="jpeg_quality" step="1" min="70" max="100" value="{{ job.scan_defaults.jpeg_quality }}">
```

After the closing `</div>` for `.triple-grid`, add:

```html
    <div class="preview-control-row">
        <label>
            Preview page
            <select
                name="preview_page"
                hx-post="/tools/scan-cleanup/{{ job.id }}/preview"
                hx-trigger="change"
                hx-include="closest form"
                hx-target="#scan-preview-result"
                hx-swap="innerHTML"
            >
                {% for page in job.analysis.pages %}
                    <option value="{{ page.page_number }}">Page {{ page.page_number }}</option>
                {% endfor %}
            </select>
        </label>
        <button
            class="ghost-button"
            type="button"
            hx-post="/tools/scan-cleanup/{{ job.id }}/preview"
            hx-include="closest form"
            hx-target="#scan-preview-result"
            hx-swap="innerHTML"
        >
            Update preview
        </button>
    </div>
```

- [ ] **Step 5: Add the auto-loading comparison target**

In `pdf_toolkit/web/templates/partials/scan_cleanup_review_body.html`, add this block immediately after the `job-launch-panel` block:

```html
<section class="compare-panel">
    <div
        id="scan-preview-result"
        hx-post="/tools/scan-cleanup/{{ job.id }}/preview"
        hx-trigger="load"
        hx-include="closest form"
        hx-swap="innerHTML"
    >
        <p class="muted">Generating preview for page 1...</p>
    </div>
</section>
```

- [ ] **Step 6: Run the UI regression and endpoint tests**

Run:

```powershell
uv run pytest -q tests/test_auth_and_jobs.py::test_scan_cleanup_review_form_exposes_output_controls tests/test_auth_and_jobs.py::test_scan_cleanup_partial_embeds_review_when_analysis_is_ready tests/test_auth_and_jobs.py::test_scan_cleanup_preview_submission_returns_before_after_panel
```

Expected: 3 passed.

- [ ] **Step 7: Commit the UI integration**

Run:

```powershell
git add pdf_toolkit/web/rendering.py pdf_toolkit/web/app.py pdf_toolkit/web/templates/partials/scan_settings_form.html pdf_toolkit/web/templates/partials/scan_cleanup_review_body.html tests/test_auth_and_jobs.py
git commit -m "feat: add scan cleanup compare controls"
```

---

### Task 6: Compare Layout Styling

**Files:**
- Modify: `pdf_toolkit/web/static/app.css`
- Test: Browser smoke plus focused tests

- [ ] **Step 1: Add comparison styles**

Append this CSS after the existing `.settings-help dd` block in `pdf_toolkit/web/static/app.css`:

```css
.preview-control-row {
    display: grid;
    grid-template-columns: minmax(180px, 260px) auto;
    align-items: end;
    gap: 0.75rem;
}

.compare-panel {
    display: grid;
    gap: 0.75rem;
}

.compare-status {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 0.5rem 0.75rem;
    color: var(--muted);
    font-size: 0.9rem;
}

.compare-status strong {
    color: var(--ink);
}

.compare-grid {
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 1rem;
}

.compare-frame {
    display: grid;
    gap: 0.5rem;
    margin: 0;
    min-width: 0;
}

.compare-frame figcaption {
    font-weight: 700;
}

.compare-frame img {
    width: 100%;
    height: auto;
    display: block;
    border: 1px solid rgba(31, 42, 55, 0.1);
    border-radius: 14px;
    background: rgba(255, 255, 255, 0.72);
}
```

Inside the existing `@media (max-width: 860px)` block, add:

```css
    .preview-control-row,
    .compare-grid {
        grid-template-columns: 1fr;
    }
```

- [ ] **Step 2: Run the relevant automated tests**

Run:

```powershell
uv run pytest -q tests/test_scan_cleanup.py::test_render_cleaned_page_preview_outputs_single_page_image tests/test_auth_and_jobs.py::test_scan_cleanup_review_form_exposes_output_controls tests/test_auth_and_jobs.py::test_scan_cleanup_preview_submission_returns_before_after_panel tests/test_auth_and_jobs.py::test_scan_cleanup_preview_rejects_invalid_page
```

Expected: 4 passed.

- [ ] **Step 3: Commit styling**

Run:

```powershell
git add pdf_toolkit/web/static/app.css
git commit -m "style: lay out scan cleanup compare preview"
```

---

### Task 7: Browser Verification

**Files:**
- No source edits expected
- Test: Local app in browser

- [ ] **Step 1: Start the app inline**

Run:

```powershell
$env:PDFKIT_RUN_JOBS_INLINE="true"
$env:PDFKIT_REQUIRE_LOGIN="false"
$env:PDFKIT_PORT="8130"
uv run uvicorn pdf_toolkit.web.app:create_app --factory --host 127.0.0.1 --port 8130
```

Expected: the app starts on `http://127.0.0.1:8130`.

- [ ] **Step 2: Smoke test the scan cleanup flow**

Use the browser to:

1. Open `http://127.0.0.1:8130/tools/scan-cleanup`.
2. Upload `D:\MyDocuments\08-French\Niveau7\semaine 4\les relatifs.pdf` if it exists locally.
3. Confirm the analysis result swaps directly into the review UI.
4. Confirm the compare panel auto-loads page 1.
5. Change `White Point` to `248` and click `Update preview`.
6. Confirm the `Processed` image refreshes and the status row shows `White 248`.
7. If the PDF has more than one page, pick another page from `Preview page` and confirm the original/processed pair changes.

- [ ] **Step 3: Check mobile layout**

Use a narrow viewport around `390x844`.

Expected:

- The settings controls stack cleanly.
- Original and processed images stack vertically.
- No text overlaps the selector, buttons, or comparison captions.

- [ ] **Step 4: Stop the local server**

Stop the foreground uvicorn process with `Ctrl+C`.

---

### Task 8: Final Verification

**Files:**
- No source edits expected
- Test: Full relevant suite

- [ ] **Step 1: Run focused scan cleanup tests**

Run:

```powershell
uv run pytest -q tests/test_scan_cleanup.py tests/test_auth_and_jobs.py::test_scan_cleanup_review_form_exposes_output_controls tests/test_auth_and_jobs.py::test_scan_cleanup_partial_embeds_review_when_analysis_is_ready tests/test_auth_and_jobs.py::test_scan_cleanup_process_submission_stores_output_controls tests/test_auth_and_jobs.py::test_scan_cleanup_process_submission_stores_page_overrides tests/test_auth_and_jobs.py::test_scan_cleanup_preview_submission_returns_before_after_panel tests/test_auth_and_jobs.py::test_scan_cleanup_preview_rejects_invalid_page
```

Expected: all non-fixture-dependent scan cleanup tests pass. If the two existing slow fixture tests still fail because external sample files are absent or one-page fixtures lack page 2, record that as existing test-data debt and do not claim the full suite is clean.

- [ ] **Step 2: Run the normal non-slow suite**

Run:

```powershell
uv run pytest -q -m "not slow"
```

Expected: all non-slow tests pass.

- [ ] **Step 3: Inspect git status**

Run:

```powershell
git status --short --branch
```

Expected: branch is ahead by the feature commits and no unstaged changes remain.

---

## Self-Review

Spec coverage:

- Side-by-side original and processed preview is covered by Tasks 4-6.
- Multi-page page selection is covered by Task 5.
- Avoiding a full final-process/download/open loop is covered by the single-page preview endpoint in Task 4.
- Keeping the project lean is covered by reusing the existing cleanup pipeline, existing preview folder, and HTMX partials rather than adding a new service or viewer library.

Placeholder scan:

- No task uses TBD, TODO, "implement later", or vague "add tests" language.
- Each code-changing task includes exact paths, code blocks, commands, and expected outcomes.

Type consistency:

- `CleanupSettings`, `ScanAnalysis`, and `render_cleaned_page_preview` are consistently named across core, route, and tests.
- The form field is consistently named `preview_page`.
- The comparison target is consistently named `scan-preview-result`.
