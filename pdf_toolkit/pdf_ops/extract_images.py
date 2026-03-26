from __future__ import annotations

from pathlib import Path

import fitz
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from ..storage import write_bytes


@retry(
    retry=retry_if_exception_type((RuntimeError, OSError, ValueError)),
    wait=wait_exponential(multiplier=0.25, min=0.25, max=2),
    stop=stop_after_attempt(3),
    reraise=True,
)
def extract_embedded_images(source_path: Path, output_dir: Path) -> list[dict]:
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest: list[dict] = []
    seen_xrefs: set[int] = set()

    with fitz.open(source_path) as document:
        for page_number, page in enumerate(document, start=1):
            image_entries: list[dict] = []
            images = page.get_images(full=True)
            needs_composite = len(images) > 1 or any(image[1] for image in images)
            for image_index, image in enumerate(images, start=1):
                xref = image[0]
                extracted = document.extract_image(xref)
                if not extracted or not extracted.get("image"):
                    continue

                image_path = output_dir / f"page-{page_number:03d}-image-{image_index:02d}.{extracted['ext']}"
                if xref not in seen_xrefs:
                    write_bytes(image_path, extracted["image"])
                    seen_xrefs.add(xref)

                image_entries.append(
                    {
                        "page_number": page_number,
                        "image_index": image_index,
                        "xref": xref,
                        "smask": image[1],
                        "ext": extracted["ext"],
                        "width": extracted.get("width"),
                        "height": extracted.get("height"),
                        "path": str(image_path),
                    }
                )

            composite_path: Path | None = None
            if needs_composite:
                composite_path = output_dir / f"page-{page_number:03d}-composited.png"
                pixmap = page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
                pixmap.save(composite_path)

            manifest.append(
                {
                    "page_number": page_number,
                    "images": image_entries,
                    "composited_fallback": str(composite_path) if composite_path else None,
                }
            )

    return manifest
