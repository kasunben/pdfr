import re

import fitz
import pytest

import pdfr


# ---------------------------------------------------------------------------
# Credit card (Luhn) validation
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("number", [
    "4111111111111111",  # Visa test number
    "5500005555555559",  # Mastercard test number
    "340000000000009",   # Amex test number (15 digits)
])
def test_cc_valid_accepts_known_test_numbers(number):
    assert pdfr._cc_valid(number)


@pytest.mark.parametrize("number", [
    "4111111111111112",  # Visa test number with corrupted checksum digit
    "1234567890123",     # 13 digits, not Luhn-valid
])
def test_cc_valid_rejects_bad_checksum(number):
    assert not pdfr._cc_valid(number)


def test_cc_valid_rejects_wrong_length():
    assert not pdfr._cc_valid("411111111111")  # 12 digits, too short


def test_cc_valid_strips_separators():
    assert pdfr._cc_valid("4111-1111-1111-1111")
    assert pdfr._cc_valid("4111 1111 1111 1111")


# ---------------------------------------------------------------------------
# IBAN (mod-97) validation
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("iban", [
    "GB82 WEST 1234 5698 7654 32",
    "DE89370400440532013000",
    "FR1420041010050500013M02606",
])
def test_iban_ok_accepts_valid_ibans(iban):
    assert pdfr._iban_ok(iban)


def test_iban_ok_rejects_bad_checksum():
    # Same as a valid IBAN above, with the final check digit corrupted.
    assert not pdfr._iban_ok("DE89370400440532013009")


def test_iban_ok_rejects_malformed_input():
    assert not pdfr._iban_ok("not-an-iban")
    assert not pdfr._iban_ok("GB82WEST12345698765")  # too short


# ---------------------------------------------------------------------------
# Built-in regex patterns
# ---------------------------------------------------------------------------

def test_email_pattern_matches():
    assert pdfr.PATTERNS["email"].search("contact alice@example.com today")


def test_email_pattern_no_match_on_plain_text():
    assert pdfr.PATTERNS["email"].search("no email here") is None


def test_ssn_pattern_excludes_known_invalid_ranges():
    assert pdfr.PATTERNS["ssn"].search("SSN: 219-09-9999")
    assert pdfr.PATTERNS["ssn"].search("000-12-3456") is None  # invalid area
    assert pdfr.PATTERNS["ssn"].search("123-00-4567") is None  # invalid group
    assert pdfr.PATTERNS["ssn"].search("123-45-0000") is None  # invalid serial


def test_phone_pattern_matches_common_us_formats():
    assert pdfr.PATTERNS["phone"].search("call 555-123-4567")
    assert pdfr.PATTERNS["phone"].search("(555) 123-4567")


def test_bic_pattern_matches_8_and_11_char_codes():
    assert pdfr.PATTERNS["bic"].fullmatch("DEUTDEFF")
    assert pdfr.PATTERNS["bic"].fullmatch("DEUTDEFF500")


# ---------------------------------------------------------------------------
# International phone detection (phonenumbers)
# ---------------------------------------------------------------------------

requires_phonenumbers = pytest.mark.skipif(
    not pdfr.PHONE_LIB_AVAILABLE, reason="phonenumbers package not installed"
)


@requires_phonenumbers
def test_find_text_matches_detects_international_number_with_country_code():
    text = "Call us at +44 20 7946 0958 for support"
    matches = list(pdfr.find_text_matches(text, ["phone"], [], phone_region="US"))
    assert [(label, value) for _, _, label, value in matches] == [
        ("phone", "+44 20 7946 0958")
    ]


@requires_phonenumbers
def test_find_text_matches_uses_phone_region_for_national_format():
    text = "Reception: 020 7946 0958"

    us_matches = list(pdfr.find_text_matches(text, ["phone"], [], phone_region="US"))
    assert us_matches == []

    gb_matches = list(pdfr.find_text_matches(text, ["phone"], [], phone_region="GB"))
    assert [(label, value) for _, _, label, value in gb_matches] == [
        ("phone", "020 7946 0958")
    ]


@requires_phonenumbers
def test_find_text_matches_phone_still_matches_us_national_format():
    text = "Call 415-867-5309 for details"
    matches = list(pdfr.find_text_matches(text, ["phone"], [], phone_region="US"))
    assert [(label, value) for _, _, label, value in matches] == [
        ("phone", "415-867-5309")
    ]


# ---------------------------------------------------------------------------
# _mask
# ---------------------------------------------------------------------------

def test_mask_short_value_fully_masked():
    assert pdfr._mask("1234") == "****"


def test_mask_long_value_keeps_first_and_last_two_chars():
    assert pdfr._mask("alice@example.com") == "al*************om"


# ---------------------------------------------------------------------------
# find_text_matches
# ---------------------------------------------------------------------------

def test_find_text_matches_respects_types_filter():
    text = "email me at alice@example.com or call 555-123-4567"
    matches = list(pdfr.find_text_matches(text, ["email"], []))
    labels = {label for _, _, label, _ in matches}
    assert labels == {"email"}


def test_find_text_matches_applies_custom_patterns():
    text = "Project code PROJ-1234 is confidential"
    custom = [("custom_regex_1", re.compile(r"PROJ-\d{4}"))]
    matches = list(pdfr.find_text_matches(text, [], custom))
    assert matches == [(13, 22, "custom_regex_1", "PROJ-1234")]


def test_find_text_matches_filters_invalid_credit_card_by_luhn():
    text = "invoice number 1234567890123456"  # 16 digits, fails Luhn
    matches = list(pdfr.find_text_matches(text, ["credit_card"], []))
    assert matches == []


# ---------------------------------------------------------------------------
# find_pdfs
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# _read_pattern_file
# ---------------------------------------------------------------------------

def test_read_pattern_file_skips_blank_lines_and_comments(tmp_path):
    path = tmp_path / "terms.txt"
    path.write_text(
        "Acme Corp\n"
        "\n"
        "# a comment\n"
        "  Jane Doe  \n"
        "   \n"
        "PROJ-4821\n"
    )

    assert list(pdfr._read_pattern_file(path)) == ["Acme Corp", "Jane Doe", "PROJ-4821"]


def test_find_pdfs_recurses_and_sorts(tmp_path):
    (tmp_path / "b").mkdir()
    (tmp_path / "a").mkdir()
    (tmp_path / "b" / "2.pdf").write_bytes(b"")
    (tmp_path / "a" / "1.pdf").write_bytes(b"")
    (tmp_path / "notes.txt").write_bytes(b"")

    found = pdfr.find_pdfs(tmp_path)
    assert found == sorted(found)
    assert {p.name for p in found} == {"1.pdf", "2.pdf"}


# ---------------------------------------------------------------------------
# redact_pdf — integration tests against real PDFs
# ---------------------------------------------------------------------------

def _make_pdf(path, lines):
    doc = fitz.open()
    page = doc.new_page()
    y = 72
    for line in lines:
        page.insert_text((72, y), line)
        y += 20
    doc.save(path)
    doc.close()


def test_redact_pdf_dry_run_reports_hits_without_writing(tmp_path):
    src = tmp_path / "in.pdf"
    _make_pdf(src, ["Contact: alice@example.com", "SSN: 219-09-9999"])
    mtime_before = src.stat().st_mtime_ns

    hits, log = pdfr.redact_pdf(src, src, ["email", "ssn"], [], dry_run=True)

    assert hits == 2
    assert {label for _, label, _ in log} == {"email", "ssn"}
    assert src.stat().st_mtime_ns == mtime_before


def test_redact_pdf_removes_text_and_purges_raw_bytes(tmp_path):
    src = tmp_path / "in.pdf"
    _make_pdf(src, ["Contact: alice@example.com", "SSN: 219-09-9999"])

    hits, _ = pdfr.redact_pdf(src, src, ["email", "ssn"], [], dry_run=False)
    assert hits == 2

    doc = fitz.open(src)
    extracted = doc[0].get_text()
    doc.close()
    assert "alice@example.com" not in extracted
    assert "219-09-9999" not in extracted

    # The redacted values must not survive as unreferenced objects in the
    # raw file bytes either — a real redaction, not just a hidden reference.
    raw = src.read_bytes()
    assert b"alice@example.com" not in raw
    assert b"219-09-9999" not in raw


@requires_phonenumbers
def test_redact_pdf_redacts_international_phone_number(tmp_path):
    src = tmp_path / "in.pdf"
    _make_pdf(src, ["Reception: 020 7946 0958"])

    hits, log = pdfr.redact_pdf(src, src, ["phone"], [], dry_run=True, phone_region="GB")

    assert hits == 1
    assert log == [(1, "phone", "020 7946 0958")]


def test_redact_pdf_counts_repeated_value_once_per_occurrence(tmp_path):
    # Regression test: search_for(value) locates *every* occurrence of a
    # value on the page in one call, so a value appearing twice must be
    # counted twice — not once per (redundant) regex match times matches
    # found, which previously inflated the count quadratically.
    src = tmp_path / "in.pdf"
    _make_pdf(src, [
        "Contact: alice@example.com",
        "Contact again: alice@example.com",
    ])

    hits, log = pdfr.redact_pdf(src, src, ["email"], [], dry_run=True)

    assert hits == 2
    assert len(log) == 2


def test_redact_pdf_out_dir_leaves_original_untouched(tmp_path):
    src_dir = tmp_path / "docs"
    src_dir.mkdir()
    out_dir = tmp_path / "redacted"
    src = src_dir / "in.pdf"
    _make_pdf(src, ["Contact: alice@example.com"])
    original_bytes = src.read_bytes()

    out_path = out_dir / "in.pdf"
    hits, _ = pdfr.redact_pdf(src, out_path, ["email"], [], dry_run=False)

    assert hits == 1
    assert src.read_bytes() == original_bytes
    assert out_path.exists()

    doc = fitz.open(out_path)
    assert "alice@example.com" not in doc[0].get_text()
    doc.close()


def test_redact_pdf_no_hits_writes_nothing(tmp_path):
    out_dir = tmp_path / "redacted"
    src = tmp_path / "in.pdf"
    _make_pdf(src, ["Nothing sensitive here."])

    hits, log = pdfr.redact_pdf(src, out_dir / "in.pdf", ["email", "ssn"], [], dry_run=False)

    assert hits == 0
    assert log == []
    assert not (out_dir / "in.pdf").exists()


def test_redact_pdf_custom_term_and_regex(tmp_path):
    src = tmp_path / "in.pdf"
    _make_pdf(src, ["Client: Acme Corp", "Ref: PROJ-4821"])
    custom = [
        ("custom_term_1", re.compile(re.escape("Acme Corp"), re.IGNORECASE)),
        ("custom_regex_1", re.compile(r"PROJ-\d{4}")),
    ]

    hits, log = pdfr.redact_pdf(src, src, [], custom, dry_run=True)

    assert hits == 2
    assert {label for _, label, _ in log} == {"custom_term_1", "custom_regex_1"}


def test_redact_pdf_warns_when_match_cannot_be_located(tmp_path, monkeypatch, capsys):
    # Regression test: if a text-layer match can't be relocated via
    # search_for() (e.g. due to text-extraction differences), it must be
    # surfaced as a warning rather than silently dropped.
    src = tmp_path / "in.pdf"
    _make_pdf(src, ["Contact: alice@example.com"])

    real_search_for = fitz.Page.search_for
    monkeypatch.setattr(fitz.Page, "search_for", lambda self, value, **kw: [])

    hits, log = pdfr.redact_pdf(src, src, ["email"], [], dry_run=True)

    assert hits == 0
    assert log == []
    err = capsys.readouterr().err
    assert "could not be located" in err


# ---------------------------------------------------------------------------
# CLI (main)
# ---------------------------------------------------------------------------

def test_main_errors_on_unknown_type(tmp_path, capsys):
    (tmp_path / "in.pdf").write_bytes(b"")
    with pytest.raises(SystemExit) as exc:
        pdfr.main(["-r", str(tmp_path), "--types", "bogus"])
    assert "unknown PII type" in str(exc.value)


def test_main_errors_when_target_is_not_a_directory(tmp_path):
    not_a_dir = tmp_path / "missing"
    with pytest.raises(SystemExit) as exc:
        pdfr.main(["-r", str(not_a_dir)])
    assert "not a directory" in str(exc.value)


def test_main_errors_when_no_pdfs_found(tmp_path):
    with pytest.raises(SystemExit) as exc:
        pdfr.main(["-r", str(tmp_path)])
    assert "No PDF files found" in str(exc.value)


def test_main_dry_run_end_to_end(tmp_path, capsys):
    _make_pdf(tmp_path / "in.pdf", ["Contact: alice@example.com"])

    pdfr.main(["-r", str(tmp_path), "--dry-run", "--types", "email"])

    out = capsys.readouterr().out
    assert "would redact 1 item(s)" in out
    assert "Dry run only" in out


def test_main_redacts_terms_from_file(tmp_path, capsys):
    _make_pdf(tmp_path / "in.pdf", ["Client: Acme Corp", "Owner: Jane Doe"])
    terms_file = tmp_path / "terms.txt"
    terms_file.write_text("Acme Corp\n# a comment\nJane Doe\n")

    pdfr.main([
        "-r", str(tmp_path), "--dry-run", "--types", "",
        "--custom-term-file", str(terms_file),
    ])

    out = capsys.readouterr().out
    assert "would redact 2 item(s)" in out


def test_main_combines_cli_and_file_custom_patterns(tmp_path, capsys):
    _make_pdf(tmp_path / "in.pdf", ["Client: Acme Corp", "Ref: PROJ-4821"])
    regex_file = tmp_path / "patterns.txt"
    regex_file.write_text(r"PROJ-\d{4}" + "\n")

    pdfr.main([
        "-r", str(tmp_path), "--dry-run", "--types", "",
        "--custom-term", "Acme Corp",
        "--custom-regex-file", str(regex_file),
    ])

    out = capsys.readouterr().out
    assert "would redact 2 item(s)" in out


def test_main_errors_when_term_file_missing(tmp_path):
    (tmp_path / "in.pdf").write_bytes(b"")
    with pytest.raises(SystemExit) as exc:
        pdfr.main([
            "-r", str(tmp_path), "--types", "",
            "--custom-term-file", str(tmp_path / "missing.txt"),
        ])
    assert "is not a file" in str(exc.value)


@requires_phonenumbers
def test_main_errors_on_unknown_phone_region(tmp_path):
    (tmp_path / "in.pdf").write_bytes(b"")
    with pytest.raises(SystemExit) as exc:
        pdfr.main(["-r", str(tmp_path), "--types", "phone", "--phone-region", "ZZ"])
    assert "unknown --phone-region" in str(exc.value)


@requires_phonenumbers
def test_main_dry_run_detects_international_phone_with_region(tmp_path, capsys):
    _make_pdf(tmp_path / "in.pdf", ["Reception: 020 7946 0958"])

    pdfr.main([
        "-r", str(tmp_path), "--dry-run", "--types", "phone",
        "--phone-region", "GB",
    ])

    out = capsys.readouterr().out
    assert "would redact 1 item(s)" in out
