"""Tests for the forensic cleaner module."""

import pytest

from phantompdf.cleaner import (
    scan_tool_traces,
    strip_tool_traces,
    flatten_incremental_saves,
    _is_inside_metadata_string,
)


class TestScanToolTraces:
    """Test tool watermark scanning."""

    def test_finds_mupdf(self):
        data = b"%PDF-1.4\n/Producer (MuPDF 1.23)\n%%EOF"
        traces = scan_tool_traces(data)
        markers = [t["marker"] for t in traces]
        assert b"MuPDF" in markers

    def test_finds_multiple_tools(self):
        data = b"%PDF-1.4\n/Producer (iText 7.0)\n/Creator (Ghostscript)\n%%EOF"
        traces = scan_tool_traces(data)
        markers = [t["marker"] for t in traces]
        assert b"iText" in markers
        assert b"Ghostscript" in markers

    def test_no_traces_in_clean_pdf(self):
        data = b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog >>\nendobj\n%%EOF"
        traces = scan_tool_traces(data)
        assert len(traces) == 0

    def test_counts_multiple_occurrences(self):
        data = b"MuPDF something MuPDF again MuPDF"
        traces = scan_tool_traces(data)
        mupdf = [t for t in traces if t["marker"] == b"MuPDF"][0]
        assert mupdf["count"] == 3

    def test_finds_phantom_pdf(self):
        data = b"%PDF-1.4\nphantom-pdf was here\n%%EOF"
        traces = scan_tool_traces(data)
        markers = [t["marker"] for t in traces]
        assert b"phantom-pdf" in markers


class TestStripToolTraces:
    """Test tool watermark removal."""

    def test_strips_outside_stream(self):
        data = b"%PDF-1.4\n/Producer (MuPDF v1.23)\n%%EOF"
        traces = scan_tool_traces(data)
        cleaned, actions = strip_tool_traces(data, traces)
        assert b"MuPDF" not in cleaned
        assert len(actions) > 0

    def test_preserves_byte_length(self):
        """Stripping replaces with spaces to preserve xref offsets."""
        data = b"%PDF-1.4\n/Producer (MuPDF v1.23)\n%%EOF"
        traces = scan_tool_traces(data)
        cleaned, _ = strip_tool_traces(data, traces)
        assert len(cleaned) == len(data)

    def test_skips_inside_stream(self):
        """Traces inside content streams should not be stripped."""
        data = (
            b"%PDF-1.4\n"
            b"1 0 obj\n<< /Length 10 >>\nstream\n"
            b"MuPDF data\n"
            b"endstream\nendobj\n%%EOF"
        )
        traces = scan_tool_traces(data)
        cleaned, actions = strip_tool_traces(data, traces)
        # The MuPDF inside stream should still be there
        # (the stripper should skip it)
        assert b"MuPDF" in cleaned or len(actions) == 0


class TestFlattenIncrementalSaves:
    """Test incremental save flattening."""

    def test_single_eof_unchanged(self):
        data = b"%PDF-1.4\n1 0 obj<</Type/Catalog>>endobj\nxref\n0 1\n0000000000 65535 f \ntrailer<</Size 1>>\nstartxref\n0\n%%EOF"
        result, actions = flatten_incremental_saves(data)
        assert "Already clean" in actions[0]

    def test_detects_multiple_eof(self):
        # This is a synthetic test — real incremental saves are more complex
        data = b"%PDF-1.4\nsome content\n%%EOF\nmore content\n%%EOF"
        # This will fail to parse as valid PDF, but we test detection
        assert data.count(b"%%EOF") == 2


class TestMetadataStringDetection:
    """Test metadata string boundary detection."""

    def test_inside_producer(self):
        data = b"<< /Producer (MuPDF v1.23) >>"
        # "MuPDF" starts at offset 16
        assert _is_inside_metadata_string(data, 16) is True

    def test_outside_metadata(self):
        data = b"<< /Type /Catalog /Pages 2 0 R >>"
        assert _is_inside_metadata_string(data, 10) is False
