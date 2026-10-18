from __future__ import annotations

import re
from pathlib import Path

UPLOAD_PREFIX_PATTERN = re.compile(r"^\d{2}-")


def build_ordered_file_item(path: Path, kind: str) -> dict[str, str]:
    return {
        "id": path.name,
        "name": UPLOAD_PREFIX_PATTERN.sub("", path.name),
        "kind": kind,
    }


def resolve_ordered_paths(input_paths: list[str], ordered_file_ids: list[str]) -> list[Path]:
    path_by_id = {Path(path).name: Path(path) for path in input_paths}
    if len(path_by_id) != len(input_paths):
        raise ValueError("Uploaded files have duplicate IDs.")
    if len(ordered_file_ids) != len(path_by_id) or set(ordered_file_ids) != set(path_by_id):
        raise ValueError("Submitted file order does not match uploaded files.")
    return [path_by_id[file_id] for file_id in ordered_file_ids]
