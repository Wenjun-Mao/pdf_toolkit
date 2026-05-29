from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import fitz
import pytest

from pdf_toolkit.jobs import create_job, update_job_fields
from pdf_toolkit.models import JobStatus
from pdf_toolkit.pdf_ops import analyze_scan_pdf


def test_scan_cleanup_tuning_form_can_reset_global_defaults(
    open_app_client,
    sample_scan_pdf: Path,
    tmp_path: Path,
) -> None:
    job = _create_scan_analysis_job(sample_scan_pdf, tmp_path / "previews")

    response = open_app_client.get(f"/jobs/{job.id}")

    assert response.status_code == 200
    assert "Optimize" in response.text
    assert f'hx-post="/tools/scan-cleanup/{job.id}/optimize"' in response.text
    assert f'hx-target="#scan-optimize-result-{job.id}"' in response.text
    assert f'id="scan-optimize-result-{job.id}"' in response.text
    assert "Reset defaults" in response.text
    assert 'x-on:click="resetScanDefaults($el.form)"' in response.text
    assert 'data-preview-submit' in response.text
    assert 'name="strength" step="0.05" min="0" max="1" value="0.65" data-scan-default="0.65"' in response.text
    assert 'name="white_point" step="1" min="215" max="252" value="242" data-scan-default="242"' in response.text
    assert 'name="contrast" step="0.05" min="0.7" max="1.6" value="1.05" data-scan-default="1.05"' in response.text
    assert 'name="dpi_cap" step="1" min="120" max="600" value="300" data-scan-default="300"' in response.text
    assert 'name="jpeg_quality" step="1" min="70" max="100" value="92" data-scan-default="92"' in response.text
    assert "Reset all tuning" not in response.text


def test_scan_cleanup_tuning_form_can_reset_everything_when_page_overrides_exist(
    open_app_client,
    sample_image_inputs: list[Path],
    tmp_path: Path,
) -> None:
    source_pdf = tmp_path / "two-page-scan.pdf"
    document = fitz.open()
    for image_path in sample_image_inputs:
        page = document.new_page(width=300, height=225)
        page.insert_image(page.rect, filename=str(image_path))
    document.save(source_pdf)
    document.close()
    job = _create_scan_analysis_job(source_pdf, tmp_path / "previews")

    response = open_app_client.get(f"/jobs/{job.id}")

    assert response.status_code == 200
    assert "Reset all tuning" in response.text
    assert "Reset global settings and clear all per-page overrides?" in response.text
    assert 'x-on:click="confirm(' in response.text
    assert 'resetAllScanTuning($el.form)' in response.text
    assert len(re.findall(r"<input[^>]+data-page-override", response.text)) == 6


def test_scan_cleanup_base_includes_apply_optimized_settings(open_app_client) -> None:
    response = open_app_client.get("/tools/scan-cleanup")

    assert response.status_code == 200
    assert '<script defer src="/static/scan_cleanup.js"></script>' in response.text
    assert "applyOptimizedScanSettings(proposal, form)" in response.text
    assert "window.applyOptimizedScanSettingsToForm(" in response.text
    assert "(targetForm) => this.refreshScanPreview(targetForm)" in response.text


def test_apply_optimized_scan_settings_static_helper_updates_form_only() -> None:
    node_path = shutil.which("node")
    if node_path is None:
        pytest.skip("Node.js is required to execute scan cleanup apply behavior test.")

    script_path = (
        Path(__file__).parents[1] / "pdf_toolkit" / "web" / "static" / "scan_cleanup.js"
    )
    node_test = r"""
const assert = require("assert");
const fs = require("fs");
const vm = require("vm");

const source = fs.readFileSync(process.argv[1], "utf8");
const context = { window: {} };
vm.createContext(context);
vm.runInContext(source, context, { filename: process.argv[1] });

assert.strictEqual(typeof context.window.applyOptimizedScanSettingsToForm, "function");

const fields = {
  strength: { value: "0.65" },
  white_point: { value: "242" },
  contrast: { value: "1.05" },
  dpi_cap: { value: "300" },
  jpeg_quality: { value: "92" },
  page_1_strength: { value: "0.40" },
  page_1_white_point: { value: "230" },
  page_1_contrast: { value: "0.95" },
};
const form = {
  querySelector(selector) {
    const nameMatch = selector.match(/^\[name="(.+)"\]$/);
    if (!nameMatch) {
      return null;
    }
    return fields[nameMatch[1]] || null;
  },
};
const state = { textContent: "Optimized settings have not been applied yet." };
const proposal = {
  dataset: {
    optimizedStrength: "0.80",
    optimizedWhitePoint: "248",
    optimizedContrast: "1.20",
    optimizedDpiCap: "240",
    optimizedJpegQuality: "88",
  },
  querySelector(selector) {
    return selector === "[data-optimization-state]" ? state : null;
  },
};
const refreshCalls = [];

context.window.applyOptimizedScanSettingsToForm(
  proposal,
  form,
  (targetForm) => refreshCalls.push(targetForm),
);

assert.strictEqual(fields.strength.value, "0.80");
assert.strictEqual(fields.white_point.value, "248");
assert.strictEqual(fields.contrast.value, "1.20");
assert.strictEqual(fields.dpi_cap.value, "240");
assert.strictEqual(fields.jpeg_quality.value, "88");
assert.strictEqual(fields.page_1_strength.value, "0.40");
assert.strictEqual(fields.page_1_white_point.value, "230");
assert.strictEqual(fields.page_1_contrast.value, "0.95");
assert.strictEqual(state.textContent, "Optimized settings applied. Preview refresh requested.");
assert.strictEqual(refreshCalls.length, 1);
assert.strictEqual(refreshCalls[0], form);
"""
    result = subprocess.run(
        [node_path, "-e", node_test, str(script_path)],
        cwd=Path(__file__).parents[1],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


def _create_scan_analysis_job(source_pdf: Path, preview_dir: Path):
    analysis = analyze_scan_pdf(source_pdf, preview_dir)
    analysis_payload = analysis.to_json()
    for page_payload in analysis_payload["pages"]:
        page_payload["preview_path"] = Path(page_payload["preview_path"]).name
    job = create_job(
        "scan-cleanup-analysis",
        "Analyze Scan Cleanup",
        [source_pdf],
    )
    update_job_fields(
        job.id,
        status=JobStatus.AWAITING_SETTINGS.value,
        artifact_json={"analysis": analysis_payload},
    )
    return job
