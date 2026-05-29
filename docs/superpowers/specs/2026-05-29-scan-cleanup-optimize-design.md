# Scan Cleanup Optimize Design

Date: 2026-05-29

## Context

Scan Cleanup currently uses stable fixed defaults for the tuning controls:

- Strength: `0.65`
- White Point: `242`
- Contrast: `1.05`
- Max DPI: `300`
- JPEG Quality: `92`

These defaults are predictable and safe, but users still need trial and error to find stronger settings for each scanned document. The existing preview workflow makes that iteration possible, but the app can provide a better cleanup-forward starting point without changing the underlying cleanup pipeline.

## Goals

- Keep the current fixed settings as the stable app defaults.
- Add an explicit `Optimize` action that suggests document-specific global settings.
- Favor cleanup-forward output: whiter paper, reduced tint, crisper dark text, and better print readability.
- Protect against obvious damage: erased faint marks, lifted dark text, harsh noise, or excessive clipping.
- Use only local deterministic image analysis with existing dependencies.
- Keep v1 document-level only; do not auto-populate per-page overrides.

## Non-Goals

- Do not replace the existing cleanup pipeline.
- Do not run OCR, machine learning, or external services.
- Do not optimize every page individually in v1.
- Do not auto-apply optimized settings during `Analyze Scan`.
- Do not change the meaning of `Reset defaults`.

## User Experience

The Scan Cleanup tuning form will expose three distinct actions:

### Optimize

`Optimize` runs a document-specific estimator using the existing scan analysis payload and sampled page renders. It fills the five global fields with suggested cleanup-forward values and refreshes the selected-page comparison preview.

After optimization succeeds, the UI shows a small inline note:

```text
Optimized for this document. Preview before running cleanup.
```

### Reset Defaults

`Reset defaults` restores the stable app defaults:

```text
0.65 / 242 / 1.05 / 300 / 92
```

It does not clear per-page overrides.

### Reset All Tuning

`Reset all tuning` restores the stable app defaults and clears all per-page override fields.

## Algorithm

The estimator should be cleanup-forward with guardrails. It should choose one document-level `CleanupSettings` by sampling representative pages, generating candidate settings, processing those candidates with the existing cleanup function, and scoring the results.

### Sample Selection

For a one-page PDF, sample that page.

For multi-page PDFs, sample up to five pages:

- first page
- middle page
- last page
- one or two pages with stronger tint, lower background brightness, or noisier background when detectable from analysis-resolution renders

Sampling should render only selected pages, not the whole document.

### Candidate Generation

Candidates should stay within the existing normalized ranges:

- Strength: `0.0` to `1.0`
- White Point: `215` to `252`
- Contrast: `0.7` to `1.6`
- Max DPI: `120` to `600`
- JPEG Quality: `70` to `100`

For v1, optimization should mainly search a small cleanup-forward grid around likely useful values:

- Strength: `0.65`, `0.75`, `0.85`, `0.9`
- White Point: `238`, `242`, `246`, `248`
- Contrast: `1.05`, `1.15`, `1.25`, `1.35`

`Max DPI` should remain stable at `300` unless existing page analysis clearly shows lower effective source DPI, in which case it may cap to the lower source-like value. `JPEG Quality` should remain stable at `92` for v1.

The grid should be small enough to keep the action responsive on typical documents. If needed, the implementation can first compute a heuristic baseline and evaluate only nearby candidates.

### Scoring

For each sampled page and candidate:

1. Render the original page at preview or analysis resolution.
2. Process the render through the existing cleanup image function.
3. Compute metrics from the original and processed grayscale images.
4. Score the candidate.

The score should reward:

- background brightness moving closer to white
- reduced background tint
- smoother background
- stronger foreground/background separation
- dark foreground strokes staying dark

The score should penalize:

- loss of faint/midtone content beyond a safe threshold
- dark foreground becoming lighter
- too much of the page clipping to pure white
- increased local noise or harsh artifacts
- unusually aggressive settings winning by only destroying detail

Scores are averaged or robustly aggregated across sampled pages. The highest-scoring candidate becomes the suggested document-level settings.

## Architecture

Add a focused estimator module:

```text
pdf_toolkit/pdf_ops/scan_cleanup_estimator.py
```

Responsibilities:

- select sample pages
- render sample pages
- calculate original and processed metrics
- generate candidate settings
- score candidates
- return one normalized `CleanupSettings`

The existing `scan_cleanup.py` module remains responsible for analysis, rendering, cleanup, and final PDF output. The estimator may call existing public cleanup/render helpers or narrowly exposed internal helpers if the implementation keeps module boundaries clear.

Add a web route:

```text
POST /tools/scan-cleanup/{analysis_job_id}/optimize
```

Responsibilities:

- load the analysis job
- validate that analysis data exists
- parse the selected preview page from the current form
- run the estimator
- return updated tuning UI values
- refresh or trigger refresh of the selected-page comparison preview

The route should not create a final cleanup job and should not write per-page overrides.

## Failure Behavior

If optimization fails:

- keep the user's current form values
- show a small inline error notice
- leave manual preview and final cleanup available

If no useful page samples can be rendered, treat it as an optimization failure and keep current values.

If optimized settings normalize to the same stable defaults, the UI may still show the success note because the estimator made a valid choice.

## Testing

Add focused tests for:

- estimator returns normalized settings within allowed ranges
- candidate scoring prefers a cleaner background when dark foreground is preserved
- candidate scoring penalizes lost faint content
- sample selection uses one page for one-page PDFs and a bounded subset for multi-page PDFs
- optimize endpoint updates the global fields with suggested values
- optimize endpoint leaves current values intact on estimator failure
- `Reset defaults` still restores stable app defaults, not optimized values
- `Reset all tuning` still restores stable app defaults and clears per-page overrides

Run the scan cleanup and web tests after implementation, then run the non-slow test suite before merging.

## Open Decisions

The design intentionally fixes these choices for v1:

- Optimization is explicit, not automatic.
- Stable defaults remain stable.
- Optimization is document-level only.
- OCR is excluded.
- JPEG quality remains fixed at `92`.

The main implementation judgment left is the exact scoring weights. They should start conservative enough to prevent erased faint content, then be tuned with real scanned examples.
