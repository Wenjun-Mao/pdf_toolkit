from __future__ import annotations

from pathlib import Path

import fitz

from pdf_toolkit.jobs import create_job, enqueue_mixed_to_pdf, get_job


def test_mixed_to_pdf_job_completes_inline(
    app_client,
    sample_merge_pdfs: list[Path],
    sample_image_inputs: list[Path],
) -> None:
    job = create_job(
        "mixed-to-pdf",
        "Mixed to PDF",
        [sample_merge_pdfs[1], sample_image_inputs[0], sample_merge_pdfs[0]],
        params_json={
            "fallback_dpi": 300,
            "jpeg_quality": 95,
            "page_size": "original",
            "margin_mm": 0.0,
            "placement": "fit",
        },
    )

    enqueue_mixed_to_pdf(job.id)

    completed_job = get_job(job.id)
    assert completed_job is not None
    assert completed_job.status == "completed"
    assert completed_job.output_path is not None
    assert Path(completed_job.output_path).name == "mixed.pdf"

    with fitz.open(completed_job.output_path) as output_doc:
        assert output_doc.page_count == 4
        assert "Second PDF / Page 1" in output_doc[0].get_text()
        assert output_doc[1].get_images(full=True)
        assert "First PDF / Page 1" in output_doc[2].get_text()
