"""
cli.py
=======
Command-line interface for the Multi-Source Candidate Data Transformer.

Supports:
  - Single file       : python candidate_pipeline.py resume.pdf
  - Multiple files    : python candidate_pipeline.py r1.pdf r2.pdf r3.docx
  - Whole directory   : python candidate_pipeline.py --dir ./resumes/
  - Parallel workers  : python candidate_pipeline.py --dir ./resumes/ --workers 8
  - Quiet JSON output : python candidate_pipeline.py resume.pdf --quiet
  - Save to file      : python candidate_pipeline.py --dir ./resumes/ --out results.json
  - Schema selection  : python candidate_pipeline.py resume.pdf --schema ats
"""

import argparse
import json
import logging
import sys
from pathlib import Path

from pipeline import Pipeline

# Supported file extensions the pipeline can handle
SUPPORTED_EXTENSIONS = {".pdf", ".docx", ".txt", ".csv", ".json", ".png", ".jpg", ".jpeg", ".rtf", ".html"}


def _collect_files(inputs: list[str], dir_path: str | None) -> list[Path]:
    """
    Collect all files to process from:
      - Positional file arguments (inputs)
      - A directory scan (--dir flag)

    Returns a deduplicated, sorted list of Path objects.
    """
    files: list[Path] = []

    # From positional args
    for inp in (inputs or []):
        p = Path(inp)
        if not p.exists():
            print(f"[WARNING] File not found, skipping: {p}", file=sys.stderr)
            continue
        if p.suffix.lower() not in SUPPORTED_EXTENSIONS:
            print(f"[WARNING] Unsupported file type, skipping: {p}", file=sys.stderr)
            continue
        files.append(p.resolve())

    # From --dir flag
    if dir_path:
        d = Path(dir_path)
        if not d.is_dir():
            print(f"[ERROR] Directory not found: {d}", file=sys.stderr)
            sys.exit(1)
        for f in sorted(d.rglob("*")):
            if f.is_file() and f.suffix.lower() in SUPPORTED_EXTENSIONS:
                files.append(f.resolve())

    # Deduplicate while preserving order
    seen = set()
    unique = []
    for f in files:
        if f not in seen:
            seen.add(f)
            unique.append(f)

    return unique


def main():
    parser = argparse.ArgumentParser(
        description="Multi-Source Candidate Data Transformer",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Single resume
  python candidate_pipeline.py resume.pdf

  # Multiple resumes at once (parallel)
  python candidate_pipeline.py resume1.pdf resume2.docx resume3.pdf

  # Entire folder of resumes
  python candidate_pipeline.py --dir ./resumes/

  # Folder with 8 parallel workers, quiet JSON output
  python candidate_pipeline.py --dir ./resumes/ --workers 8 --quiet

  # Save output to a JSON file
  python candidate_pipeline.py --dir ./resumes/ --out results.json

  # Different output schemas: canonical (default), full, ats, linkedin, minimal
  python candidate_pipeline.py resume.pdf --schema ats
"""
    )

    parser.add_argument(
        "files", nargs="*",
        help="One or more candidate files (PDF, DOCX, CSV, JSON, TXT, PNG, JPG)"
    )
    parser.add_argument(
        "--dir", metavar="DIRECTORY",
        help="Process all supported files in this directory"
    )
    parser.add_argument(
        "--schema", default="canonical",
        help="Output schema: canonical (default), full, ats, linkedin, minimal"
    )
    parser.add_argument(
        "--channel", default="manual",
        help="Upload channel applied to all files (default: manual)"
    )
    parser.add_argument(
        "--workers", type=int, default=4,
        help="Number of parallel threads for batch processing (default: 4)"
    )
    parser.add_argument(
        "--out", metavar="FILE",
        help="Save JSON output to this file instead of printing to stdout"
    )
    parser.add_argument(
        "--no-persist", action="store_true",
        help="Disable database persistence"
    )
    parser.add_argument(
        "--quiet", "-q", action="store_true",
        help="Suppress all logs — stdout is pure JSON only"
    )
    parser.add_argument(
        "--referral-code", default=None,
        help="Employee referral code"
    )
    parser.add_argument(
        "--referred-by", default=None,
        help="Employee who referred the candidate"
    )

    args = parser.parse_args()

    # ── Logging setup ─────────────────────────────────────────────────────────
    # Logs ALWAYS go to stderr so stdout stays pure JSON.
    # --quiet silences everything below CRITICAL (effectively silent).
    log_level = logging.CRITICAL if args.quiet else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        stream=sys.stderr,
    )

    # ── Collect files ─────────────────────────────────────────────────────────
    files = _collect_files(args.files, args.dir)

    if not files:
        print(
            json.dumps({"error": "No valid files found. Pass file paths or use --dir."}),
            file=sys.stderr,
        )
        sys.exit(1)

    # ── Run pipeline ──────────────────────────────────────────────────────────
    pipeline = Pipeline(persist=not args.no_persist)

    if len(files) == 1:
        # Single file — output a single JSON object
        result = pipeline.run(
            file_path=str(files[0]),
            upload_channel=args.channel,
            schema=args.schema,
            referral_code=args.referral_code,
            referred_by=args.referred_by,
        )
        result["_meta"] = {"file": files[0].name, "status": "ok", "error": None}
        output = result

    else:
        # Multiple files — parallel batch, output a JSON array
        if not args.quiet:
            print(
                f"[INFO] Processing {len(files)} files with {min(args.workers, len(files))} workers...",
                file=sys.stderr,
            )
        output = pipeline.run_batch(
            file_paths=[str(f) for f in files],
            upload_channel=args.channel,
            schema=args.schema,
            workers=args.workers,
        )

        # Print summary to stderr
        if not args.quiet:
            ok    = sum(1 for r in output if r["_meta"]["status"] == "ok")
            error = len(output) - ok
            print(
                f"[INFO] Batch done: {ok}/{len(output)} succeeded"
                + (f", {error} failed" if error else ""),
                file=sys.stderr,
            )

    # ── Write output ──────────────────────────────────────────────────────────
    json_str = json.dumps(output, indent=2, ensure_ascii=False)

    if args.out:
        out_path = Path(args.out)
        out_path.write_text(json_str, encoding="utf-8")
        if not args.quiet:
            print(f"[INFO] Output saved to: {out_path.resolve()}", file=sys.stderr)
    else:
        print(json_str)


if __name__ == "__main__":
    main()
