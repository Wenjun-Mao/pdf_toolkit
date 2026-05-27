from __future__ import annotations

import argparse
import json
from pathlib import Path

from .pdf_ops import (
    CleanupSettings,
    analyze_scan_pdf,
    clean_scanned_pdf,
    extract_embedded_images,
    extract_pages,
    id_halves_to_pdf,
    images_to_pdf,
    merge_pdfs,
    mixed_files_to_pdf,
    split_pdf,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="pdfkit", description="Run PDF toolkit operations from the command line.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    merge_parser = subparsers.add_parser("merge", help="Merge multiple PDFs into one.")
    merge_parser.add_argument("output", type=Path)
    merge_parser.add_argument("inputs", nargs="+", type=Path)

    split_parser = subparsers.add_parser("split", help="Split a PDF by groups or every N pages.")
    split_parser.add_argument("input", type=Path)
    split_parser.add_argument("output_dir", type=Path)
    split_group = split_parser.add_mutually_exclusive_group(required=True)
    split_group.add_argument("--ranges", type=str, help="Group syntax like 1-3;4-6;7-10")
    split_group.add_argument("--every", type=int, help="Split every N pages")

    extract_parser = subparsers.add_parser("extract-pages", help="Extract selected pages into one PDF.")
    extract_parser.add_argument("input", type=Path)
    extract_parser.add_argument("output", type=Path)
    extract_parser.add_argument("--pages", required=True, type=str)

    images_parser = subparsers.add_parser("extract-images", help="Export embedded images from a PDF.")
    images_parser.add_argument("input", type=Path)
    images_parser.add_argument("output_dir", type=Path)

    images_to_pdf_parser = subparsers.add_parser("images-to-pdf", help="Combine multiple images into one PDF.")
    images_to_pdf_parser.add_argument("output", type=Path)
    images_to_pdf_parser.add_argument("inputs", nargs="+", type=Path)
    images_to_pdf_parser.add_argument("--fallback-dpi", type=int, default=300)
    images_to_pdf_parser.add_argument("--jpeg-quality", type=int, default=95)
    images_to_pdf_parser.add_argument("--page-size", type=str, default="original", choices=["original", "a4", "letter"])
    images_to_pdf_parser.add_argument("--margin-mm", type=float, default=0.0)
    images_to_pdf_parser.add_argument("--placement", type=str, default="fit", choices=["fit", "fill"])

    mixed_parser = subparsers.add_parser("mixed-to-pdf", help="Combine PDFs and images into one PDF.")
    mixed_parser.add_argument("output", type=Path)
    mixed_parser.add_argument("inputs", nargs="+", type=Path)
    mixed_parser.add_argument("--fallback-dpi", type=int, default=300)
    mixed_parser.add_argument("--jpeg-quality", type=int, default=95)
    mixed_parser.add_argument("--page-size", type=str, default="original", choices=["original", "a4", "letter"])
    mixed_parser.add_argument("--margin-mm", type=float, default=0.0)
    mixed_parser.add_argument("--placement", type=str, default="fit", choices=["fit", "fill"])

    id_halves_parser = subparsers.add_parser(
        "id-halves-to-pdf",
        help="Build one PDF page from the top half of image one and bottom half of image two.",
    )
    id_halves_parser.add_argument("top_image", type=Path)
    id_halves_parser.add_argument("bottom_image", type=Path)
    id_halves_parser.add_argument("output", type=Path)
    id_halves_parser.add_argument("--fallback-dpi", type=int, default=300)
    id_halves_parser.add_argument("--jpeg-quality", type=int, default=95)

    analyze_parser = subparsers.add_parser("scan-analyze", help="Analyze a scanned PDF and generate previews.")
    analyze_parser.add_argument("input", type=Path)
    analyze_parser.add_argument("preview_dir", type=Path)

    cleanup_parser = subparsers.add_parser("scan-cleanup", help="Clean scan backgrounds while preserving OCR layers.")
    cleanup_parser.add_argument("input", type=Path)
    cleanup_parser.add_argument("output", type=Path)
    cleanup_parser.add_argument("--strength", type=float, default=0.65)
    cleanup_parser.add_argument("--white-point", type=int, default=242)
    cleanup_parser.add_argument("--contrast", type=float, default=1.05)
    cleanup_parser.add_argument("--preview-dir", type=Path, default=Path("data/cli-previews"))

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    if args.command == "merge":
        merge_pdfs(args.inputs, args.output)
        print(args.output.resolve())
        return

    if args.command == "split":
        split_pdf(args.input, args.output_dir, range_spec=args.ranges, every_n=args.every)
        print(args.output_dir.resolve())
        return

    if args.command == "extract-pages":
        extract_pages(args.input, args.pages, args.output)
        print(args.output.resolve())
        return

    if args.command == "extract-images":
        manifest = extract_embedded_images(args.input, args.output_dir)
        print(json.dumps(manifest, indent=2))
        return

    if args.command == "images-to-pdf":
        images_to_pdf(
            args.inputs,
            args.output,
            fallback_dpi=args.fallback_dpi,
            jpeg_quality=args.jpeg_quality,
            page_size=args.page_size,
            margin_mm=args.margin_mm,
            placement=args.placement,
        )
        print(args.output.resolve())
        return

    if args.command == "mixed-to-pdf":
        mixed_files_to_pdf(
            args.inputs,
            args.output,
            fallback_dpi=args.fallback_dpi,
            jpeg_quality=args.jpeg_quality,
            page_size=args.page_size,
            margin_mm=args.margin_mm,
            placement=args.placement,
        )
        print(args.output.resolve())
        return

    if args.command == "id-halves-to-pdf":
        id_halves_to_pdf(
            args.top_image,
            args.bottom_image,
            args.output,
            fallback_dpi=args.fallback_dpi,
            jpeg_quality=args.jpeg_quality,
        )
        print(args.output.resolve())
        return

    if args.command == "scan-analyze":
        analysis = analyze_scan_pdf(args.input, args.preview_dir)
        print(json.dumps(analysis.to_json(), indent=2))
        return

    if args.command == "scan-cleanup":
        analysis = analyze_scan_pdf(args.input, args.preview_dir)
        clean_scanned_pdf(
            args.input,
            args.output,
            analysis=analysis,
            default_settings=CleanupSettings(
                strength=args.strength,
                white_point=args.white_point,
                contrast=args.contrast,
            ),
        )
        print(args.output.resolve())


if __name__ == "__main__":
    main()
