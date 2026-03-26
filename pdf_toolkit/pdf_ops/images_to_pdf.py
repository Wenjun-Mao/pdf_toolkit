from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

import fitz
from PIL import Image, ImageOps
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

JPEG_SOURCE_FORMATS = {"JPEG", "JPG"}
LOSSLESS_SOURCE_FORMATS = {"PNG", "GIF", "BMP", "TIFF"}
PAGE_SIZE_PRESETS: dict[str, tuple[float, float]] = {
    "a4": (595.2756, 841.8898),
    "letter": (612.0, 792.0),
}
PLACEMENT_MODES = {"fit", "fill"}


@dataclass(slots=True)
class PreparedImage:
    payload: bytes
    natural_width_pt: float
    natural_height_pt: float


@retry(
    retry=retry_if_exception_type((RuntimeError, OSError, ValueError)),
    wait=wait_exponential(multiplier=0.25, min=0.25, max=2),
    stop=stop_after_attempt(3),
    reraise=True,
)
def images_to_pdf(
    source_paths: list[Path],
    output_path: Path,
    *,
    fallback_dpi: int = 300,
    jpeg_quality: int = 95,
    page_size: str = "original",
    margin_mm: float = 0.0,
    placement: str = "fit",
) -> Path:
    """Combine images into a PDF with explicit sizing and PDF-friendly encoding.

    Steps:
    1. Read each image through Pillow so EXIF orientation and DPI metadata are available.
    2. Preserve original JPEG bytes when that is safe to avoid generation loss.
    3. Flatten transparency and pick lossless PNG or high-quality JPEG when a transcode is needed.
    4. Size each PDF page from the image DPI metadata, with a robust fallback when metadata is missing.
    5. Optionally place each image onto a fixed page size with margins and fit / fill behavior.
    """

    if not source_paths:
        raise ValueError("At least one image is required.")
    if fallback_dpi <= 0:
        raise ValueError("fallback_dpi must be greater than 0.")
    if not 1 <= jpeg_quality <= 100:
        raise ValueError("jpeg_quality must be between 1 and 100.")
    normalized_page_size = _normalize_page_size(page_size)
    if margin_mm < 0:
        raise ValueError("margin_mm must be greater than or equal to 0.")
    normalized_placement = placement.strip().lower()
    if normalized_placement not in PLACEMENT_MODES:
        raise ValueError(f"placement must be one of {sorted(PLACEMENT_MODES)}.")
    margin_pt = margin_mm * 72.0 / 25.4

    output_path.parent.mkdir(parents=True, exist_ok=True)
    document = fitz.open()
    try:
        for source_path in source_paths:
            prepared = _prepare_image_for_pdf(
                source_path,
                fallback_dpi=fallback_dpi,
                jpeg_quality=jpeg_quality,
            )
            page_rect, content_rect = _resolve_page_geometry(
                prepared,
                page_size=normalized_page_size,
                margin_pt=margin_pt,
            )
            image_rect = _resolve_image_rect(
                content_rect,
                prepared,
                placement=normalized_placement,
            )
            page = document.new_page(width=page_rect.width, height=page_rect.height)
            page.draw_rect(page.rect, color=None, fill=(1, 1, 1))
            page.insert_image(image_rect, stream=prepared.payload)
            if normalized_placement == "fill":
                _mask_margins(page, content_rect)
        document.save(output_path, garbage=4, deflate=True)
    finally:
        document.close()
    return output_path


def _prepare_image_for_pdf(
    source_path: Path,
    *,
    fallback_dpi: int,
    jpeg_quality: int,
) -> PreparedImage:
    if not source_path.exists():
        raise FileNotFoundError(f"Image file was not found: {source_path}")

    with Image.open(source_path) as opened_image:
        orientation = opened_image.getexif().get(274, 1)
        transposed_image = ImageOps.exif_transpose(opened_image)
        dpi_x, dpi_y = _resolve_dpi(transposed_image, fallback_dpi=fallback_dpi)
        page_width_pt = transposed_image.width * 72.0 / dpi_x
        page_height_pt = transposed_image.height * 72.0 / dpi_y
        payload = _encode_for_pdf(
            source_path,
            opened_image=opened_image,
            prepared_image=transposed_image,
            orientation=orientation,
            jpeg_quality=jpeg_quality,
        )

    return PreparedImage(
        payload=payload,
        natural_width_pt=page_width_pt,
        natural_height_pt=page_height_pt,
    )


def _resolve_dpi(image: Image.Image, *, fallback_dpi: int) -> tuple[float, float]:
    dpi = image.info.get("dpi")
    if not isinstance(dpi, tuple) or len(dpi) < 2:
        return float(fallback_dpi), float(fallback_dpi)

    normalized: list[float] = []
    for raw_value in dpi[:2]:
        try:
            value = float(raw_value)
        except (TypeError, ValueError):
            value = float(fallback_dpi)
        if value < 36 or value > 1200:
            value = float(fallback_dpi)
        normalized.append(value)
    return normalized[0], normalized[1]


def _encode_for_pdf(
    source_path: Path,
    *,
    opened_image: Image.Image,
    prepared_image: Image.Image,
    orientation: int,
    jpeg_quality: int,
) -> bytes:
    source_format = (opened_image.format or source_path.suffix.lstrip(".")).upper()
    needs_transform = orientation != 1 or _needs_alpha_flatten(prepared_image)

    if source_format in JPEG_SOURCE_FORMATS and not needs_transform:
        return source_path.read_bytes()

    working_image = _normalize_image_mode(prepared_image)
    if source_format in LOSSLESS_SOURCE_FORMATS and not needs_transform and _prefer_lossless_encoding(working_image, source_format):
        return source_path.read_bytes()

    buffer = BytesIO()
    save_kwargs = {}

    if _prefer_lossless_encoding(working_image, source_format):
        working_image.save(buffer, format="PNG", optimize=True)
    else:
        working_image = working_image.convert("RGB")
        icc_profile = opened_image.info.get("icc_profile")
        if icc_profile:
            save_kwargs["icc_profile"] = icc_profile
        working_image.save(
            buffer,
            format="JPEG",
            quality=jpeg_quality,
            optimize=True,
            progressive=True,
            subsampling=0,
            **save_kwargs,
        )
    return buffer.getvalue()


def _needs_alpha_flatten(image: Image.Image) -> bool:
    return image.mode in {"RGBA", "LA"} or (image.mode == "P" and "transparency" in image.info)


def _normalize_image_mode(image: Image.Image) -> Image.Image:
    if _needs_alpha_flatten(image):
        rgba_image = image.convert("RGBA")
        background = Image.new("RGBA", rgba_image.size, (255, 255, 255, 255))
        composited = Image.alpha_composite(background, rgba_image)
        return composited.convert("RGB")

    if image.mode in {"1", "L", "RGB"}:
        return image.copy()
    if image.mode == "CMYK":
        return image.copy()
    return image.convert("RGB")


def _prefer_lossless_encoding(image: Image.Image, source_format: str) -> bool:
    if image.mode in {"1", "L"}:
        return True
    if source_format not in LOSSLESS_SOURCE_FORMATS:
        return False
    if image.width * image.height > 8_000_000:
        return False
    return image.convert("RGB").getcolors(maxcolors=257) is not None


def _normalize_page_size(page_size: str) -> str:
    normalized = page_size.strip().lower()
    if normalized == "":
        normalized = "original"
    if normalized != "original" and normalized not in PAGE_SIZE_PRESETS:
        raise ValueError(f"page_size must be 'original' or one of {sorted(PAGE_SIZE_PRESETS)}.")
    return normalized


def _resolve_page_geometry(
    prepared: PreparedImage,
    *,
    page_size: str,
    margin_pt: float,
) -> tuple[fitz.Rect, fitz.Rect]:
    if page_size == "original":
        page_width_pt = prepared.natural_width_pt + margin_pt * 2
        page_height_pt = prepared.natural_height_pt + margin_pt * 2
    else:
        page_width_pt, page_height_pt = PAGE_SIZE_PRESETS[page_size]
        if prepared.natural_width_pt > prepared.natural_height_pt and page_width_pt < page_height_pt:
            page_width_pt, page_height_pt = page_height_pt, page_width_pt
        if prepared.natural_width_pt < prepared.natural_height_pt and page_width_pt > page_height_pt:
            page_width_pt, page_height_pt = page_height_pt, page_width_pt

    if margin_pt * 2 >= min(page_width_pt, page_height_pt):
        raise ValueError("Margins are too large for the selected page size.")

    page_rect = fitz.Rect(0, 0, page_width_pt, page_height_pt)
    content_rect = fitz.Rect(
        margin_pt,
        margin_pt,
        page_width_pt - margin_pt,
        page_height_pt - margin_pt,
    )
    return page_rect, content_rect


def _resolve_image_rect(
    content_rect: fitz.Rect,
    prepared: PreparedImage,
    *,
    placement: str,
) -> fitz.Rect:
    image_aspect = prepared.natural_width_pt / prepared.natural_height_pt
    content_aspect = content_rect.width / content_rect.height

    if placement == "fit":
        if image_aspect >= content_aspect:
            target_width = content_rect.width
            target_height = target_width / image_aspect
        else:
            target_height = content_rect.height
            target_width = target_height * image_aspect
    else:
        if image_aspect >= content_aspect:
            target_height = content_rect.height
            target_width = target_height * image_aspect
        else:
            target_width = content_rect.width
            target_height = target_width / image_aspect

    left = content_rect.x0 + (content_rect.width - target_width) / 2
    top = content_rect.y0 + (content_rect.height - target_height) / 2
    return fitz.Rect(left, top, left + target_width, top + target_height)


def _mask_margins(page: fitz.Page, content_rect: fitz.Rect) -> None:
    strips = [
        fitz.Rect(0, 0, page.rect.width, content_rect.y0),
        fitz.Rect(0, content_rect.y1, page.rect.width, page.rect.height),
        fitz.Rect(0, content_rect.y0, content_rect.x0, content_rect.y1),
        fitz.Rect(content_rect.x1, content_rect.y0, page.rect.width, content_rect.y1),
    ]
    for strip in strips:
        if strip.width <= 0 or strip.height <= 0:
            continue
        page.draw_rect(strip, color=None, fill=(1, 1, 1), overlay=True)
