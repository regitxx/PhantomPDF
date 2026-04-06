"""Tests for the CID font decoder module."""

import pytest

from phantompdf.fonts import FontInfo, parse_tounicode_cmap, parse_w_array


class TestToUnicodeCMap:
    """Test CMap parsing."""

    def test_parse_bfrange(self):
        cmap = b"""
        beginbfrange
        <0003><0003><0020>
        <0024><0024><0041>
        endbfrange
        """
        result = parse_tounicode_cmap(cmap)
        assert result[" "] == 3     # space
        assert result["A"] == 0x24  # uppercase A

    def test_parse_bfrange_sequential(self):
        cmap = b"""
        beginbfrange
        <0030><0039><0030>
        endbfrange
        """
        result = parse_tounicode_cmap(cmap)
        # CID 0x30-0x39 maps to Unicode '0'-'9'
        for i in range(10):
            assert result[str(i)] == 0x30 + i

    def test_parse_bfchar(self):
        cmap = b"""
        beginbfchar
        <002C><002C>
        <002E><002E>
        endbfchar
        """
        result = parse_tounicode_cmap(cmap)
        assert result[","] == 0x2C
        assert result["."] == 0x2E

    def test_parse_mixed(self):
        cmap = b"""
        beginbfrange
        <0041><0043><0041>
        endbfrange
        beginbfchar
        <0020><0020>
        endbfchar
        """
        result = parse_tounicode_cmap(cmap)
        assert result["A"] == 0x41
        assert result["B"] == 0x42
        assert result["C"] == 0x43
        assert result[" "] == 0x20

    def test_empty_cmap(self):
        result = parse_tounicode_cmap(b"")
        assert result == {}


class TestWArray:
    """Test CIDFont W array parsing."""

    def test_parse_bracketed(self):
        w = "[305[503 520 520]]"
        result = parse_w_array(w)
        assert result[305] == 503
        assert result[306] == 520
        assert result[307] == 520

    def test_parse_range(self):
        w = "10 20 500"
        result = parse_w_array(w)
        for cid in range(10, 21):
            assert result[cid] == 500

    def test_empty(self):
        result = parse_w_array("")
        assert result == {}


class TestFontInfo:
    """Test FontInfo class."""

    @pytest.fixture
    def sample_font(self):
        return FontInfo(
            name="TestFont",
            ref_name="F1",
            xref=5,
            char_to_cid={"H": 0x48, "e": 0x65, "l": 0x6C, "o": 0x6F, " ": 0x20},
            cid_widths={0x48: 700, 0x65: 500, 0x6C: 250, 0x6F: 550, 0x20: 250},
            default_width=1000,
        )

    def test_has_glyph(self, sample_font):
        assert sample_font.has_glyph("H")
        assert sample_font.has_glyph(" ")
        assert not sample_font.has_glyph("Z")

    def test_missing_glyphs(self, sample_font):
        assert sample_font.missing_glyphs("Hello") == []
        assert sample_font.missing_glyphs("HZ") == ["Z"]
        assert sample_font.missing_glyphs("XYZ") == ["X", "Y", "Z"]

    def test_missing_glyphs_dedup(self, sample_font):
        """Missing glyphs should not have duplicates."""
        assert sample_font.missing_glyphs("ZZZ") == ["Z"]

    def test_encode_text(self, sample_font):
        encoded = sample_font.encode_text("He")
        assert encoded == b"\x00\x48\x00\x65"

    def test_encode_text_missing_char_raises(self, sample_font):
        with pytest.raises(ValueError, match="not in font"):
            sample_font.encode_text("Z")

    def test_decode_cid_bytes(self, sample_font):
        decoded = sample_font.decode_cid_bytes(b"\x00\x48\x00\x65")
        assert decoded == "He"

    def test_char_width(self, sample_font):
        # Width at 10pt: CID width / 1000 * fontsize
        assert sample_font.char_width("H", 10.0) == 7.0
        assert sample_font.char_width(" ", 10.0) == 2.5

    def test_text_width(self, sample_font):
        width = sample_font.text_width("Hello", 10.0)
        expected = (700 + 500 + 250 + 250 + 550) / 1000 * 10.0
        assert width == expected

    def test_char_width_unknown_uses_default(self, sample_font):
        # Force a CID not in widths table
        sample_font.char_to_cid["X"] = 999
        width = sample_font.char_width("X", 10.0)
        assert width == 10.0  # default_width=1000, /1000 * 10 = 10

    def test_encode_decode_roundtrip(self, sample_font):
        text = "Hello"
        encoded = sample_font.encode_text(text)
        decoded = sample_font.decode_cid_bytes(encoded)
        assert decoded == text

    def test_equivalent_chars(self):
        """Test that character equivalents (hyphen variants, space variants) work."""
        font = FontInfo(
            name="TestFont",
            ref_name="F1",
            xref=5,
            char_to_cid={"-": 0x2D, " ": 0x20},
            cid_widths={},
        )
        # Non-breaking space should map to regular space CID
        encoded = font.encode_text("\u00A0")
        assert encoded == b"\x00\x20"
