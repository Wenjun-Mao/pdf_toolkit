from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import cv2
import fitz
import numpy as np
from PIL import Image
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from .scan_cleanup import (
    CleanupSettings,
    ScanAnalysis,
    _clean_page_image,
    _render_page_rgb,
    _resolve_preview_render_dpi,
)


_CLEANUP_PROFILE_CANDIDATES = (
    (0.65, 242, 1.05),
    (0.70, 242, 1.10),
    (0.75, 244, 1.15),
    (0.80, 246, 1.20),
    (0.85, 246, 1.25),
    (0.90, 248, 1.30),
    (0.82, 248, 1.10),
    (0.90, 246, 1.15),
    (0.72, 246, 1.25),
)
_MAX_PROBLEM_SAMPLE_PAGES = 2
_PREVIEW_PROBLEM_SCORE_THRESHOLD = 0.45


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
    retry=retry_if_exception_type((RuntimeError, OSError)),
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
    preview_dir: Path | None = None,
) -> OptimizationResult:
    normalized_stable = (stable_settings or CleanupSettings()).normalized()
    sampled_pages = select_sample_page_numbers(
        analysis,
        max_samples=max_samples,
        preview_dir=preview_dir,
    )
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

            for index, candidate in enumerate(candidates):
                cleaned_gray = _clean_page_image(original_rgb, candidate)
                scores[index] += score_candidate(original_rgb, cleaned_gray, candidate)

    averaged_scores = [score / len(sampled_pages) for score in scores]
    best_index = max(range(len(candidates)), key=lambda index: averaged_scores[index])
    return OptimizationResult(
        settings=candidates[best_index],
        sampled_pages=sampled_pages,
        score=averaged_scores[best_index],
    )


def select_sample_page_numbers(
    analysis: ScanAnalysis,
    *,
    max_samples: int = 5,
    preview_dir: Path | None = None,
) -> list[int]:
    page_count = analysis.page_count
    if page_count <= 0 or max_samples <= 0:
        return []
    if page_count <= max_samples:
        return list(range(1, page_count + 1))
    if max_samples == 1:
        return [1]
    if max_samples == 2:
        return [1, page_count]

    selected: list[int] = []
    for page_number in (1, _middle_page_number(page_count), page_count):
        _append_page_number(selected, page_number, page_count, max_samples)

    problem_slots = min(_MAX_PROBLEM_SAMPLE_PAGES, max_samples - len(selected))
    if problem_slots > 0:
        for page_number, score in _rank_preview_problem_pages(analysis, preview_dir):
            if score < _PREVIEW_PROBLEM_SCORE_THRESHOLD:
                break
            if _append_page_number(selected, page_number, page_count, max_samples):
                problem_slots -= 1
            if problem_slots <= 0:
                break

    for page_number in _evenly_spaced_page_numbers(page_count, max_samples):
        if len(selected) >= max_samples:
            break
        _append_page_number(selected, page_number, page_count, max_samples)

    for page_number in range(1, page_count + 1):
        if len(selected) >= max_samples:
            break
        _append_page_number(selected, page_number, page_count, max_samples)

    return sorted(selected)


def generate_candidate_settings(stable_settings: CleanupSettings) -> list[CleanupSettings]:
    normalized = stable_settings.normalized()
    candidates: list[CleanupSettings] = []
    seen: set[tuple[float, int, float, int, int]] = set()

    _append_unique_candidate(candidates, seen, normalized)
    for strength, white_point, contrast in _CLEANUP_PROFILE_CANDIDATES:
        candidate = CleanupSettings(
            strength=strength,
            white_point=white_point,
            contrast=contrast,
            dpi_cap=normalized.dpi_cap,
            jpeg_quality=normalized.jpeg_quality,
        ).normalized()
        _append_unique_candidate(candidates, seen, candidate)

    return candidates


def score_candidate(
    original_gray: np.ndarray,
    cleaned_gray: np.ndarray,
    settings: CleanupSettings | None = None,
) -> float:
    original_array = np.asarray(original_gray)
    original = _as_grayscale(original_array)
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

    score = (
        background_score
        + 0.8 * separation_score
        - 1.25 * foreground_lift_penalty
        - faint_loss_penalty
        - clipping_penalty
        - 0.7 * noise_penalty
    )
    if settings is not None and _is_rgb_image(original_array):
        score += _tint_cleanup_reward(original_array, settings)
    return score


def _middle_page_number(page_count: int) -> int:
    return max(1, min(page_count, (page_count + 1) // 2))


def _append_page_number(
    selected: list[int],
    page_number: int,
    page_count: int,
    max_samples: int,
) -> bool:
    bounded_page_number = min(max(int(page_number), 1), page_count)
    if len(selected) >= max_samples or bounded_page_number in selected:
        return False
    selected.append(bounded_page_number)
    return True


def _evenly_spaced_page_numbers(page_count: int, max_samples: int) -> list[int]:
    page_numbers = np.rint(np.linspace(1, page_count, num=max_samples)).astype(int)
    return [min(max(int(page_number), 1), page_count) for page_number in page_numbers]


def _rank_preview_problem_pages(
    analysis: ScanAnalysis,
    preview_dir: Path | None,
) -> list[tuple[int, float]]:
    scored_pages: list[tuple[int, float]] = []
    for page_info in analysis.pages:
        preview_path = _resolve_preview_path(page_info.preview_path, preview_dir)
        if preview_path is None:
            continue
        try:
            preview_rgb = _load_preview_rgb(preview_path)
        except (OSError, ValueError):
            continue
        problem_score = _preview_problem_score(preview_rgb)
        if problem_score > 0.0:
            scored_pages.append((page_info.page_number, problem_score))

    return sorted(scored_pages, key=lambda item: (-item[1], item[0]))


def _resolve_preview_path(preview_path: str, preview_dir: Path | None) -> Path | None:
    raw_path = Path(preview_path)
    candidates: list[Path] = []
    if raw_path.is_absolute():
        candidates.append(raw_path)
    else:
        if preview_dir is not None:
            candidates.append(preview_dir / raw_path)
            candidates.append(preview_dir / raw_path.name)
        candidates.append(raw_path)

    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def _load_preview_rgb(preview_path: Path) -> np.ndarray:
    with Image.open(preview_path) as image:
        rgb = np.array(image.convert("RGB"))

    height, width = rgb.shape[:2]
    max_dimension = max(height, width)
    if max_dimension <= 360:
        return rgb

    scale = 360 / max_dimension
    resized_size = (max(1, round(width * scale)), max(1, round(height * scale)))
    return cv2.resize(rgb, resized_size, interpolation=cv2.INTER_AREA)


def _preview_problem_score(rgb_image: np.ndarray) -> float:
    tint_pressure = _background_tint_pressure(rgb_image)
    dim_pressure = _background_dim_pressure(rgb_image)
    noise_pressure = _background_noise_pressure(rgb_image)
    return 0.9 * tint_pressure + 0.65 * dim_pressure + 0.45 * noise_pressure


def _tint_cleanup_reward(original_rgb: np.ndarray, settings: CleanupSettings) -> float:
    tint_pressure = _background_tint_pressure(original_rgb)
    if tint_pressure <= 0.0:
        return 0.0

    normalized = settings.normalized()
    cleanup_bias = (
        0.7 * ((normalized.strength - 0.65) / 0.25)
        + 0.15 * ((normalized.white_point - 242) / 8.0)
        + 0.15 * ((normalized.contrast - 1.05) / 0.25)
    )
    return tint_pressure * float(np.clip(cleanup_bias, -1.0, 1.0)) * 0.45


def _background_tint_pressure(rgb_image: np.ndarray) -> float:
    if not _is_rgb_image(rgb_image):
        return 0.0

    rgb = np.asarray(rgb_image, dtype=np.uint8)
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    background_mask = _background_mask(gray)
    background = rgb[background_mask].astype(np.float32)
    if background.size == 0:
        return 0.0

    mean_channels = np.mean(background, axis=0)
    channel_spread = float(np.max(mean_channels) - np.min(mean_channels))
    per_pixel_spread = float(np.mean(np.max(background, axis=1) - np.min(background, axis=1)))
    tint = max(channel_spread, per_pixel_spread * 0.75)
    return _clamp01((tint - 8.0) / 42.0)


def _background_dim_pressure(rgb_image: np.ndarray) -> float:
    if not _is_rgb_image(rgb_image):
        return 0.0

    gray = cv2.cvtColor(np.asarray(rgb_image, dtype=np.uint8), cv2.COLOR_RGB2GRAY)
    background_mask = _background_mask(gray)
    background = gray[background_mask]
    if background.size == 0:
        return 0.0

    brightness = float(np.percentile(background, 80))
    return _clamp01((232.0 - brightness) / 70.0)


def _background_noise_pressure(rgb_image: np.ndarray) -> float:
    if not _is_rgb_image(rgb_image):
        return 0.0

    gray = cv2.cvtColor(np.asarray(rgb_image, dtype=np.uint8), cv2.COLOR_RGB2GRAY)
    background_mask = _background_mask(gray)
    laplacian = cv2.Laplacian(gray, cv2.CV_32F)
    if not np.any(background_mask):
        return 0.0

    noise = float(np.std(laplacian[background_mask]))
    return _clamp01((noise - 5.0) / 45.0)


def _background_mask(gray: np.ndarray) -> np.ndarray:
    image = gray.astype(np.uint8)
    light_mask = image >= np.percentile(image, 70)
    edge_mask = cv2.dilate(cv2.Canny(image, 50, 150), np.ones((3, 3), dtype=np.uint8)) > 0
    background_mask = light_mask & ~edge_mask
    if np.count_nonzero(background_mask) < max(16, int(image.size * 0.02)):
        return light_mask
    return background_mask


def _is_rgb_image(image: np.ndarray) -> bool:
    array = np.asarray(image)
    return array.ndim == 3 and array.shape[2] == 3


def _clamp01(value: float) -> float:
    return min(max(float(value), 0.0), 1.0)


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
