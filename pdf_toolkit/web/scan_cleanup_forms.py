from __future__ import annotations

from starlette.datastructures import FormData

from ..pdf_ops import CleanupSettings
from ..settings import Settings

PAGE_OVERRIDE_FIELDS = {"strength", "white_point", "contrast"}


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
        key_parts = key.split("_", maxsplit=2)
        if len(key_parts) != 3:
            continue
        _, page_number, setting_name = key_parts
        if not page_number.isdigit() or setting_name not in PAGE_OVERRIDE_FIELDS:
            continue
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
