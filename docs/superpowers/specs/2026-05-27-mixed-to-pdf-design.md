# Mixed PDF Design

## Summary

Add a new `mixed-to-pdf` tool that combines uploaded PDFs and images into one output PDF while preserving a user-selected order. The feature intentionally excludes Microsoft Word and other office formats to keep the project lean and avoid heavy document-rendering dependencies.

The first version works at whole-file granularity. A user can place an image between two PDFs, or arrange several PDFs and images, but cannot yet insert an image between page 4 and page 5 of the same PDF. Page-level assembly is documented as a future mode.

## Goals

- Accept PDF files and raster images in one workflow.
- Let the user review and reorder uploaded files before starting the job.
- Preserve the chosen file order in the final PDF.
- Reuse existing PDF merge and image-to-PDF behavior.
- Avoid new heavyweight dependencies.
- Keep the implementation modular so the web app does not grow into a catch-all file.

## Non-Goals

- Do not support `.doc`, `.docx`, Office files, HTML, or arbitrary document conversion.
- Do not add page-level reordering or page insertion in v1.
- Do not add OCR, compression, scan cleanup, or page editing behavior to this tool.
- Do not replace the existing PDF-only merge tool or images-only tool.

## User Workflow

1. The user opens the new Mixed to PDF tool.
2. The user selects one or more PDFs and images.
3. The app creates a mixed-tool job in `awaiting_settings`, uploads the files into that job's upload folder, and returns a review form.
4. The UI shows a sortable ordered list with filename and file type.
5. The user reorders files with drag handles or accessible up/down controls.
6. The user chooses image conversion settings, using the same options as Images to PDF:
   - fallback DPI
   - JPEG quality
   - page size: original, A4, or Letter
   - margin in mm
   - placement: fit or fill
7. The user submits the final order.
8. The app builds and returns `mixed.pdf`.

## Architecture

Add a focused core operation in `pdf_toolkit/pdf_ops/mixed_to_pdf.py`.

Public API: `mixed_files_to_pdf(source_paths: list[Path], output_path: Path, *, fallback_dpi: int = 300, jpeg_quality: int = 95, page_size: str = "original", margin_mm: float = 0.0, placement: str = "fit") -> Path`.

The function classifies each input as PDF or image, normalizes every input into a PDF segment, then merges the segments into the requested output.

PDF inputs are opened with PyMuPDF to verify they are readable before merging. Image inputs are converted through the existing `images_to_pdf` behavior, one image per temporary segment so ordering remains obvious and future page-level work stays possible.

The operation should use the same `tenacity` retry pattern already used by `merge_pdfs` and `images_to_pdf`, because it performs potentially flaky file I/O.

## Web Integration

Add a new tool id: `mixed-to-pdf`.

The web flow should be a two-step workflow rather than the current immediate-submit form:

1. Upload files and render a review panel.
2. Submit the ordered list and image options to queue the job.

This avoids relying only on browser multipart order and gives the user explicit control.

The upload step should create a normal `Job` record with status `awaiting_settings`, store the persisted upload paths on `input_paths`, and return a mixed-tool review partial instead of a polling job card. The final submit step should validate that the submitted ordered file IDs correspond exactly to that job's stored uploads, update `input_paths` into the selected order, store image settings in `params_json`, set the status to `queued`, and enqueue processing.

Because `pdf_toolkit/web/app.py` is already close to the project file-size warning threshold, the implementation should include a small extraction for tool route registration or mixed-tool helpers instead of adding a large route block directly into that file.

## CLI Integration

Add a command:

```powershell
uv run pdfkit mixed-to-pdf output.pdf file1.pdf image1.jpg file2.pdf image2.png
```

The CLI should accept the same image options as `images-to-pdf`. The command order is the source of truth.

## Job Integration

Add:

- `enqueue_mixed_to_pdf`
- `run_mixed_to_pdf_job`
- `_mixed_to_pdf_job_impl`

The job output should be `mixed.pdf`.

The job should read the ordered input paths and image options from the job record. The two-step web workflow should reuse the current job model instead of introducing a separate draft table.

## Data Flow

1. Receive ordered paths.
2. Validate that at least one input is present.
3. For each input:
   - If it is a PDF, validate readability and add it as a segment.
   - If it is an image, convert it to a temporary single-image PDF segment.
   - If it is unsupported, raise a clear validation error.
4. Merge all segments into `mixed.pdf`.
5. Clean up temporary conversion outputs.

## Error Handling

Unsupported files should fail with a clear message:

```text
Unsupported mixed PDF input: report.docx. Use PDF or image files.
```

Unreadable PDFs should identify the failing file. Invalid images should keep the existing Pillow/PyMuPDF error context where useful, but the web notice should remain user-readable.

The web upload step should reject empty selections. The submit step should reject missing or unknown file IDs and any ordered list that does not match the uploaded files for that draft/job.

## Testing

Add focused tests for:

- Core PDF + image + PDF assembly produces the expected page count.
- Output page order follows the requested order.
- Image page options still affect image-derived pages.
- Unsupported extensions fail with a clear error.
- Web upload/reorder/submit flow completes inline and offers `Download mixed.pdf`.

Run the existing pytest suite after implementation. Add CLI coverage only if it can be done without broad test harness changes.

## Future Page Assembly Mode

Page-level insertion and reordering should be a separate advanced workflow.

That future mode would treat each PDF as page ranges or expanded pages, while images become one-page insertable items. Example:

```text
book.pdf pages 1-4
photo.jpg
book.pdf pages 5-10
```

Keeping this out of v1 prevents `mixed-to-pdf` from becoming a PDF editor while still leaving a clean path to a more powerful assembly feature later.
