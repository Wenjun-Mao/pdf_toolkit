# Scan Cleanup Optimize Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a Scan Cleanup `Optimize` action that proposes document-specific cleanup settings through an Original / Stable Default / Optimized comparison before the user applies the settings.

**Architecture:** Keep the existing scan cleanup pipeline as the renderer/processor. Add a focused estimator module for sample selection, candidate generation, and scoring; add an HTMX optimize route that returns a proposal partial; use Alpine to apply proposed values to the existing tuning form only when the user clicks `Use optimized settings`.

**Tech Stack:** Python 3.12, FastAPI, Jinja2, HTMX, Alpine.js, PyMuPDF (`fitz`), OpenCV (`cv2`), NumPy, pytest.

---

## File Structure

- Create `pdf_toolkit/pdf_ops/scan_cleanup_estimator.py`
  - Owns document-level optimization: sample page selection, candidate generation, image metrics, candidate scoring, and returning `OptimizationResult`.
- Modify `pdf_toolkit/web/scan_cleanup_preview.py`
  - Reuse preview rendering for normal previews and optimize proposals.
  - Add `POST /tools/scan-cleanup/{analysis_job_id}/optimize`.
- Modify `pdf_toolkit/web/templates/partials/scan_settings_form.html`
  - Add the `Optimize` button beside existing preview/reset actions.
- Modify `pdf_toolkit/web/templates/partials/scan_cleanup_review_body.html`
  - Add a stable target for optimization proposal output.
- Create `pdf_toolkit/web/templates/partials/scan_cleanup_optimize_proposal.html`
  - Renders Original / Stable Default / Optimized comparison plus `Use optimized settings`.
- Modify `pdf_toolkit/web/templates/base.html`
  - Add Alpine method to copy proposed settings into the existing form and refresh preview.
- Modify `pdf_toolkit/web/static/app.css`
  - Add compact styles for the three-column optimization proposal.
- Create `tests/test_scan_cleanup_estimator.py`
  - Tests estimator selection, scoring, candidate generation, and normalized integration result.
- Modify `tests/test_auth_and_jobs.py`
  - Tests the optimize endpoint and failure behavior.
- Modify `tests/test_scan_cleanup_tuning_ui.py`
  - Tests the button, target, and client-side apply hook are wired.

The existing uncommitted Recent Jobs cleanup files are unrelated. Do not revert or restage them while implementing this plan.

---

### Task 1: Estimator Core

**Files:**
- Create: `tests/test_scan_cleanup_estimator.py`
- Create: `pdf_toolkit/pdf_ops/scan_cleanup_estimator.py`

- [ ] **Step 1: Write failing estimator tests**

Create `tests/test_scan_cleanup_estimator.py`:

```python
from __future__ import annotations

from pathlib import Path

import cv2
import fitz
import numpy as np

from pdf_toolkit.pdf_ops import CleanupSettings, analyze_scan_pdf
from pdf_toolkit.pdf_ops.scan_cleanup_estimator import (
    OptimizationResult,
    estimate_cleanup_settings,
    generate_candidate_settings,
    score_candidate,
    select_sample_page_numbers,
)


def test_select_sample_page_numbers_uses_one_page_for_single_page(sample_scan_pdf: Path, tmp_path: Path) -> None:
    analysis = analyze_scan_pdf(sample_scan_pdf, tmp_path / "previews")

    assert select_sample_page_numbers(analysis, max_samples=5) == [1]


def test_select_sample_page_numbers_uses_bounded_coverage_for_multi_page(tmp_path: Path) -> None:
    source_pdf = tmp_path / "many-pages.pdf"
    document = fitz.open()
    for _ in range(12):
        page = document.new_page(width=300, height=400)
        page.insert_text((36, 72), "sample")
    document.save(source_pdf)
    document.close()
    analysis = analyze_scan_pdf(source_pdf, tmp_path / "previews")

    selected = select_sample_page_numbers(analysis, max_samples=5)

    assert selected[0] == 1
    assert selected[-1] == 12
    assert len(selected) <= 5
    assert selected == sorted(set(selected))


def test_generate_candidate_settings_keeps_output_controls_stable() -> None:
    stable = CleanupSettings(strength=0.65, white_point=242, contrast=1.05, dpi_cap=300, jpeg_quality=92)

    candidates = generate_candidate_settings(stable)

    assert stable.normalized() in candidates
    assert {candidate.dpi_cap for candidate in candidates} == {300}
    assert {candidate.jpeg_quality for candidate in candidates} == {92}
    assert all(0.0 <= candidate.strength <= 1.0 for candidate in candidates)
    assert all(215 <= candidate.white_point <= 252 for candidate in candidates)
    assert all(0.7 <= candidate.contrast <= 1.6 for candidate in candidates)


def test_score_candidate_prefers_clean_background_when_foreground_is_preserved() -> None:
    original = np.full((96, 128), 208, dtype=np.uint8)
    cv2.rectangle(original, (18, 28), (112, 40), 35, thickness=-1)
    cv2.line(original, (16, 62), (116, 62), 155, thickness=1)

    weak = np.full_like(original, 226)
    cv2.rectangle(weak, (18, 28), (112, 40), 35, thickness=-1)
    cv2.line(weak, (16, 62), (116, 62), 155, thickness=1)

    clean = np.full_like(original, 250)
    cv2.rectangle(clean, (18, 28), (112, 40), 35, thickness=-1)
    cv2.line(clean, (16, 62), (116, 62), 155, thickness=1)

    assert score_candidate(original, clean) > score_candidate(original, weak)


def test_score_candidate_penalizes_erased_faint_content() -> None:
    original = np.full((96, 128), 210, dtype=np.uint8)
    cv2.rectangle(original, (18, 28), (112, 40), 35, thickness=-1)
    cv2.line(original, (16, 62), (116, 62), 155, thickness=1)

    preserved = np.full_like(original, 250)
    cv2.rectangle(preserved, (18, 28), (112, 40), 35, thickness=-1)
    cv2.line(preserved, (16, 62), (116, 62), 165, thickness=1)

    erased = np.full_like(original, 250)
    cv2.rectangle(erased, (18, 28), (112, 40), 35, thickness=-1)
    cv2.line(erased, (16, 62), (116, 62), 255, thickness=1)

    assert score_candidate(original, preserved) > score_candidate(original, erased)


def test_estimate_cleanup_settings_returns_normalized_result(sample_scan_pdf: Path, tmp_path: Path) -> None:
    analysis = analyze_scan_pdf(sample_scan_pdf, tmp_path / "previews")

    result = estimate_cleanup_settings(
        sample_scan_pdf,
        analysis,
        stable_settings=CleanupSettings(),
        preview_width_px=360,
        max_samples=3,
    )

    assert isinstance(result, OptimizationResult)
    assert result.sampled_pages == [1]
    assert 0.0 <= result.settings.strength <= 1.0
    assert 215 <= result.settings.white_point <= 252
    assert 0.7 <= result.settings.contrast <= 1.6
    assert result.settings.dpi_cap == 300
    assert result.settings.jpeg_quality == 92
```

- [ ] **Step 2: Run estimator tests and verify they fail**

Run:

```bash
uv run pytest -q tests/test_scan_cleanup_estimator.py
```

Expected: import failure because `pdf_toolkit.pdf_ops.scan_cleanup_estimator` does not exist.

- [ ] **Step 3: Implement estimator module**

Create `pdf_toolkit/pdf_ops/scan_cleanup_estimator.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
from itertools import product
from pathlib import Path

import cv2
import fitz
import numpy as np

from .scan_cleanup import (
    CleanupSettings,
    ScanAnalysis,
    _clean_page_image,
    _render_page_rgb,
    _resolve_preview_render_dpi,
)


@dataclass(frozen=True, slots=True)
class OptimizationResult:
    settings: CleanupSettings
    sampled_pages: list[int]
    score: float


@dataclass(frozen=True, slots=True)
class GrayscaleMetrics:
    background: float
    foreground: float
    contrast: float
    white_ratio: float
    midtone_ratio: float
    noise: float


def estimate_cleanup_settings(
    source_path: Path,
    analysis: ScanAnalysis,
    *,
    stable_settings: CleanupSettings | None = None,
    preview_width_px: int = 900,
    max_samples: int = 5,
) -> OptimizationResult:
    normalized_stable = (stable_settings or CleanupSettings()).normalized()
    sampled_pages = select_sample_page_numbers(analysis, max_samples=max_samples)
    if not sampled_pages:
        raise ValueError("No pages are available for scan cleanup optimization.")

    candidates = generate_candidate_settings(normalized_stable)
    if not candidates:
        raise ValueError("No scan cleanup candidates are available.")

    candidate_scores = [0.0 for _ in candidates]

    with fitz.open(source_path) as document:
        for page_number in sampled_pages:
            page = document[page_number - 1]
            page_info = analysis.pages[page_number - 1]
            render_dpi = _resolve_preview_render_dpi(
                page,
                page_info,
                normalized_stable,
                preview_width_px,
            )
            original_rgb = _render_page_rgb(page, render_dpi)
            original_gray = cv2.cvtColor(original_rgb, cv2.COLOR_RGB2GRAY)

            for index, candidate in enumerate(candidates):
                cleaned_gray = _clean_page_image(original_rgb, candidate)
                candidate_scores[index] += score_candidate(original_gray, cleaned_gray)

    sample_count = max(len(sampled_pages), 1)
    averaged_scores = [score / sample_count for score in candidate_scores]
    best_index = max(range(len(candidates)), key=lambda index: averaged_scores[index])
    return OptimizationResult(
        settings=candidates[best_index],
        sampled_pages=sampled_pages,
        score=averaged_scores[best_index],
    )


def select_sample_page_numbers(analysis: ScanAnalysis, *, max_samples: int = 5) -> list[int]:
    page_count = analysis.page_count
    if page_count <= 0 or max_samples <= 0:
        return []
    if page_count <= max_samples:
        return list(range(1, page_count + 1))

    anchors = [
        1,
        max(1, round(page_count * 0.25)),
        max(1, round(page_count * 0.5)),
        max(1, round(page_count * 0.75)),
        page_count,
    ]
    selected = sorted({min(max(page_number, 1), page_count) for page_number in anchors})
    return selected[:max_samples]


def generate_candidate_settings(stable_settings: CleanupSettings) -> list[CleanupSettings]:
    normalized = stable_settings.normalized()
    values: list[CleanupSettings] = []
    seen: set[tuple[float, int, float, int, int]] = set()

    for strength, white_point, contrast in product(
        (0.65, 0.75, 0.85, 0.9),
        (238, 242, 246, 248),
        (1.05, 1.15, 1.25, 1.35),
    ):
        candidate = CleanupSettings(
            strength=strength,
            white_point=white_point,
            contrast=contrast,
            dpi_cap=normalized.dpi_cap,
            jpeg_quality=normalized.jpeg_quality,
        ).normalized()
        key = _settings_key(candidate)
        if key not in seen:
            seen.add(key)
            values.append(candidate)

    stable_key = _settings_key(normalized)
    if stable_key not in seen:
        values.insert(0, normalized)
    return values


def score_candidate(original_gray: np.ndarray, cleaned_gray: np.ndarray) -> float:
    original = _measure_grayscale(original_gray)
    cleaned = _measure_grayscale(cleaned_gray)

    background_score = (cleaned.background - original.background) / 30.0
    contrast_score = (cleaned.contrast - original.contrast) / 45.0
    foreground_lift_penalty = max(0.0, cleaned.foreground - original.foreground - 10.0) / 18.0
    faint_loss_penalty = _faint_loss_ratio(original_gray, cleaned_gray) * 1.6
    clipping_penalty = max(0.0, cleaned.white_ratio - 0.94) * 5.0
    noise_penalty = max(0.0, cleaned.noise - original.noise - 4.0) / 12.0

    return (
        background_score
        + 0.85 * contrast_score
        - 1.20 * foreground_lift_penalty
        - faint_loss_penalty
        - clipping_penalty
        - 0.35 * noise_penalty
    )


def _measure_grayscale(gray: np.ndarray) -> GrayscaleMetrics:
    image = gray.astype(np.uint8)
    background = float(np.percentile(image, 92))
    foreground = float(np.percentile(image, 8))
    laplacian = cv2.Laplacian(image, cv2.CV_32F)
    light_mask = image >= np.percentile(image, 65)
    noise = float(np.std(laplacian[light_mask])) if np.any(light_mask) else float(np.std(laplacian))
    return GrayscaleMetrics(
        background=background,
        foreground=foreground,
        contrast=background - foreground,
        white_ratio=float(np.mean(image >= 252)),
        midtone_ratio=float(np.mean((image >= 80) & (image <= 220))),
        noise=noise,
    )


def _faint_loss_ratio(original_gray: np.ndarray, cleaned_gray: np.ndarray) -> float:
    original = original_gray.astype(np.uint8)
    cleaned = cleaned_gray.astype(np.uint8)
    edges = cv2.Canny(original, 40, 120)
    faint_mask = (original >= 115) & (original <= 225) & (edges > 0)
    if not np.any(faint_mask):
        return 0.0
    return float(np.mean(cleaned[faint_mask] >= 248))


def _settings_key(settings: CleanupSettings) -> tuple[float, int, float, int, int]:
    return (
        round(settings.strength, 4),
        settings.white_point,
        round(settings.contrast, 4),
        settings.dpi_cap,
        settings.jpeg_quality,
    )
```

- [ ] **Step 4: Run estimator tests and verify they pass**

Run:

```bash
uv run pytest -q tests/test_scan_cleanup_estimator.py
```

Expected: all tests in `tests/test_scan_cleanup_estimator.py` pass.

- [ ] **Step 5: Commit estimator core**

Run:

```bash
git add pdf_toolkit/pdf_ops/scan_cleanup_estimator.py tests/test_scan_cleanup_estimator.py
git commit -m "feat: add scan cleanup settings estimator"
```

---

### Task 2: Optimize Route And Proposal Partial

**Files:**
- Modify: `pdf_toolkit/web/scan_cleanup_preview.py`
- Create: `pdf_toolkit/web/templates/partials/scan_cleanup_optimize_proposal.html`
- Test: `tests/test_auth_and_jobs.py`

- [ ] **Step 1: Write failing route tests**

Append these imports to `tests/test_auth_and_jobs.py` if they are not already present:

```python
from pdf_toolkit.pdf_ops.scan_cleanup_estimator import OptimizationResult
```

Append these tests near the existing scan cleanup preview tests:

```python
def test_scan_cleanup_optimize_returns_three_way_proposal(
    monkeypatch,
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

    def fake_estimate_cleanup_settings(*args, **kwargs):
        return OptimizationResult(
            settings=CleanupSettings(strength=0.85, white_point=246, contrast=1.25, dpi_cap=300, jpeg_quality=92),
            sampled_pages=[1],
            score=1.5,
        )

    monkeypatch.setattr(
        "pdf_toolkit.web.scan_cleanup_preview.estimate_cleanup_settings",
        fake_estimate_cleanup_settings,
    )

    response = open_app_client.post(
        f"/tools/scan-cleanup/{analysis_job.id}/optimize",
        data={
            "preview_page": "1",
            "strength": "0.25",
            "white_point": "220",
            "contrast": "0.8",
            "dpi_cap": "300",
            "jpeg_quality": "92",
        },
    )

    assert response.status_code == 200
    assert "Optimized proposal ready. Compare before applying." in response.text
    assert "Original" in response.text
    assert "Stable Default" in response.text
    assert "Optimized" in response.text
    assert "Use optimized settings" in response.text
    assert 'data-optimized-strength="0.85"' in response.text
    assert 'data-optimized-white-point="246"' in response.text
    assert 'data-optimized-contrast="1.25"' in response.text
    assert 'data-optimized-dpi-cap="300"' in response.text
    assert 'data-optimized-jpeg-quality="92"' in response.text
    assert 'name="strength"' not in response.text
    assert response.text.count("processed-page-001-") >= 2


def test_scan_cleanup_optimize_failure_keeps_existing_preview_available(
    monkeypatch,
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

    def fake_estimate_cleanup_settings(*args, **kwargs):
        raise RuntimeError("cannot score this document")

    monkeypatch.setattr(
        "pdf_toolkit.web.scan_cleanup_preview.estimate_cleanup_settings",
        fake_estimate_cleanup_settings,
    )

    response = open_app_client.post(
        f"/tools/scan-cleanup/{analysis_job.id}/optimize",
        data={"preview_page": "1"},
    )

    assert response.status_code == 400
    assert "Optimization failed. Keep using the current settings or tune manually." in response.text
    assert "optimize-proposal" not in response.text
```

- [ ] **Step 2: Run route tests and verify they fail**

Run:

```bash
uv run pytest -q tests/test_auth_and_jobs.py::test_scan_cleanup_optimize_returns_three_way_proposal tests/test_auth_and_jobs.py::test_scan_cleanup_optimize_failure_keeps_existing_preview_available
```

Expected: fail with `404 Not Found` or missing optimize route.

- [ ] **Step 3: Refactor preview rendering helper and add optimize route**

Modify `pdf_toolkit/web/scan_cleanup_preview.py`.

Add imports:

```python
from loguru import logger

from ..pdf_ops.scan_cleanup_estimator import estimate_cleanup_settings
```

Replace the inline processed-preview block inside `scan_cleanup_preview_submit` with:

```python
        processed_filename = _render_or_reuse_processed_preview(
            source_path=Path(analysis_job.input_paths[0]),
            preview_dir=preview_dir,
            analysis=analysis,
            page_number=page_number,
            preview_settings=preview_settings,
            preview_width_px=settings.preview_width_px,
        )
```

Add this route inside `register_scan_cleanup_preview_routes`, after `scan_cleanup_preview_submit`:

```python
    @app.post("/tools/scan-cleanup/{analysis_job_id}/optimize", response_class=HTMLResponse)
    async def scan_cleanup_optimize_submit(request: Request, analysis_job_id: str):
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

        source_path = Path(analysis_job.input_paths[0])
        preview_dir = job_preview_dir(analysis_job.id, settings)
        stable_settings = _stable_scan_settings(settings)
        try:
            optimization = estimate_cleanup_settings(
                source_path,
                analysis,
                stable_settings=stable_settings,
                preview_width_px=settings.preview_width_px,
            )
            stable_filename = _render_or_reuse_processed_preview(
                source_path=source_path,
                preview_dir=preview_dir,
                analysis=analysis,
                page_number=page_number,
                preview_settings=stable_settings,
                preview_width_px=settings.preview_width_px,
            )
            optimized_filename = _render_or_reuse_processed_preview(
                source_path=source_path,
                preview_dir=preview_dir,
                analysis=analysis,
                page_number=page_number,
                preview_settings=optimization.settings,
                preview_width_px=settings.preview_width_px,
            )
        except Exception:
            logger.exception("Scan cleanup optimization failed")
            return _render_validation_notice(
                request,
                templates,
                "Optimization failed. Keep using the current settings or tune manually.",
            )

        page_payload = analysis_payload["pages"][page_number - 1]
        return templates.TemplateResponse(
            request,
            "partials/scan_cleanup_optimize_proposal.html",
            {
                "job_id": analysis_job.id,
                "page_number": page_number,
                "optimized_settings": optimization.settings,
                "sampled_pages": optimization.sampled_pages,
                "original_url": f"/previews/{analysis_job.id}/{page_payload['preview_path']}",
                "stable_url": f"/previews/{analysis_job.id}/{stable_filename}",
                "optimized_url": f"/previews/{analysis_job.id}/{optimized_filename}",
            },
        )
```

Add these helpers near `_processed_preview_filename`:

```python
def _render_or_reuse_processed_preview(
    *,
    source_path: Path,
    preview_dir: Path,
    analysis: ScanAnalysis,
    page_number: int,
    preview_settings: CleanupSettings,
    preview_width_px: int,
) -> str:
    processed_filename = _processed_preview_filename(
        page_number,
        preview_settings,
        preview_width_px,
    )
    processed_path = preview_dir / processed_filename
    if not processed_path.exists():
        render_cleaned_page_preview(
            source_path=source_path,
            output_path=processed_path,
            analysis=analysis,
            page_number=page_number,
            settings=preview_settings,
            preview_width_px=preview_width_px,
        )
    return processed_filename


def _stable_scan_settings(settings: Settings) -> CleanupSettings:
    return CleanupSettings(
        strength=settings.scan_default_strength,
        white_point=settings.scan_default_white_point,
        contrast=settings.scan_default_contrast,
        dpi_cap=settings.scan_default_dpi_cap,
        jpeg_quality=settings.scan_default_jpeg_quality,
    ).normalized()
```

- [ ] **Step 4: Create proposal partial**

Create `pdf_toolkit/web/templates/partials/scan_cleanup_optimize_proposal.html`:

```html
<section
    class="compare-panel optimize-proposal"
    data-optimized-strength="{{ '%.2f'|format(optimized_settings.strength) }}"
    data-optimized-white-point="{{ optimized_settings.white_point }}"
    data-optimized-contrast="{{ '%.2f'|format(optimized_settings.contrast) }}"
    data-optimized-dpi-cap="{{ optimized_settings.dpi_cap }}"
    data-optimized-jpeg-quality="{{ optimized_settings.jpeg_quality }}"
>
    <div class="section-header compact-section-header">
        <div>
            <p class="eyebrow">Optimization Proposal</p>
            <h3>Optimized proposal ready. Compare before applying.</h3>
        </div>
        <button
            class="primary-button mini-button"
            type="button"
            x-on:click="applyOptimizedScanSettings($el.closest('[data-optimized-strength]'), document.querySelector('#job-launch-panel-{{ job_id }} form'))"
        >
            Use optimized settings
        </button>
    </div>
    <div class="compare-status">
        <strong>Page {{ page_number }}</strong>
        <span>Strength {{ "%.2f"|format(optimized_settings.strength) }}</span>
        <span>White {{ optimized_settings.white_point }}</span>
        <span>Contrast {{ "%.2f"|format(optimized_settings.contrast) }}</span>
        <span>DPI cap {{ optimized_settings.dpi_cap }}</span>
        <span>JPEG {{ optimized_settings.jpeg_quality }}</span>
        <span>Sampled {{ sampled_pages|join(", ") }}</span>
    </div>
    <p class="muted" data-optimization-state>Optimized settings have not been applied yet.</p>
    <div class="compare-grid optimize-compare-grid">
        <figure class="compare-frame">
            <figcaption>Original</figcaption>
            <button
                class="zoom-image-button"
                type="button"
                x-on:click="openZoom('{{ original_url }}', 'Original page {{ page_number }}')"
                aria-label="Zoom original page {{ page_number }}"
            >
                <img src="{{ original_url }}" alt="Original page {{ page_number }}">
            </button>
        </figure>
        <figure class="compare-frame">
            <figcaption>Stable Default</figcaption>
            <button
                class="zoom-image-button"
                type="button"
                x-on:click="openZoom('{{ stable_url }}', 'Stable default page {{ page_number }}')"
                aria-label="Zoom stable default page {{ page_number }}"
            >
                <img src="{{ stable_url }}" alt="Stable default page {{ page_number }}">
            </button>
        </figure>
        <figure class="compare-frame">
            <figcaption>Optimized</figcaption>
            <button
                class="zoom-image-button"
                type="button"
                x-on:click="openZoom('{{ optimized_url }}', 'Optimized page {{ page_number }}')"
                aria-label="Zoom optimized page {{ page_number }}"
            >
                <img src="{{ optimized_url }}" alt="Optimized page {{ page_number }}">
            </button>
        </figure>
    </div>
</section>
```

- [ ] **Step 5: Run route tests and existing preview tests**

Run:

```bash
uv run pytest -q tests/test_auth_and_jobs.py::test_scan_cleanup_preview_submission_returns_before_after_panel tests/test_auth_and_jobs.py::test_scan_cleanup_optimize_returns_three_way_proposal tests/test_auth_and_jobs.py::test_scan_cleanup_optimize_failure_keeps_existing_preview_available
```

Expected: all selected tests pass.

- [ ] **Step 6: Commit route and proposal partial**

Run:

```bash
git add pdf_toolkit/web/scan_cleanup_preview.py pdf_toolkit/web/templates/partials/scan_cleanup_optimize_proposal.html tests/test_auth_and_jobs.py
git commit -m "feat: add scan cleanup optimize proposal route"
```

---

### Task 3: Tuning Form Button And Apply Behavior

**Files:**
- Modify: `pdf_toolkit/web/templates/partials/scan_settings_form.html`
- Modify: `pdf_toolkit/web/templates/partials/scan_cleanup_review_body.html`
- Modify: `pdf_toolkit/web/templates/base.html`
- Modify: `pdf_toolkit/web/static/app.css`
- Test: `tests/test_scan_cleanup_tuning_ui.py`

- [ ] **Step 1: Write failing UI wiring tests**

Append these assertions to `test_scan_cleanup_tuning_form_can_reset_global_defaults` in `tests/test_scan_cleanup_tuning_ui.py`:

```python
    assert "Optimize" in response.text
    assert f'hx-post="/tools/scan-cleanup/{job.id}/optimize"' in response.text
    assert f'hx-target="#scan-optimize-result-{job.id}"' in response.text
    assert f'id="scan-optimize-result-{job.id}"' in response.text
```

Add this test to `tests/test_scan_cleanup_tuning_ui.py`:

```python
def test_scan_cleanup_base_includes_apply_optimized_settings(open_app_client) -> None:
    response = open_app_client.get("/tools/scan-cleanup")

    assert response.status_code == 200
    assert "applyOptimizedScanSettings(proposal, form)" in response.text
    assert "optimizedStrength" in response.text
    assert "data-optimization-state" in response.text
```

- [ ] **Step 2: Run UI tests and verify they fail**

Run:

```bash
uv run pytest -q tests/test_scan_cleanup_tuning_ui.py
```

Expected: fail because the optimize button and Alpine apply method are not wired.

- [ ] **Step 3: Add Optimize button to tuning form**

In `pdf_toolkit/web/templates/partials/scan_settings_form.html`, add this button inside `<div class="preview-actions">`, before `Reset defaults`:

```html
            <button
                class="ghost-button"
                type="button"
                hx-post="/tools/scan-cleanup/{{ job.id }}/optimize"
                hx-include="closest form"
                hx-target="#scan-optimize-result-{{ job.id }}"
                hx-swap="innerHTML"
            >
                Optimize
            </button>
```

- [ ] **Step 4: Add proposal target to review body**

In `pdf_toolkit/web/templates/partials/scan_cleanup_review_body.html`, add this target between the form panel and the normal compare panel:

```html
<div id="scan-optimize-result-{{ job.id }}"></div>
```

The surrounding section belongs in `scan_cleanup_optimize_proposal.html`, so the empty target does not create extra visible layout before optimization.

- [ ] **Step 5: Add Alpine apply method**

In `pdf_toolkit/web/templates/base.html`, inside `Alpine.data("scanCleanupReview", () => ({ ... }))`, add this method after `resetAllScanTuning(form)`:

```javascript
                applyOptimizedScanSettings(proposal, form) {
                    if (!proposal || !form) {
                        return;
                    }
                    const fields = {
                        strength: "optimizedStrength",
                        white_point: "optimizedWhitePoint",
                        contrast: "optimizedContrast",
                        dpi_cap: "optimizedDpiCap",
                        jpeg_quality: "optimizedJpegQuality",
                    };
                    Object.entries(fields).forEach(([fieldName, datasetName]) => {
                        const input = form.querySelector(`[name="${fieldName}"]`);
                        const value = proposal.dataset[datasetName];
                        if (input && value !== undefined) {
                            input.value = value;
                        }
                    });
                    const state = proposal.querySelector("[data-optimization-state]");
                    if (state) {
                        state.textContent = "Optimized settings applied. Preview refreshed.";
                    }
                    this.refreshScanPreview(form);
                },
```

Do not modify `applyScanDefaults(form)`. `Reset defaults` must continue using stable `data-scan-default` values.

- [ ] **Step 6: Style optimization proposal**

Append these styles near the existing compare styles in `pdf_toolkit/web/static/app.css`:

```css
.optimize-proposal {
    margin: 1rem 0;
    padding: 1rem;
    border: 1px solid rgba(171, 59, 36, 0.18);
    border-radius: 18px;
    background: rgba(255, 255, 255, 0.58);
}

.compact-section-header {
    align-items: center;
    gap: 0.75rem;
}

.compact-section-header h3 {
    margin: 0;
    font-size: 1.05rem;
}

.optimize-compare-grid {
    grid-template-columns: repeat(3, minmax(0, 1fr));
}
```

Inside the existing `@media (max-width: 860px)` block, add `.optimize-compare-grid` to the one-column rule:

```css
    .preview-control-row,
    .compare-grid,
    .optimize-compare-grid {
        grid-template-columns: 1fr;
    }
```

- [ ] **Step 7: Run UI tests**

Run:

```bash
uv run pytest -q tests/test_scan_cleanup_tuning_ui.py
```

Expected: all tests in `tests/test_scan_cleanup_tuning_ui.py` pass.

- [ ] **Step 8: Commit UI wiring**

Run:

```bash
git add pdf_toolkit/web/templates/partials/scan_settings_form.html pdf_toolkit/web/templates/partials/scan_cleanup_review_body.html pdf_toolkit/web/templates/base.html pdf_toolkit/web/static/app.css tests/test_scan_cleanup_tuning_ui.py
git commit -m "feat: wire scan cleanup optimize proposal UI"
```

---

### Task 4: Focused Integration Verification

**Files:**
- Verify: `pdf_toolkit/pdf_ops/scan_cleanup_estimator.py`
- Verify: `pdf_toolkit/web/scan_cleanup_preview.py`
- Verify: `pdf_toolkit/web/templates/partials/scan_cleanup_optimize_proposal.html`

- [ ] **Step 1: Run estimator and scan cleanup tests**

Run:

```bash
uv run pytest -q tests/test_scan_cleanup_estimator.py tests/test_scan_cleanup.py -m "not slow"
```

Expected: estimator tests pass and non-slow scan cleanup tests pass.

- [ ] **Step 2: Run web scan cleanup tests**

Run:

```bash
uv run pytest -q tests/test_auth_and_jobs.py::test_scan_cleanup_review_form_exposes_output_controls tests/test_auth_and_jobs.py::test_scan_cleanup_partial_embeds_review_when_analysis_is_ready tests/test_auth_and_jobs.py::test_scan_cleanup_preview_submission_returns_before_after_panel tests/test_auth_and_jobs.py::test_scan_cleanup_optimize_returns_three_way_proposal tests/test_auth_and_jobs.py::test_scan_cleanup_optimize_failure_keeps_existing_preview_available tests/test_scan_cleanup_tuning_ui.py
```

Expected: all selected web tests pass.

- [ ] **Step 3: Run non-slow suite**

Run:

```bash
uv run pytest -q -m "not slow"
```

Expected: all non-slow tests pass. If unrelated failures appear from the pre-existing dirty Recent Jobs changes, record the failing tests and inspect whether they are caused by this feature before changing files outside this plan.

- [ ] **Step 4: Start local app**

Run:

```bash
uv run uvicorn pdf_toolkit.web.app:create_app --factory --host 127.0.0.1 --port 8000
```

Expected: app starts and logs a local server on `http://127.0.0.1:8000`.

- [ ] **Step 5: Browser smoke test**

Open `http://127.0.0.1:8000` in the in-app browser and run this flow:

1. Open `Scan Cleanup`.
2. Upload a scanned PDF.
3. Wait for analysis to open the tuning view.
4. Confirm stable defaults still appear in the five global fields.
5. Click `Optimize`.
6. Confirm the proposal shows `Original`, `Stable Default`, and `Optimized`.
7. Click each image and confirm the zoom overlay works.
8. Click `Use optimized settings`.
9. Confirm the global fields change to the optimized proposal values.
10. Confirm the normal two-panel preview refreshes.
11. Click `Reset defaults`.
12. Confirm the global fields return to `0.65 / 242 / 1.05 / 300 / 92`.

- [ ] **Step 6: Commit final verification adjustments only if needed**

If verification required small fixes, commit only files touched by this plan:

```bash
git add pdf_toolkit/pdf_ops/scan_cleanup_estimator.py pdf_toolkit/web/scan_cleanup_preview.py pdf_toolkit/web/templates/partials/scan_settings_form.html pdf_toolkit/web/templates/partials/scan_cleanup_review_body.html pdf_toolkit/web/templates/partials/scan_cleanup_optimize_proposal.html pdf_toolkit/web/templates/base.html pdf_toolkit/web/static/app.css tests/test_scan_cleanup_estimator.py tests/test_auth_and_jobs.py tests/test_scan_cleanup_tuning_ui.py
git commit -m "fix: polish scan cleanup optimize flow"
```

Expected: commit succeeds if fixes were made. Skip this step if Task 1-3 commits already pass verification unchanged.

---

## Final Acceptance Criteria

- `Optimize` does not mutate the tuning fields immediately.
- Optimize proposal shows `Original | Stable Default | Optimized` for the selected preview page.
- `Use optimized settings` copies proposal values into global fields only.
- Per-page override fields are not changed by optimization.
- `Reset defaults` restores stable app defaults and does not use optimized values.
- `Reset all tuning` restores stable app defaults and clears per-page overrides.
- The estimator renders only selected sample pages, not the whole document.
- No OCR, ML, network calls, or new external services are added.
- Non-slow tests pass.

## Self-Review Notes

- Spec coverage: estimator, explicit proposal UI, stable reset behavior, failure behavior, tests, and no OCR are covered by Tasks 1-4.
- Type consistency: `OptimizationResult`, `CleanupSettings`, route names, template data attributes, and Alpine dataset keys match across tasks.
- Scope control: v1 stays document-level and does not expose internal candidate lists.
