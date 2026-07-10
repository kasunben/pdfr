#!/usr/bin/env python3
"""
pdfr - PII redaction toolkit for PDFs.

Recursively scans a directory tree for PDFs, detects PII, and redacts
it — both visually (black box) and structurally (underlying text,
glyphs, or image data removed, not just covered).

Detected out of the box:
    - Email addresses
    - Phone numbers
    - SSNs
    - Credit card numbers (Luhn-validated)
    - IBANs (checksum-validated)
    - BICs / SWIFT codes
    - QR codes and barcodes (any encoded payload, image-based)
    - Custom text patterns (regex or literal terms you supply)

Usage:
    pdfr -r ./docs                       # redact in place, recursively
    pdfr -r ./docs -o ./redacted          # write redacted copies to a
                                           # mirrored directory tree,
                                           # originals untouched
    pdfr -r ./docs --dry-run              # report matches, write nothing
    pdfr -r ./docs --types email,ssn      # limit to specific PII types
    pdfr -r ./docs --custom-term "Acme Corp"
    pdfr -r ./docs --custom-regex "PROJ-\\d{4}"
    pdfr -r ./docs --no-codes             # skip QR/barcode scanning (faster)
    pdfr -r ./docs --phone-region GB      # interpret undialed-code numbers
                                           # as GB numbers (default: US)

Phone number detection uses Google's libphonenumber (via the
'phonenumbers' package) when installed, so numbers with an explicit
country code (e.g. "+44 20 7946 0958") are recognized regardless of
--phone-region, and numbers written in national format are interpreted
using --phone-region as the assumed country. Without 'phonenumbers'
installed, phone detection falls back to a US-only pattern.
"""

import argparse
import re
import sys
from pathlib import Path

try:
    import fitz  # PyMuPDF
except ImportError:
    sys.exit(
        "Error: PyMuPDF is not installed in this environment.\n"
        "If you installed pdfr via install.sh, this shouldn't happen — "
        "try re-running the installer.\n"
        "Otherwise: pip install pymupdf"
    )

try:
    from PIL import Image
    from pyzbar.pyzbar import decode as zbar_decode
    CODES_AVAILABLE = True
except (ImportError, OSError):
    CODES_AVAILABLE = False

try:
    import phonenumbers
    PHONE_LIB_AVAILABLE = True
except ImportError:
    PHONE_LIB_AVAILABLE = False

VERSION = "0.1.0"

# ---------------------------------------------------------------------------
# Built-in text PII patterns
# ---------------------------------------------------------------------------

PATTERNS = {
    "email": re.compile(
        r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}"
    ),
    "ssn": re.compile(
        r"\b(?!000|666|9\d{2})\d{3}-(?!00)\d{2}-(?!0000)\d{4}\b"
    ),
    "credit_card": re.compile(
        r"\b(?:\d[ -]?){13,16}\b"
    ),
    # US-format-biased fallback, only used when the 'phonenumbers' package
    # (which detects real international formats) isn't installed.
    "phone": re.compile(
        r"(?<!\d)(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]\d{3}[-.\s]\d{4}(?!\d)"
    ),
    "iban": re.compile(
        r"\b[A-Z]{2}\d{2}(?:[ ]?[A-Z0-9]{4}){2,7}(?:[ ]?[A-Z0-9]{1,4})?\b"
    ),
    "bic": re.compile(
        r"\b[A-Z]{4}[A-Z]{2}[A-Z0-9]{2}(?:[A-Z0-9]{3})?\b"
    ),
}

# Types resolved from the on-page text layer via regex.
TEXT_TYPES = set(PATTERNS)

# Types resolved by rendering the page to an image and scanning for
# encoded visual data (no text layer involved).
CODE_TYPES = {"qrcode", "barcode"}

ALL_TYPES = TEXT_TYPES | CODE_TYPES


def _luhn_ok(digits: str) -> bool:
    """Luhn checksum — cuts down credit-card false positives on long
    invoice/account numbers that happen to match the digit pattern."""
    nums = [int(d) for d in digits]
    checksum = 0
    parity = len(nums) % 2
    for i, d in enumerate(nums):
        if i % 2 == parity:
            d *= 2
            if d > 9:
                d -= 9
        checksum += d
    return checksum % 10 == 0


def _cc_valid(value: str) -> bool:
    digits = re.sub(r"[ -]", "", value)
    return len(digits) in (13, 14, 15, 16) and _luhn_ok(digits)


def _iban_ok(value: str) -> bool:
    """ISO 7064 mod-97-10 checksum used by all real IBANs. Filters out
    the many short uppercase-alphanumeric strings that would otherwise
    match the structural pattern."""
    v = re.sub(r"\s", "", value).upper()
    if not re.match(r"^[A-Z]{2}\d{2}[A-Z0-9]{10,30}$", v):
        return False
    rearranged = v[4:] + v[:4]
    try:
        numeric = "".join(str(int(c, 36)) for c in rearranged)
    except ValueError:
        return False
    return int(numeric) % 97 == 1


VALIDATORS = {
    "credit_card": _cc_valid,
    "iban": _iban_ok,
}


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------

def _find_phone_matches(text: str, region: str):
    """Yield (start, end, 'phone', raw_string) using libphonenumber, which
    recognizes real national and international formats for any region —
    not just the US. `region` is only used to interpret numbers written
    without an explicit country code; numbers with one (e.g. "+44 ...")
    are matched regardless of it."""
    for m in phonenumbers.PhoneNumberMatcher(text, region):
        yield m.start, m.end, "phone", m.raw_string


def find_text_matches(text: str, types, custom_patterns, phone_region="US"):
    """Yield (start, end, label, matched_text) for each hit in a page's text."""
    for label in types:
        if label == "phone" and PHONE_LIB_AVAILABLE:
            yield from _find_phone_matches(text, phone_region)
            continue
        if label not in PATTERNS:
            continue
        for m in PATTERNS[label].finditer(text):
            value = m.group()
            validator = VALIDATORS.get(label)
            if validator and not validator(value):
                continue
            yield m.start(), m.end(), label, value

    for label, pattern in custom_patterns:
        for m in pattern.finditer(text):
            yield m.start(), m.end(), label, m.group()


def find_code_matches(page, zoom=3.0):
    """Render a page to an image and decode any QR codes / barcodes.
    Returns a list of (fitz.Rect in PDF coordinates, label, decoded_value).
    """
    if not CODES_AVAILABLE:
        return []

    pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom))
    img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    results = []
    for code in zbar_decode(img):
        left, top, width, height = code.rect
        rect = fitz.Rect(
            left / zoom,
            top / zoom,
            (left + width) / zoom,
            (top + height) / zoom,
        )
        label = "qrcode" if code.type == "QRCODE" else "barcode"
        try:
            value = code.data.decode("utf-8", errors="replace")
        except Exception:
            value = "<binary>"
        results.append((rect, label, value))
    return results


# ---------------------------------------------------------------------------
# Redaction
# ---------------------------------------------------------------------------

def redact_pdf(in_path: Path, out_path: Path, types, custom_patterns, dry_run=False, phone_region="US"):
    """Redact PII in a single PDF. Returns (hit_count, hit_log)."""
    doc = fitz.open(in_path)
    total_hits = 0
    hit_log = []

    scan_codes = bool(CODE_TYPES & set(types)) and CODES_AVAILABLE

    for page in doc:
        text = page.get_text()
        text_matches = list(find_text_matches(text, types, custom_patterns, phone_region))

        # De-dupe by (label, value): search_for() locates *every* occurrence
        # of a value on the page in one call, so searching once per distinct
        # value (rather than once per regex match) avoids re-finding and
        # re-counting the same occurrences when a value repeats on a page.
        seen_values = dict.fromkeys((label, value) for _, _, label, value in text_matches)

        for label, value in seen_values:
            rects = page.search_for(value)
            if not rects:
                print(
                    f"Warning: p.{page.number + 1}: '{label}' match "
                    f"{_mask(value)} found in text but could not be located "
                    "on the page for redaction — skipped.",
                    file=sys.stderr,
                )
                continue
            for rect in rects:
                total_hits += 1
                hit_log.append((page.number + 1, label, value))
                if not dry_run:
                    page.add_redact_annot(rect, fill=(0, 0, 0))

        if scan_codes:
            for rect, label, value in find_code_matches(page):
                total_hits += 1
                hit_log.append((page.number + 1, label, value))
                if not dry_run:
                    page.add_redact_annot(rect, fill=(0, 0, 0))

        if not dry_run:
            page.apply_redactions()

    if not dry_run and total_hits > 0:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        if out_path == in_path:
            # Save to a temp path first — PyMuPDF can't safely overwrite
            # a file it currently has open.
            tmp_path = out_path.with_suffix(out_path.suffix + ".pdfr_tmp")
            doc.save(tmp_path, garbage=4, deflate=True)
            doc.close()
            tmp_path.replace(out_path)
            return total_hits, hit_log
        doc.save(out_path, garbage=4, deflate=True)

    doc.close()
    return total_hits, hit_log


def find_pdfs(root: Path):
    """Recursively find all PDFs under root, sorted for stable output."""
    return sorted(root.rglob("*.pdf"))


def _mask(value: str) -> str:
    if len(value) <= 4:
        return "*" * len(value)
    return value[:2] + "*" * (len(value) - 4) + value[-2:]


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser():
    parser = argparse.ArgumentParser(
        prog="pdfr",
        description="Recursively redact PII from PDFs.",
    )
    parser.add_argument("--version", action="version", version=f"pdfr {VERSION}")

    parser.add_argument(
        "-r", "--redact",
        metavar="DIR",
        required=True,
        help="Directory to scan recursively for PDFs",
    )
    parser.add_argument(
        "-o", "--out",
        metavar="DIR",
        default=None,
        help="Output directory. If given, redacted copies are written here, "
             "mirroring the input directory structure, and originals are "
             "left untouched. If omitted, files are redacted IN PLACE.",
    )
    parser.add_argument(
        "--types",
        default=",".join(sorted(ALL_TYPES)),
        help=f"Comma-separated PII types to redact (available: {', '.join(sorted(ALL_TYPES))})",
    )
    parser.add_argument(
        "--custom-regex",
        action="append",
        default=[],
        metavar="PATTERN",
        help="Custom regex pattern to redact, in addition to built-in types. "
             "Repeatable.",
    )
    parser.add_argument(
        "--custom-term",
        action="append",
        default=[],
        metavar="TEXT",
        help="Exact (case-insensitive) text to redact wherever it appears, "
             "in addition to built-in types. Repeatable.",
    )
    parser.add_argument(
        "--phone-region",
        default="US",
        metavar="REGION",
        help="ISO 3166-1 alpha-2 region code (e.g. US, GB, DE) used to "
             "interpret phone numbers written without a country code. "
             "Numbers with an explicit country code (e.g. +44 20 7946 0958) "
             "are detected regardless of this setting. Requires the "
             "'phonenumbers' package; ignored (falls back to a US-only "
             "pattern) if it isn't installed. Default: US",
    )
    parser.add_argument(
        "--no-codes",
        action="store_true",
        help="Skip QR code / barcode scanning (faster; equivalent to "
             "excluding qrcode,barcode from --types)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would be redacted without writing any files",
    )
    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)

    in_dir = Path(args.redact).resolve()
    if not in_dir.is_dir():
        sys.exit(f"Error: {in_dir} is not a directory")

    types = [t.strip() for t in args.types.split(",") if t.strip()]
    for t in types:
        if t not in ALL_TYPES:
            sys.exit(f"Error: unknown PII type '{t}'. Available: {', '.join(sorted(ALL_TYPES))}")

    if args.no_codes:
        types = [t for t in types if t not in CODE_TYPES]

    if (CODE_TYPES & set(types)) and not CODES_AVAILABLE:
        print(
            "Warning: QR/barcode scanning requested but pyzbar/zbar isn't "
            "available in this environment — skipping code detection.\n",
            file=sys.stderr,
        )
        types = [t for t in types if t not in CODE_TYPES]

    if "phone" in types:
        if not PHONE_LIB_AVAILABLE:
            print(
                "Warning: the 'phonenumbers' package isn't installed — "
                "phone detection will fall back to a US-only pattern. "
                "Install it for international phone number support: "
                "pip install phonenumbers\n",
                file=sys.stderr,
            )
        elif args.phone_region.upper() not in phonenumbers.SUPPORTED_REGIONS:
            sys.exit(
                f"Error: unknown --phone-region '{args.phone_region}'. "
                "Expected an ISO 3166-1 alpha-2 code, e.g. US, GB, DE."
            )

    custom_patterns = []
    for i, pattern_str in enumerate(args.custom_regex):
        try:
            custom_patterns.append((f"custom_regex_{i+1}", re.compile(pattern_str)))
        except re.error as e:
            sys.exit(f"Error: invalid --custom-regex '{pattern_str}': {e}")
    for i, term in enumerate(args.custom_term):
        custom_patterns.append(
            (f"custom_term_{i+1}", re.compile(re.escape(term), re.IGNORECASE))
        )

    out_dir = Path(args.out).resolve() if args.out else None
    in_place = out_dir is None

    pdfs = find_pdfs(in_dir)
    if not pdfs:
        sys.exit(f"No PDF files found under {in_dir}")

    if in_place and not args.dry_run:
        print(f"Redacting {len(pdfs)} file(s) IN PLACE under {in_dir}")
        print("Originals will be overwritten. Ctrl+C now to abort.\n")

    grand_total = 0
    for pdf_path in pdfs:
        rel = pdf_path.relative_to(in_dir)
        dest = (out_dir / rel) if out_dir else pdf_path

        hits, log = redact_pdf(
            pdf_path, dest, types, custom_patterns,
            dry_run=args.dry_run, phone_region=args.phone_region,
        )
        grand_total += hits

        if hits == 0:
            print(f"[ok]     {rel}: no PII found")
            continue

        verb = "would redact" if args.dry_run else "redacted"
        print(f"[redact] {rel}: {verb} {hits} item(s)")
        for page_num, label, value in log:
            print(f"           p.{page_num:<3} {label:<16} {_mask(value)}")

    print(f"\nDone. {grand_total} item(s) across {len(pdfs)} file(s).")
    if args.dry_run:
        print("Dry run only, no files were written.")
    elif out_dir:
        print(f"Redacted copies written to: {out_dir}")
    else:
        print(f"Files redacted in place under: {in_dir}")


if __name__ == "__main__":
    main()
