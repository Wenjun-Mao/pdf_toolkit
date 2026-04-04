from __future__ import annotations

from io import BytesIO
from pathlib import Path

import fitz
from PIL import Image, ImageOps
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential


@retry(
    retry=retry_if_exception_type((RuntimeError, OSError, ValueError)),
    wait=wait_exponential(multiplier=0.25, min=0.25, max=2),
    stop=stop_after_attempt(3),
    reraise=True,
)
def id_halves_to_pdf(
    top_image_path: Path,
    bottom_image_path: Path,
    output_path: Path,
    *,
    fallback_dpi: int = 300,
    jpeg_quality: int = 95,
) -> Path:
    """Build a single-page PDF from the top half of one image and bottom half of another.

    Steps:
    1. Load and EXIF-normalize both images.
    2. Crop top / bottom halves and align them onto a shared canvas width.
    3. Encode the composite image and insert it into a one-page PDF.
    """

    if not top_image_path.exists():
        raise FileNotFoundError(f"Top image was not found: {top_image_path}")
    if not bottom_image_path.exists():
        raise FileNotFoundError(f"Bottom image was not found: {bottom_image_path}")
    if fallback_dpi <= 0:
        raise ValueError("fallback_dpi must be greater than 0.")
    if not 1 <= jpeg_quality <= 100:
        raise ValueError("jpeg_quality must be between 1 and 100.")

    with Image.open(top_image_path) as top_image, Image.open(bottom_image_path) as bottom_image:
        top_prepared = _normalize_image(ImageOps.exif_transpose(top_image))
        bottom_prepared = _normalize_image(ImageOps.exif_transpose(bottom_image))

        top_half = _crop_top_half(top_prepared)
        bottom_half = _crop_bottom_half(bottom_prepared)

        composite = _combine_halves(top_half, bottom_half)
        dpi_x, dpi_y = _resolve_dpi(top_prepared, fallback_dpi=fallback_dpi)
        image_payload = _encode_composite(composite, jpeg_quality=jpeg_quality)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    page_width_pt = composite.width * 72.0 / dpi_x
    page_height_pt = composite.height * 72.0 / dpi_y

    document = fitz.open()
    try:
        page = document.new_page(width=page_width_pt, height=page_height_pt)
        page.draw_rect(page.rect, color=None, fill=(1, 1, 1))
        page.insert_image(page.rect, stream=image_payload)
        document.save(output_path, garbage=4, deflate=True)
    finally:
        document.close()

    return output_path


def _normalize_image(image: Image.Image) -> Image.Image:
    if image.mode in {"RGBA", "LA"} or (image.mode == "P" and "transparency" in image.info):
        rgba_image = image.convert("RGBA")
        background = Image.new("RGBA", rgba_image.size, (255, 255, 255, 255))
        composited = Image.alpha_composite(background, rgba_image)
        return composited.convert("RGB")
    if image.mode in {"1", "L", "RGB"}:
        return image.copy()
    if image.mode == "CMYK":
        return image.convert("RGB")
    return image.convert("RGB")


def _crop_top_half(image: Image.Image) -> Image.Image:
    midpoint = max(image.height // 2, 1)
    return image.crop((0, 0, image.width, midpoint))


def _crop_bottom_half(image: Image.Image) -> Image.Image:
    half_height = max(image.height // 2, 1)
    top = image.height - half_height
    return image.crop((0, top, image.width, image.height))


def _combine_halves(top_half: Image.Image, bottom_half: Image.Image) -> Image.Image:
    canvas_width = max(top_half.width, bottom_half.width)
    canvas_height = top_half.height + bottom_half.height
    canvas = Image.new("RGB", (canvas_width, canvas_height), color=(255, 255, 255))

    top_x = (canvas_width - top_half.width) // 2
    bottom_x = (canvas_width - bottom_half.width) // 2
    canvas.paste(top_half, (top_x, 0))
    canvas.paste(bottom_half, (bottom_x, top_half.height))
    return canvas


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


def _encode_composite(image: Image.Image, *, jpeg_quality: int) -> bytes:
    if image.convert("RGB").getcolors(maxcolors=257) is not None:
        buffer = BytesIO()
        image.save(buffer, format="PNG", optimize=True)
        return buffer.getvalue()

    buffer = BytesIO()
    image.save(
        buffer,
        format="JPEG",
        quality=jpeg_quality,
        optimize=True,
        progressive=True,
        subsampling=0,
    )
    return buffer.getvalue()