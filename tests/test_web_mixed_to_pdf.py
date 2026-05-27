from __future__ import annotations

import re
from pathlib import Path


def _upload_tuple(path: Path) -> tuple[str, tuple[str, bytes, str]]:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        content_type = "application/pdf"
    elif suffix in {".jpg", ".jpeg"}:
        content_type = "image/jpeg"
    else:
        content_type = "image/png"
    return ("files", (path.name, path.read_bytes(), content_type))


def _parse_ordered_file_ids(html: str) -> list[str]:
    file_ids = []
    seen_file_ids = set()
    for tag_match in re.finditer(r"<[^>]+>", html):
        attributes = {
            match.group("name"): match.group("value")
            for match in re.finditer(
                r'\s(?P<name>[A-Za-z_][\w-]*)=(?P<quote>["\'])(?P<value>.*?)(?P=quote)',
                tag_match.group(0),
            )
        }
        file_id = attributes.get("data-ordered-file-id") or attributes.get("data-file-id")
        if file_id is None and attributes.get("name") == "ordered_file_ids":
            file_id = attributes.get("value")
        if file_id is not None and file_id not in seen_file_ids:
            file_ids.append(file_id)
            seen_file_ids.add(file_id)
    return file_ids


def _upload_mixed_job(app_client, paths: list[Path]) -> tuple[str, list[str]]:
    response = app_client.post(
        "/tools/mixed-to-pdf/upload",
        files=[_upload_tuple(path) for path in paths],
    )
    assert response.status_code == 200
    assert "Build Mixed PDF" in response.text
    match = re.search(r"(/tools/mixed-to-pdf/[0-9a-f-]+/submit)", response.text)
    assert match is not None
    file_ids = _parse_ordered_file_ids(response.text)
    assert len(file_ids) == len(paths)
    return match.group(1), file_ids


def test_ordered_file_id_parser_ignores_client_bound_values() -> None:
    html = """
    <input type="hidden" name="ordered_file_ids" :value="item.id">
    <input type="hidden" name="ordered_file_ids" x-bind:value="item.id">
    <input type="hidden" name="ordered_file_ids" value="server-rendered-id">
    <div data-file-id="same-id">
        <input type="hidden" name="ordered_file_ids" value="same-id">
    </div>
    <input
        type="hidden"
        name="ordered_file_ids"
        data-ordered-file-id="preferred-data-id"
        value="duplicate-value"
    >
    """

    assert _parse_ordered_file_ids(html) == ["server-rendered-id", "same-id", "preferred-data-id"]


def test_mixed_to_pdf_tool_page_is_available(app_client) -> None:
    response = app_client.get("/tools/mixed-to-pdf")

    assert response.status_code == 200
    assert "Mixed to PDF" in response.text
    assert 'accept="application/pdf,image/*"' in response.text


def test_mixed_to_pdf_upload_review_and_submit_completes_inline(
    app_client,
    sample_merge_pdfs: list[Path],
    sample_image_inputs: list[Path],
) -> None:
    ordered_uploads = [sample_merge_pdfs[0], sample_image_inputs[0], sample_merge_pdfs[1]]
    submit_url, file_ids = _upload_mixed_job(app_client, ordered_uploads)

    response = app_client.post(
        submit_url,
        data=[
            ("ordered_file_ids", file_ids[1]),
            ("ordered_file_ids", file_ids[0]),
            ("ordered_file_ids", file_ids[2]),
            ("fallback_dpi", "300"),
            ("jpeg_quality", "95"),
            ("page_size", "letter"),
            ("margin_mm", "12.7"),
            ("placement", "fit"),
        ],
    )

    assert response.status_code == 200
    assert "Download mixed.pdf" in response.text


def test_mixed_to_pdf_submit_rejects_unknown_file_id(
    app_client,
    sample_merge_pdfs: list[Path],
    sample_image_inputs: list[Path],
) -> None:
    submit_url, file_ids = _upload_mixed_job(app_client, [sample_merge_pdfs[0], sample_image_inputs[0]])

    response = app_client.post(
        submit_url,
        data=[
            ("ordered_file_ids", file_ids[0]),
            ("ordered_file_ids", "missing.pdf"),
            ("fallback_dpi", "300"),
            ("jpeg_quality", "95"),
            ("page_size", "original"),
            ("margin_mm", "0"),
            ("placement", "fit"),
        ],
    )

    assert response.status_code == 400
    assert "Submitted file order does not match uploaded files." in response.text
