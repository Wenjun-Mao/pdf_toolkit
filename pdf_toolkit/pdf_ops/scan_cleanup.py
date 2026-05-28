from __future__ import annotations

import io
import subprocess
from dataclasses import asdict, dataclass
from difflib import SequenceMatcher
from pathlib import Path
from tempfile import NamedTemporaryFile

import cv2
import fitz
import numpy as np
from PIL import Image, ImageOps
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

GOLDEN_SCAN_PAGES = [1, 2, 7, 15, 28, 41, 56, 72, 96, 121, 144, 172]


@dataclass(slots=True)
class CleanupSettings:
    strength: float = 0.65
    white_point: int = 242
    contrast: float = 1.05
    dpi_cap: int = 300
    jpeg_quality: int = 92

    def normalized(self) -> "CleanupSettings":
        return CleanupSettings(
            strength=min(max(self.strength, 0.0), 1.0),
            white_point=min(max(self.white_point, 215), 252),
            contrast=min(max(self.contrast, 0.7), 1.6),
            dpi_cap=min(max(self.dpi_cap, 120), 600),
            jpeg_quality=min(max(self.jpeg_quality, 70), 100),
        )


@dataclass(slots=True)
class PageAnalysis:
    page_number: int
    has_text: bool
    full_page_xrefs: list[int]
    dominant_xref: int | None
    dominant_has_mask: bool
    effective_dpi: float | None
    has_mask: bool
    layered_images: bool
    preview_path: str


@dataclass(slots=True)
class ScanAnalysis:
    source_path: str
    page_count: int
    pages: list[PageAnalysis]

    def to_json(self) -> dict:
        return {
            "source_path": self.source_path,
            "page_count": self.page_count,
            "pages": [asdict(page) for page in self.pages],
        }

    @classmethod
    def from_json(cls, payload: dict) -> "ScanAnalysis":
        return cls(
            source_path=payload["source_path"],
            page_count=payload["page_count"],
            pages=[PageAnalysis(**page_payload) for page_payload in payload["pages"]],
        )


def analyze_scan_pdf(source_path: Path, preview_dir: Path, preview_width_px: int = 900) -> ScanAnalysis:
    preview_dir.mkdir(parents=True, exist_ok=True)
    page_payloads: list[PageAnalysis] = []

    with fitz.open(source_path) as document:
        for page_number, page in enumerate(document, start=1):
            preview_path = preview_dir / f"page-{page_number:03d}.jpg"
            preview_pixmap = page.get_pixmap(
                matrix=_fit_preview_matrix(page.rect.width, preview_width_px),
                alpha=False,
            )
            preview_pixmap.save(preview_path)

            candidates = _find_full_page_candidates(document, page)
            replace_candidates = [candidate for candidate in candidates if candidate["smask"] == 0] or candidates
            dominant = replace_candidates[0] if replace_candidates else None
            effective_dpi = _effective_dpi(dominant["width"], dominant["rect"].width) if dominant else None
            page_payloads.append(
                PageAnalysis(
                    page_number=page_number,
                    has_text=bool(page.get_text("text").strip()),
                    full_page_xrefs=[dominant["xref"]] if dominant else [],
                    dominant_xref=dominant["xref"] if dominant else None,
                    dominant_has_mask=bool(dominant["smask"]) if dominant else False,
                    effective_dpi=effective_dpi,
                    has_mask=any(candidate["smask"] for candidate in candidates),
                    layered_images=len(candidates) > 1,
                    preview_path=str(preview_path),
                )
            )

    return ScanAnalysis(
        source_path=str(source_path),
        page_count=len(page_payloads),
        pages=page_payloads,
    )


@retry(
    retry=retry_if_exception_type((RuntimeError, OSError, ValueError)),
    wait=wait_exponential(multiplier=0.25, min=0.25, max=2),
    stop=stop_after_attempt(3),
    reraise=True,
)
def clean_scanned_pdf(
    source_path: Path,
    output_path: Path,
    analysis: ScanAnalysis,
    default_settings: CleanupSettings,
    page_overrides: dict[int, CleanupSettings] | None = None,
) -> Path:
    page_overrides = page_overrides or {}
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with fitz.open(source_path) as document:
        cleaned_document = fitz.open()
        for page_info in analysis.pages:
            page = document[page_info.page_number - 1]
            cleanup_settings = page_overrides.get(page_info.page_number, default_settings).normalized()
            working_image = _render_page_rgb(page, _resolve_render_dpi(page_info, cleanup_settings))
            cleaned_image = _clean_page_image(working_image, cleanup_settings)
            cleaned_bytes = _encode_grayscale_jpeg(cleaned_image, quality=cleanup_settings.jpeg_quality)

            new_page = cleaned_document.new_page(width=page.rect.width, height=page.rect.height)
            new_page.insert_image(new_page.rect, stream=cleaned_bytes)
            _copy_invisible_text(page, new_page)

        cleaned_document.save(output_path, garbage=4, deflate=True)
        cleaned_document.close()

    return output_path


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


def compare_render_metrics(source_pdf: Path, cleaned_pdf: Path, page_numbers: list[int]) -> dict:
    comparisons: list[dict] = []
    for page_number in page_numbers:
        before = _render_page_grayscale(source_pdf, page_number)
        after = _render_page_grayscale(cleaned_pdf, page_number)
        comparisons.append(
            {
                "page_number": page_number,
                "background_before": _background_luminance(before),
                "background_after": _background_luminance(after),
                "dark_p05_before": float(np.percentile(before, 5)),
                "dark_p05_after": float(np.percentile(after, 5)),
            }
        )

    improved_background_pages = sum(
        1 for item in comparisons if item["background_after"] >= item["background_before"] + 3.0
    )
    no_darkening_regressions = all(
        item["background_after"] >= item["background_before"] - 2.0
        for item in comparisons
    )
    foreground_pages = [item for item in comparisons if item["dark_p05_before"] <= 220.0]
    if foreground_pages:
        dark_stroke_retention = min(
            1.0,
            min(
                max(
                    0.0,
                    1.0 - ((item["dark_p05_after"] - item["dark_p05_before"]) / 24.0),
                )
                for item in foreground_pages
            ),
        )
    else:
        dark_stroke_retention = 1.0

    return {
        "pages": comparisons,
        "improved_background_pages": improved_background_pages,
        "no_darkening_regressions": no_darkening_regressions,
        "dark_stroke_retention": dark_stroke_retention,
    }


def generate_before_after_contact_sheet(
    source_pdf: Path,
    cleaned_pdf: Path,
    page_numbers: list[int],
    output_path: Path,
    dpi: int = 96,
) -> Path:
    rows: list[Image.Image] = []
    for page_number in page_numbers:
        before = Image.fromarray(_render_page_grayscale(source_pdf, page_number, dpi=dpi))
        after = Image.fromarray(_render_page_grayscale(cleaned_pdf, page_number, dpi=dpi))
        before_rgb = ImageOps.colorize(before, black="#111111", white="#fcfaf2")
        after_rgb = ImageOps.colorize(after, black="#111111", white="#ffffff")
        width = max(before_rgb.width, after_rgb.width)
        row = Image.new("RGB", (width * 2 + 24, max(before_rgb.height, after_rgb.height) + 42), "#f6f0df")
        row.paste(Image.new("RGB", (row.width, 42), "#1f2a37"), (0, 0))
        row.paste(before_rgb, (0, 42))
        row.paste(after_rgb, (width + 24, 42))
        rows.append(row)

    if not rows:
        raise ValueError("At least one page is required to generate a contact sheet.")

    sheet_width = max(row.width for row in rows)
    sheet_height = sum(row.height for row in rows) + (len(rows) - 1) * 12
    sheet = Image.new("RGB", (sheet_width, sheet_height), "#f0ead6")
    cursor = 0
    for row in rows:
        sheet.paste(row, (0, cursor))
        cursor += row.height + 12

    output_path.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(output_path, quality=88)
    return output_path


def run_tesseract_similarity(source_pdf: Path, cleaned_pdf: Path, page_number: int) -> float | None:
    if not _tesseract_available():
        return None
    before = _render_page_grayscale(source_pdf, page_number)
    after = _render_page_grayscale(cleaned_pdf, page_number)
    before_text = _ocr_image(before)
    after_text = _ocr_image(after)
    if not before_text and not after_text:
        return 1.0
    return SequenceMatcher(None, _normalize_text(before_text), _normalize_text(after_text)).ratio()


def _find_full_page_candidates(document: fitz.Document, page: fitz.Page) -> list[dict]:
    page_area = page.rect.width * page.rect.height
    candidates: list[dict] = []
    for image in page.get_images(full=True):
        xref, smask = image[0], image[1]
        rects = page.get_image_rects(xref)
        if not rects:
            continue
        largest_rect = max(rects, key=lambda rect: rect.width * rect.height)
        coverage = (largest_rect.width * largest_rect.height) / page_area if page_area else 0
        if coverage < 0.80:
            continue
        image_info = document.extract_image(xref)
        if not image_info:
            continue
        candidates.append(
            {
                "xref": xref,
                "smask": smask,
                "width": image_info.get("width", 0),
                "rect": largest_rect,
                "coverage": coverage,
                "byte_size": len(image_info.get("image", b"")),
            }
        )

    candidates.sort(key=lambda item: (item["coverage"], item["byte_size"]), reverse=True)
    return candidates


def _clean_page_image(image: np.ndarray, settings: CleanupSettings) -> np.ndarray:
    rgb = image.astype(np.uint8)
    lab = cv2.cvtColor(rgb, cv2.COLOR_RGB2LAB)
    l_channel, a_channel, b_channel = cv2.split(lab)

    neutral = np.full_like(a_channel, 128)
    tint_weight = settings.strength * 0.82
    a_channel = cv2.addWeighted(a_channel, 1.0 - tint_weight, neutral, tint_weight, 0.0)
    b_channel = cv2.addWeighted(b_channel, 1.0 - tint_weight, neutral, tint_weight, 0.0)
    neutralized = cv2.cvtColor(cv2.merge([l_channel, a_channel, b_channel]), cv2.COLOR_LAB2RGB)

    gray = cv2.cvtColor(neutralized, cv2.COLOR_RGB2GRAY)
    if float(np.percentile(gray, 95) - np.percentile(gray, 5)) < 14.0:
        lifted = gray.astype(np.float32)
        lifted = lifted + ((255.0 - lifted) * (0.46 + settings.strength * 0.24))
        output = np.clip(lifted, 0, 255).astype(np.uint8)
        output[output > settings.white_point - 10] = 255
        return output

    sigma = max(12.0, 34.0 * settings.strength)
    background = cv2.GaussianBlur(gray, (0, 0), sigmaX=sigma, sigmaY=sigma)
    flattened = cv2.divide(gray, background, scale=255)

    clahe = cv2.createCLAHE(clipLimit=1.4 + settings.contrast, tileGridSize=(8, 8))
    enhanced = clahe.apply(flattened)
    threshold_hint = cv2.adaptiveThreshold(
        enhanced,
        255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY,
        35,
        12,
    )
    blended = cv2.addWeighted(enhanced, 0.76, threshold_hint, 0.24, 0.0).astype(np.float32)

    low = float(np.percentile(blended, 3))
    high = max(float(np.percentile(blended, 98)), low + 8.0)
    normalized = np.clip((blended - low) / (high - low), 0.0, 1.0)
    gamma = max(0.62, 0.88 - (settings.contrast - 1.0) * 0.22)
    normalized = np.power(normalized, gamma)
    grayscale = (normalized * 255.0).astype(np.uint8)
    grayscale[grayscale > settings.white_point - 8] = 255

    return cv2.addWeighted(grayscale, 1.12, cv2.GaussianBlur(grayscale, (0, 0), 0.9), -0.12, 0.0)


def _encode_grayscale_jpeg(image: np.ndarray, quality: int = 92) -> bytes:
    success, encoded = cv2.imencode(
        ".jpg",
        image,
        [cv2.IMWRITE_JPEG_QUALITY, quality, cv2.IMWRITE_JPEG_PROGRESSIVE, 1],
    )
    if not success:
        raise RuntimeError("Failed to encode the cleaned page image.")
    return encoded.tobytes()


def _resolve_render_dpi(page_info: PageAnalysis, settings: CleanupSettings) -> int:
    effective_dpi = page_info.effective_dpi
    if effective_dpi is None or effective_dpi <= 0:
        return settings.dpi_cap
    return min(max(round(effective_dpi), 1), settings.dpi_cap)


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
    return min(settings.dpi_cap, preview_dpi)


def _fit_preview_matrix(page_width_points: float, target_width_px: int) -> fitz.Matrix:
    if page_width_points <= 0:
        return fitz.Matrix(1, 1)
    scale = max(target_width_px / page_width_points, 0.2)
    return fitz.Matrix(scale, scale)


def _effective_dpi(image_width_px: int, rect_width_points: float) -> float | None:
    if image_width_px <= 0 or rect_width_points <= 0:
        return None
    return image_width_px / (rect_width_points / 72.0)


def _render_page_rgb(page: fitz.Page, dpi: int) -> np.ndarray:
    scale = dpi / 72
    pixmap = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
    return np.array(Image.open(io.BytesIO(pixmap.tobytes("png"))).convert("RGB"))


def _render_page_grayscale(pdf_path: Path, page_number: int, dpi: int = 150) -> np.ndarray:
    with fitz.open(pdf_path) as document:
        page = document[page_number - 1]
        scale = dpi / 72
        pixmap = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
    image = Image.open(io.BytesIO(pixmap.tobytes("png"))).convert("L")
    return np.array(image)


def _copy_invisible_text(source_page: fitz.Page, target_page: fitz.Page) -> None:
    text = source_page.get_text("text").strip()
    if not text:
        return
    # A full positional OCR rebuild would be heavier and more fragile here.
    # A white text layer preserves searchability without materially changing the printed page.
    target_page.insert_textbox(
        fitz.Rect(18, 18, target_page.rect.width - 18, target_page.rect.height - 18),
        text,
        fontsize=7,
        fontname="helv",
        color=(1, 1, 1),
        overlay=True,
    )


def _background_luminance(image: np.ndarray) -> float:
    return float(np.mean(image[image >= np.percentile(image, 80)]))


def _tesseract_available() -> bool:
    try:
        return subprocess.call(
            ["tesseract", "--version"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        ) == 0
    except FileNotFoundError:
        return False


def _ocr_image(image: np.ndarray) -> str:
    pil_image = Image.fromarray(image)
    with NamedTemporaryFile(suffix=".png", delete=False) as temp_image:
        temp_path = Path(temp_image.name)
    try:
        pil_image.save(temp_path)
        result = subprocess.run(
            ["tesseract", str(temp_path), "stdout", "--psm", "6"],
            capture_output=True,
            check=False,
            text=True,
        )
        return result.stdout.strip()
    finally:
        temp_path.unlink(missing_ok=True)


def _normalize_text(text: str) -> str:
    return " ".join(text.lower().split())
