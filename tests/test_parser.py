"""Tests for the PDF binary parser module."""

import zlib

import pytest

from phantompdf.parser import (
    compress_stream,
    decompress_stream,
    extract_length_from_header,
    find_stream_boundaries,
    parse_xref_table,
    update_length_in_header,
)


class TestCompression:
    """Test FlateDecode compression/decompression."""

    def test_roundtrip(self):
        original = b"Hello world, this is PDF content stream data"
        compressed = compress_stream(original)
        assert compressed != original
        decompressed = zlib.decompress(compressed)
        assert decompressed == original

    def test_compress_empty(self):
        compressed = compress_stream(b"")
        assert zlib.decompress(compressed) == b""

    def test_compress_large_data(self):
        data = b"BT /F1 12 Tf (Hello) Tj ET\n" * 1000
        compressed = compress_stream(data)
        assert len(compressed) < len(data)
        assert zlib.decompress(compressed) == data


class TestLengthParsing:
    """Test /Length extraction and update in object headers."""

    def test_extract_length(self):
        header = b"5 0 obj\n<< /Length 1234 /Filter /FlateDecode >>"
        assert extract_length_from_header(header) == 1234

    def test_extract_length_large(self):
        header = b"10 0 obj << /Length 99999 >>"
        assert extract_length_from_header(header) == 99999

    def test_extract_length_missing(self):
        header = b"5 0 obj\n<< /Filter /FlateDecode >>"
        with pytest.raises(ValueError, match="Cannot find /Length"):
            extract_length_from_header(header)

    def test_update_length(self):
        header = b"5 0 obj\n<< /Length 1234 /Filter /FlateDecode >>"
        updated = update_length_in_header(header, 1234, 5678)
        assert b"/Length 5678" in updated
        assert b"/Length 1234" not in updated

    def test_update_length_preserves_rest(self):
        header = b"5 0 obj\n<< /Length 100 /Filter /FlateDecode /Subtype /Type1 >>"
        updated = update_length_in_header(header, 100, 200)
        assert b"/Filter /FlateDecode" in updated
        assert b"/Subtype /Type1" in updated
        assert b"/Length 200" in updated


class TestStreamBoundaries:
    """Test finding stream data in PDF objects."""

    def test_find_stream_lf(self):
        data = b"5 0 obj\n<< /Length 5 >>\nstream\nHello\nendstream\nendobj"
        header_end, start, end = find_stream_boundaries(data, 0)
        assert data[start:end] == b"Hello"

    def test_find_stream_crlf(self):
        data = b"5 0 obj\n<< /Length 5 >>\r\nstream\r\nHello\nendstream\nendobj"
        header_end, start, end = find_stream_boundaries(data, 0)
        assert data[start:end] == b"Hello"

    def test_no_stream_raises(self):
        data = b"5 0 obj\n<< /Length 0 >>\nendobj"
        with pytest.raises(ValueError, match="Cannot find 'stream'"):
            find_stream_boundaries(data, 0)


class TestXrefParsing:
    """Test xref table parsing."""

    def test_parse_simple_xref(self):
        pdf = (
            b"%PDF-1.4\n"
            b"1 0 obj\n<< >>\nendobj\n"
            b"xref\n"
            b"0 3\n"
            b"0000000000 65535 f \n"
            b"0000000009 00000 n \n"
            b"0000000100 00000 n \n"
            b"trailer\n<< /Size 3 >>\n"
            b"startxref\n"
            b"29\n"
            b"%%EOF"
        )
        offsets, xref_offset = parse_xref_table(pdf)
        assert 1 in offsets
        assert offsets[1] == 9
        assert 2 in offsets
        assert offsets[2] == 100

    def test_parse_xref_free_objects(self):
        pdf = (
            b"%PDF-1.4\n"
            b"xref\n"
            b"0 2\n"
            b"0000000000 65535 f \n"
            b"0000000050 00000 n \n"
            b"trailer\n<< /Size 2 >>\n"
            b"startxref\n"
            b"9\n"
            b"%%EOF"
        )
        offsets, _ = parse_xref_table(pdf)
        assert 0 not in offsets  # free object
        assert offsets[1] == 50

    def test_missing_startxref_raises(self):
        pdf = b"%PDF-1.4\nxref\n0 1\n0000000000 65535 f \ntrailer\n<< >>\n%%EOF"
        with pytest.raises(ValueError, match="Cannot find startxref"):
            parse_xref_table(pdf)
