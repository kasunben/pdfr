# pdfr

A CLI tool that recursively scans a directory of PDFs and redacts PII:
emails, phone numbers, SSNs, credit card numbers, IBANs, BICs, QR
codes, barcodes, and any custom text pattern you define.

Redaction is real — it draws a black box over the match **and** strips
the underlying text, glyphs, or image data, so nothing is recoverable
by copy-paste, text extraction, or re-scanning a code.

## Install

```bash
curl -fsSL https://raw.githubusercontent.com/kasunben/pdfr/main/install.sh | bash
```

This installs to `~/.pdfr` (isolated venv, won't touch your system
Python packages) and puts a `pdfr` launcher on `~/.local/bin`. If
that's not already on your `PATH`, the installer tells you the line to
add to your shell profile.

QR code / barcode detection needs the `zbar` system library. The
installer tries to install it automatically (`apt` or `brew`); if that
fails, everything else still works — QR/barcode scanning is skipped
with a warning until `zbar` is installed manually.

## Usage

Redact every PDF under a directory, in place, keeping the same folder
structure:

```bash
pdfr -r ./docs
```

Instead, write redacted copies into a separate output directory
(mirroring the input structure), leaving the originals untouched:

```bash
pdfr -r ./docs -o ./docs_redacted
```

Preview what would be redacted without writing anything:

```bash
pdfr -r ./docs --dry-run
```

Limit which built-in types are targeted:

```bash
pdfr -r ./docs --types email,ssn
```

Redact your own custom patterns, in addition to the built-ins:

```bash
pdfr -r ./docs --custom-term "Acme Corp" --custom-regex "PROJ-\d{4}"
```

Skip QR/barcode scanning (faster, if you know you don't need it):

```bash
pdfr -r ./docs --no-codes
```

Interpret undialed-code phone numbers as a specific country (default
`US`) — numbers written with an explicit country code, e.g. `+44 20
7946 0958`, are detected regardless of this setting:

```bash
pdfr -r ./docs --phone-region GB
```

## What it catches

| Type          | Method                                                             |
|---------------|-----------------------------------------------------------------------|
| Email         | Standard email pattern                                                |
| Phone         | International: parsed/validated via Google's libphonenumber (`phonenumbers` package); falls back to a US-only pattern if that package isn't installed |
| SSN           | Excludes known-invalid ranges                                          |
| Credit card   | 13–16 digit sequences, Luhn-checksum validated                         |
| IBAN          | Structural pattern, ISO 7064 mod-97 checksum validated                 |
| BIC / SWIFT   | Structural pattern (8 or 11 chars) — see limitation below              |
| QR code       | Any QR code on the page, regardless of encoded content                |
| Barcode       | Any 1D barcode on the page, regardless of encoded content              |
| Custom regex  | Any pattern you supply via `--custom-regex` (repeatable)               |
| Custom term   | Any literal text you supply via `--custom-term`, case-insensitive (repeatable) |

**Coming later (not implemented yet):** links/URLs, timestamps, dates
in various formats.

## Known limitations

- Text-layer only — scanned/image-only PDFs currently produce zero
  text-based matches with no warning. QR/barcode detection still works
  on scanned pages since it operates on the rendered image, not the
  text layer.
- Phone detection needs the `phonenumbers` package (installed by
  `install.sh` automatically) for international format support; without
  it, only common US formats are matched, with a warning printed at
  startup. Numbers written in national format (no country code) are
  interpreted using `--phone-region` (default `US`) — set this to match
  the country your documents are from if it isn't the US.
- **BIC has no checksum**, unlike IBAN or credit cards — there's no
  public algorithm to validate a BIC's structure the way Luhn or
  mod-97 do. This means any 8 or 11-character uppercase
  letter/digit string can false-positive (e.g. `ABCDEFGH`). If this
  matters for your documents, use `--types` to exclude `bic` and rely
  on `--custom-regex`/`--custom-term` for known bank codes instead.
- No detection of unstructured PII (names, addresses) yet.
- In-place mode (`pdfr -r ./docs` without `-o`) **overwrites originals
  permanently.** Use `-o` or `--dry-run` first if you want to keep the
  source files.
- No support for encrypted/password-protected PDFs.

## Uninstall

```bash
rm -rf ~/.pdfr ~/.local/bin/pdfr
```
