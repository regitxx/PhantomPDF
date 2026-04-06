"""Tests for the surgical replacement engine."""

import pytest

from phantompdf.engine import (
    _detect_fontsize,
    build_tj_line,
    escape_cid_for_pdf,
    parse_tj_array,
    replace_in_tj_segments,
)
from phantompdf.fonts import FontInfo


@pytest.fixture
def cid_font():
    """A CID font with digits, letters, and common punctuation."""
    char_to_cid = {}
    # Map digits 0-9 to CIDs 48-57
    for i in range(10):
        char_to_cid[str(i)] = 48 + i
    # Map uppercase A-Z
    for i in range(26):
        char_to_cid[chr(65 + i)] = 65 + i
    # Map lowercase a-z
    for i in range(26):
        char_to_cid[chr(97 + i)] = 97 + i
    # Common chars
    char_to_cid[" "] = 32
    char_to_cid[","] = 44
    char_to_cid["."] = 46

    cid_widths = {cid: 500 for cid in char_to_cid.values()}

    return FontInfo(
        name="TestCIDFont",
        ref_name="F1",
        xref=10,
        char_to_cid=char_to_cid,
        cid_widths=cid_widths,
    )


class TestEscapeCID:
    """Test PDF string escape handling."""

    def test_no_special_chars(self):
        assert escape_cid_for_pdf(b"\x00\x48") == b"\x00\x48"

    def test_escape_parens(self):
        assert escape_cid_for_pdf(b"\x28") == b"\\("
        assert escape_cid_for_pdf(b"\x29") == b"\\)"

    def test_escape_backslash(self):
        assert escape_cid_for_pdf(b"\x5c") == b"\\\\"

    def test_mixed(self):
        result = escape_cid_for_pdf(b"\x00\x48\x28\x29")
        assert result == b"\x00\x48\\(\\)"


class TestParseTjArray:
    """Test TJ array parsing."""

    def test_hex_strings(self):
        line = "[<00480065006C006C006F>]TJ"
        segments = parse_tj_array(line)
        assert len(segments) == 1
        assert segments[0]["type"] == "text"
        assert segments[0]["hex"] is True
        assert segments[0]["value"] == bytes.fromhex("00480065006C006C006F")

    def test_paren_strings(self):
        line = "[(Hello)]TJ"
        segments = parse_tj_array(line)
        assert len(segments) == 1
        assert segments[0]["type"] == "text"
        assert segments[0]["hex"] is False

    def test_mixed_text_and_adjustments(self):
        line = "[<0048>-50<0065>]TJ"
        segments = parse_tj_array(line)
        assert len(segments) == 3
        assert segments[0]["type"] == "text"
        assert segments[1]["type"] == "adjust"
        assert segments[1]["value"] == "-50"
        assert segments[2]["type"] == "text"

    def test_escaped_parens(self):
        line = "[(test\\(1\\))]TJ"
        segments = parse_tj_array(line)
        assert len(segments) == 1
        # Unescaped parens should be actual bytes
        assert b"\x28" in segments[0]["value"]  # (
        assert b"\x29" in segments[0]["value"]  # )


class TestBuildTjLine:
    """Test TJ array reconstruction."""

    def test_hex_format(self):
        segments = [{"type": "text", "value": b"\x00\x48", "hex": True}]
        result = build_tj_line(segments)
        assert result == b"[<0048>]TJ"

    def test_paren_format(self):
        segments = [{"type": "text", "value": b"Hi", "hex": False}]
        result = build_tj_line(segments)
        assert result == b"[(Hi)]TJ"

    def test_with_adjustments(self):
        segments = [
            {"type": "text", "value": b"\x00\x48", "hex": True},
            {"type": "adjust", "value": "-50"},
            {"type": "text", "value": b"\x00\x65", "hex": True},
        ]
        result = build_tj_line(segments)
        assert result == b"[<0048>-50<0065>]TJ"


class TestReplaceInTjSegments:
    """Test text replacement within TJ segments."""

    def test_single_segment_replace(self, cid_font):
        # Build a TJ segment containing "100"
        segments = [
            {"type": "text", "value": cid_font.encode_text("100"), "hex": True},
        ]
        new_segs, replaced = replace_in_tj_segments(
            segments, cid_font, "100", "999"
        )
        assert replaced is True

        # Verify the new text
        text_values = [s["value"] for s in new_segs if s["type"] == "text"]
        full_text = b"".join(text_values)
        assert cid_font.decode_cid_bytes(full_text) == "999"

    def test_no_match_returns_false(self, cid_font):
        segments = [
            {"type": "text", "value": cid_font.encode_text("ABC"), "hex": True},
        ]
        new_segs, replaced = replace_in_tj_segments(
            segments, cid_font, "XYZ", "123"
        )
        assert replaced is False

    def test_preserves_prefix_suffix(self, cid_font):
        # "Amount: 100,00 EUR" -> replace "100" with "999"
        segments = [
            {"type": "text", "value": cid_font.encode_text("Amount. 100,00 EUR"), "hex": True},
        ]
        new_segs, replaced = replace_in_tj_segments(
            segments, cid_font, "100", "999"
        )
        assert replaced is True

        text_values = [s["value"] for s in new_segs if s["type"] == "text"]
        full_text = b"".join(text_values)
        decoded = cid_font.decode_cid_bytes(full_text)
        assert "999" in decoded
        assert "Amount" in decoded
        assert "EUR" in decoded


class TestDetectFontsize:
    """Test font size detection from content stream."""

    def test_detect_simple(self):
        stream = "BT\n/F1 12 Tf\n(Hello) Tj\nET"
        assert _detect_fontsize(stream, "F1") == 12.0

    def test_detect_decimal(self):
        stream = "BT\n/F1 10.5 Tf\n(Hello) Tj\nET"
        assert _detect_fontsize(stream, "F1") == 10.5

    def test_fallback_on_missing(self):
        stream = "BT\n/F2 12 Tf\n(Hello) Tj\nET"
        assert _detect_fontsize(stream, "F1") == 10.0

    def test_detect_correct_font(self):
        stream = "BT\n/F1 8 Tf\n(Hello) Tj\n/F2 14 Tf\n(World) Tj\nET"
        assert _detect_fontsize(stream, "F1") == 8.0
        assert _detect_fontsize(stream, "F2") == 14.0
