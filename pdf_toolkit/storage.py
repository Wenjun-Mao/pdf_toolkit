from __future__ import annotations

import re
import shutil
from pathlib import Path
from typing import Iterable

from fastapi import UploadFile
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from .settings import Settings, get_settings

SAFE_FILENAME_PATTERN = re.compile(r"[^A-Za-z0-9._-]+")


def ensure_storage_dirs(settings: Settings | None = None) -> None:
    active_settings = settings or get_settings()
    for directory in (
        active_settings.data_dir,
        active_settings.uploads_dir,
        active_settings.results_dir,
        active_settings.previews_dir,
        active_settings.contact_sheets_dir,
    ):
        directory.mkdir(parents=True, exist_ok=True)


def sanitize_filename(filename: str, default_stem: str = "file") -> str:
    cleaned = SAFE_FILENAME_PATTERN.sub("_", filename).strip("._")
    return cleaned or default_stem


def job_upload_dir(job_id: str, settings: Settings | None = None) -> Path:
    active_settings = settings or get_settings()
    path = active_settings.uploads_dir / job_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def job_result_dir(job_id: str, settings: Settings | None = None) -> Path:
    active_settings = settings or get_settings()
    path = active_settings.results_dir / job_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def job_preview_dir(job_id: str, settings: Settings | None = None) -> Path:
    active_settings = settings or get_settings()
    path = active_settings.previews_dir / job_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def job_contact_sheet_dir(job_id: str, settings: Settings | None = None) -> Path:
    active_settings = settings or get_settings()
    path = active_settings.contact_sheets_dir / job_id
    path.mkdir(parents=True, exist_ok=True)
    return path


@retry(
    retry=retry_if_exception_type(OSError),
    wait=wait_exponential(multiplier=0.2, min=0.2, max=2),
    stop=stop_after_attempt(3),
    reraise=True,
)
def write_bytes(destination: Path, payload: bytes) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(payload)
    return destination


@retry(
    retry=retry_if_exception_type(OSError),
    wait=wait_exponential(multiplier=0.2, min=0.2, max=2),
    stop=stop_after_attempt(3),
    reraise=True,
)
def copy_file(source: Path, destination: Path) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)
    return destination


async def persist_uploads(
    job_id: str,
    uploads: Iterable[UploadFile],
    settings: Settings | None = None,
) -> list[Path]:
    upload_dir = job_upload_dir(job_id, settings)
    stored_paths: list[Path] = []
    for index, upload in enumerate(uploads, start=1):
        original_name = upload.filename or f"upload-{index}.bin"
        safe_name = f"{index:02d}-{sanitize_filename(original_name, default_stem='upload')}"
        destination = upload_dir / safe_name
        payload = await upload.read()
        write_bytes(destination, payload)
        stored_paths.append(destination)
        await upload.close()
    return stored_paths


def build_result_path(job_id: str, filename: str, settings: Settings | None = None) -> Path:
    return job_result_dir(job_id, settings) / sanitize_filename(filename, default_stem="result")


def build_preview_path(job_id: str, filename: str, settings: Settings | None = None) -> Path:
    return job_preview_dir(job_id, settings) / sanitize_filename(filename, default_stem="preview")


def build_contact_sheet_path(job_id: str, filename: str, settings: Settings | None = None) -> Path:
    return job_contact_sheet_dir(job_id, settings) / sanitize_filename(
        filename,
        default_stem="contact-sheet",
    )


def zip_directory(source_dir: Path, destination: Path) -> Path:
    archive_base = destination.with_suffix("")
    shutil.make_archive(str(archive_base), "zip", root_dir=source_dir)
    return archive_base.with_suffix(".zip")
