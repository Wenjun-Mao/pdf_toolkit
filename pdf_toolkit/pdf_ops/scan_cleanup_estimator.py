from __future__ import annotations

from dataclasses import dataclass
from itertools import product
from pathlib import Path

import cv2
import fitz
import numpy as np
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from .scan_cleanup import (
    CleanupSettings,
    ScanAnalysis,
    _clean_page_image,
    _render_page_rgb,
    _resolve_preview_render_dpi,
)


_STRENGTH_CANDIDATES = (0.65, 0.75, 0.85, 0.9)
_WHITE_POINT_CANDIDATES = (238, 242, 246, 248)
_CONTRAST_CANDIDATES = (1.05, 1.15, 1.25, 1.35)


@dataclass(frozen=True, slots=True)
class OptimizationResult:
    settings: CleanupSettings
    sampled_pages: list[int]
    score: float


@dataclass(frozen=True, slots=True)
class _GrayscaleMetrics:
    background: float
    foreground: float
    separation: float
    white_ratio: float
    noise: float


@retry(
    retry=retry_if_exception_type((RuntimeError, OSError, ValueError)),
    wait=wait_exponential(multiplier=0.25, min=0.25, max=2),
    stop=stop_after_attempt(3),
    reraise=True,
)
def estimate_cleanup_settings(
    source_path: Path,
    analysis: ScanAnalysis,
    stable_settings: CleanupSettings | None = None,
    preview_width_px: int = 900,
    max_samples: int = 5,
) -> OptimizationResult:
    normalized_stable = (stable_settings or CleanupSettings()).normalized()
    sampled_pages = select_sample_page_numbers(analysis, max_samples=max_samples)
    if not sampled_pages:
        raise ValueError("No pages are available for scan cleanup optimization.")

    candidates = generate_candidate_settings(normalized_stable)
    scores = [0.0 for _ in candidates]

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
                scores[index] += score_candidate(original_gray, cleaned_gray)

    averaged_scores = [score / len(sampled_pages) for score in scores]
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
    if max_samples == 1:
        return [1]

    page_numbers = np.rint(np.linspace(1, page_count, num=max_samples)).astype(int)
    return sorted({min(max(int(page_number), 1), page_count) for page_number in page_numbers})


def generate_candidate_settings(stable_settings: CleanupSettings) -> list[CleanupSettings]:
    normalized = stable_settings.normalized()
    candidates: list[CleanupSettings] = []
    seen: set[tuple[float, int, float, int, int]] = set()

    for strength, white_point, contrast in product(
        _STRENGTH_CANDIDATES,
        _WHITE_POINT_CANDIDATES,
        _CONTRAST_CANDIDATES,
    ):
        candidate = CleanupSettings(
            strength=strength,
            white_point=white_point,
            contrast=contrast,
            dpi_cap=normalized.dpi_cap,
            jpeg_quality=normalized.jpeg_quality,
        ).normalized()
        _append_unique_candidate(candidates, seen, candidate)

    if _settings_key(normalized) not in seen:
        candidates.insert(0, normalized)
    return candidates


def score_candidate(original_gray: np.ndarray, cleaned_gray: np.ndarray) -> float:
    original = _as_grayscale(original_gray)
    cleaned = _as_grayscale(cleaned_gray)
    if original.shape != cleaned.shape:
        raise ValueError("Original and cleaned images must have matching dimensions.")

    original_metrics = _measure_grayscale(original)
    cleaned_metrics = _measure_grayscale(cleaned)

    background_score = (cleaned_metrics.background - original_metrics.background) / 35.0
    separation_score = (cleaned_metrics.separation - original_metrics.separation) / 50.0
    foreground_lift_penalty = _foreground_lift(original, cleaned) / 18.0
    faint_loss_penalty = _faint_loss_ratio(original, cleaned) * 1.8
    clipping_penalty = max(0.0, cleaned_metrics.white_ratio - 0.96) * 4.0
    noise_penalty = max(0.0, cleaned_metrics.noise - original_metrics.noise - 4.0) / 18.0

    return (
        background_score
        + 0.8 * separation_score
        - 1.25 * foreground_lift_penalty
        - faint_loss_penalty
        - clipping_penalty
        - 0.45 * noise_penalty
    )


def _append_unique_candidate(
    candidates: list[CleanupSettings],
    seen: set[tuple[float, int, float, int, int]],
    candidate: CleanupSettings,
) -> None:
    key = _settings_key(candidate)
    if key in seen:
        return
    seen.add(key)
    candidates.append(candidate)


def _settings_key(settings: CleanupSettings) -> tuple[float, int, float, int, int]:
    return (
        round(settings.strength, 4),
        settings.white_point,
        round(settings.contrast, 4),
        settings.dpi_cap,
        settings.jpeg_quality,
    )


def _as_grayscale(image: np.ndarray) -> np.ndarray:
    array = np.asarray(image)
    if array.ndim == 2:
        return array.astype(np.uint8)
    if array.ndim == 3 and array.shape[2] == 3:
        return cv2.cvtColor(array.astype(np.uint8), cv2.COLOR_RGB2GRAY)
    raise ValueError("Expected a grayscale image or an RGB image.")


def _measure_grayscale(gray: np.ndarray) -> _GrayscaleMetrics:
    image = gray.astype(np.uint8)
    background = float(np.percentile(image, 92))
    foreground = float(np.percentile(image, 8))
    light_mask = image >= np.percentile(image, 70)
    edge_mask = cv2.dilate(cv2.Canny(image, 50, 150), np.ones((3, 3), dtype=np.uint8)) > 0
    background_mask = light_mask & ~edge_mask
    laplacian = cv2.Laplacian(image, cv2.CV_32F)
    noise = float(np.std(laplacian[background_mask])) if np.any(background_mask) else 0.0

    return _GrayscaleMetrics(
        background=background,
        foreground=foreground,
        separation=background - foreground,
        white_ratio=float(np.mean(image >= 252)),
        noise=noise,
    )


def _foreground_lift(original_gray: np.ndarray, cleaned_gray: np.ndarray) -> float:
    foreground_cutoff = min(
        float(np.percentile(original_gray, 12)),
        float(np.percentile(original_gray, 50)) - 18.0,
    )
    foreground_mask = original_gray <= foreground_cutoff
    if not np.any(foreground_mask):
        return 0.0
    lifted = cleaned_gray.astype(np.float32) - original_gray.astype(np.float32)
    return max(0.0, float(np.mean(lifted[foreground_mask])) - 8.0)


def _faint_loss_ratio(original_gray: np.ndarray, cleaned_gray: np.ndarray) -> float:
    original_background = float(np.percentile(original_gray, 85))
    faint_mask = (
        (original_gray >= 90)
        & (original_gray <= original_background - 12.0)
        & (original_gray <= 225)
    )
    if not np.any(faint_mask):
        return 0.0
    return float(np.mean(cleaned_gray[faint_mask] >= 248))
