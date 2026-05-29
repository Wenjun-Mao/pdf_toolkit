from __future__ import annotations

from pathlib import Path

import cv2
import fitz
import numpy as np
from PIL import Image

from pdf_toolkit.pdf_ops import CleanupSettings, analyze_scan_pdf
from pdf_toolkit.pdf_ops.scan_cleanup import PageAnalysis, ScanAnalysis
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

    assert len(candidates) <= 12
    assert stable.normalized() in candidates
    assert {candidate.dpi_cap for candidate in candidates} == {300}
    assert {candidate.jpeg_quality for candidate in candidates} == {92}
    assert all(0.0 <= candidate.strength <= 1.0 for candidate in candidates)
    assert all(215 <= candidate.white_point <= 252 for candidate in candidates)
    assert all(0.7 <= candidate.contrast <= 1.6 for candidate in candidates)


def test_select_sample_page_numbers_includes_problem_preview_page(tmp_path: Path) -> None:
    preview_dir = tmp_path / "previews"
    preview_dir.mkdir()
    pages: list[PageAnalysis] = []
    for page_number in range(1, 9):
        preview_path = preview_dir / f"page-{page_number:03d}.jpg"
        if page_number == 3:
            _write_preview_image(preview_path, rgb=(176, 145, 114), noise=18)
        else:
            _write_preview_image(preview_path, rgb=(238, 238, 238), noise=1)
        pages.append(_page_analysis(page_number, preview_path.name))
    analysis = ScanAnalysis(source_path="sample.pdf", page_count=8, pages=pages)

    selected = select_sample_page_numbers(analysis, max_samples=5, preview_dir=preview_dir)

    assert 1 in selected
    assert 3 in selected
    assert 8 in selected
    assert len(selected) <= 5
    assert selected == sorted(set(selected))


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


def test_score_candidate_penalizes_lifted_dark_foreground() -> None:
    original = np.full((96, 128), 210, dtype=np.uint8)
    cv2.rectangle(original, (16, 26), (112, 46), 35, thickness=-1)

    preserved = np.full_like(original, 250)
    cv2.rectangle(preserved, (16, 26), (112, 46), 35, thickness=-1)

    lifted = np.full_like(original, 250)
    cv2.rectangle(lifted, (16, 26), (112, 46), 100, thickness=-1)

    assert score_candidate(original, preserved) > score_candidate(original, lifted)


def test_score_candidate_penalizes_over_clipped_output() -> None:
    original = np.full((96, 128), 208, dtype=np.uint8)
    bright = np.full_like(original, 251)
    clipped = np.full_like(original, 255)

    assert score_candidate(original, bright) > score_candidate(original, clipped)


def test_score_candidate_penalizes_harsh_background_noise() -> None:
    rng = np.random.default_rng(7)
    original = np.full((96, 128), 210, dtype=np.uint8)
    cv2.rectangle(original, (16, 26), (112, 46), 35, thickness=-1)

    smooth = np.full_like(original, 246)
    cv2.rectangle(smooth, (16, 26), (112, 46), 35, thickness=-1)

    noisy = np.clip(
        np.full_like(original, 246, dtype=np.int16) + rng.integers(-10, 11, size=original.shape),
        0,
        255,
    ).astype(np.uint8)
    cv2.rectangle(noisy, (16, 26), (112, 46), 35, thickness=-1)

    assert score_candidate(original, smooth) > score_candidate(original, noisy)


def test_score_candidate_rewards_stronger_cleanup_under_tint_pressure() -> None:
    tinted_rgb = np.full((96, 128, 3), (218, 192, 158), dtype=np.uint8)
    cv2.rectangle(tinted_rgb, (16, 26), (112, 46), (42, 42, 42), thickness=-1)
    same_cleaned_gray = cv2.cvtColor(tinted_rgb, cv2.COLOR_RGB2GRAY)

    strong_settings = CleanupSettings(strength=0.9, white_point=246, contrast=1.25)
    weak_settings = CleanupSettings(strength=0.65, white_point=242, contrast=1.05)

    assert score_candidate(tinted_rgb, same_cleaned_gray, strong_settings) > score_candidate(
        tinted_rgb,
        same_cleaned_gray,
        weak_settings,
    )


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


def _page_analysis(page_number: int, preview_path: str) -> PageAnalysis:
    return PageAnalysis(
        page_number=page_number,
        has_text=False,
        full_page_xrefs=[],
        dominant_xref=None,
        dominant_has_mask=False,
        effective_dpi=None,
        has_mask=False,
        layered_images=False,
        preview_path=preview_path,
    )


def _write_preview_image(path: Path, *, rgb: tuple[int, int, int], noise: int) -> None:
    image = np.full((72, 56, 3), rgb, dtype=np.int16)
    if noise:
        rng = np.random.default_rng(100 + int(path.stem[-3:]))
        image += rng.integers(-noise, noise + 1, size=image.shape)
    Image.fromarray(np.clip(image, 0, 255).astype(np.uint8)).save(path)


def test_estimate_cleanup_settings_accepts_positional_optional_arguments(
    sample_scan_pdf: Path,
    tmp_path: Path,
) -> None:
    analysis = analyze_scan_pdf(sample_scan_pdf, tmp_path / "previews")

    result = estimate_cleanup_settings(sample_scan_pdf, analysis, CleanupSettings(), 360, 3)

    assert isinstance(result, OptimizationResult)
    assert result.sampled_pages == [1]
