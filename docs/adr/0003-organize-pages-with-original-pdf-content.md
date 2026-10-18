# 0003: Organize Original PDF Pages Through a Review Job

## Status

Accepted

## Context

Extract Pages accepts ranges, and Merge orders whole files. Neither provides a visual workflow for moving and deleting individual pages from one PDF. Rendering the final document from thumbnails would degrade scans and discard searchable text.

## Decision

Organize Pages is a separate tool using the existing PyMuPDF, jobs, storage, and RQ infrastructure. Upload validates a readable, unlocked PDF and stores its page count and original name. The resulting job waits for review and can be reopened from Recent Jobs.

The browser holds an ordered subset of unique, 1-based source page numbers. Users can drag pages, move them with buttons, select multiple pages for deletion, undo changes, and restore the original sequence. Edits remain local until Save PDF; reopening an unfinished session restores the original sequence. At least one page must remain. Password entry, inserting other documents, page duplication, and rotation are outside this initial tool.

Thumbnail and enlarged-preview routes render only the requested source page, at bounded resolution. These images are for review only. Saving validates the ordered subset, atomically claims the awaiting-settings job, and queues PyMuPDF's native page selection and lossless PDF serialization to a new `organized.pdf`. The original upload is not modified. A cancelled session becomes terminal (`cancelled`) and can be cleared with Recent Jobs.

## Rejected Alternatives

- Extending the range text field would not provide visual organization.
- A new browser PDF engine or complete editor adds unnecessary dependencies.
- Rendering every page during upload delays editing; rendering the output from previews loses content and quality.

## Consequences And Guardrails

No new runtime dependency or database migration is required. Tests cover exact output sequence, omissions, unchanged input, image bytes, geometry, annotations, widgets, internal links, invalid orders, single-page output, duplicate submission, lazy rendering, authentication, cancellation, and resuming review. Browser verification covers reordering, selected deletion, undo/reset, enlarged preview, and download.
