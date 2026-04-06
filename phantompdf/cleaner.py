"""Forensic cleaner - strip tool traces, flatten incremental saves, reset metadata."""

import re
import zlib
from datetime import datetime, timezone

import fitz


# Known tool watermarks that can appear inside PDF binary data.
# Each entry: (marker_bytes, description)
TOOL_MARKERS = [
    # PDF libraries
    (b"MuPDF", "MuPDF engine"),
    (b"PyMuPDF", "PyMuPDF / fitz"),
    (b"pymupdf", "PyMuPDF lowercase"),
    (b"fitz", "fitz / MuPDF binding"),
    (b"QPDF", "QPDF"),
    (b"qpdf", "qpdf lowercase"),
    (b"pdftk", "pdftk"),
    (b"Ghostscript", "Ghostscript"),
    (b"GhostPDF", "GhostPDF"),
    (b"iText", "iText"),
    (b"itext", "iText lowercase"),
    (b"OpenPDF", "OpenPDF"),
    (b"cairo", "Cairo graphics"),
    (b"Skia/PDF", "Skia PDF"),
    (b"Prince", "PrinceXML"),
    (b"wkhtmltopdf", "wkhtmltopdf"),
    (b"LibreOffice", "LibreOffice"),
    (b"OpenOffice", "OpenOffice"),
    (b"Microsoft", "Microsoft Office"),
    (b"Nitro", "Nitro PDF"),
    (b"Foxit", "Foxit"),
    (b"PDFium", "PDFium / Chromium"),
    (b"Chromium", "Chromium"),
    (b"Acrobat Distiller", "Adobe Acrobat Distiller"),
    (b"Adobe InDesign", "Adobe InDesign"),
    (b"phantom-pdf", "PhantomPDF (our own)"),
]

# Metadata keys that tools commonly modify
METADATA_TOOL_KEYS = ["producer", "creator"]


def scan_tool_traces(data: bytes) -> list[dict]:
    """Scan PDF binary for tool watermarks.

    Returns list of {marker, description, offsets} for each found trace.
    """
    found = []
    for marker, desc in TOOL_MARKERS:
        offsets = []
        start = 0
        while True:
            pos = data.find(marker, start)
            if pos == -1:
                break
            offsets.append(pos)
            start = pos + 1
        if offsets:
            found.append({
                "marker": marker,
                "description": desc,
                "offsets": offsets,
                "count": len(offsets),
            })
    return found


def _is_inside_stream(data: bytes, offset: int) -> bool:
    """Check if a byte offset falls inside a stream...endstream block.

    We search backwards for 'stream' and 'endstream' to determine this.
    """
    # Look backwards up to 64KB for stream boundaries
    search_start = max(0, offset - 65536)
    region = data[search_start:offset + 64]
    rel_offset = offset - search_start

    # Find the last 'stream\n' or 'stream\r\n' before our offset
    last_stream = -1
    last_endstream = -1

    for m in re.finditer(rb"stream[\r\n]", region[:rel_offset]):
        # Make sure it's not "endstream"
        start = m.start()
        if start >= 3 and region[start - 3:start] == b"end":
            last_endstream = start
        else:
            last_stream = m.start()

    for m in re.finditer(rb"endstream", region[:rel_offset]):
        last_endstream = m.start()

    if last_stream == -1:
        return False
    if last_endstream == -1:
        return True  # Found stream but no endstream before us
    return last_stream > last_endstream


def _is_inside_metadata_string(data: bytes, offset: int) -> bool:
    """Check if offset is inside a metadata string value (Producer, Creator, etc)."""
    # Look backwards for /Producer or /Creator within ~200 bytes
    region_start = max(0, offset - 200)
    region = data[region_start:offset]
    for key in [b"/Producer", b"/Creator", b"/Author"]:
        pos = region.rfind(key)
        if pos != -1:
            return True
    return False


def strip_tool_traces(data: bytes, traces: list[dict]) -> tuple[bytes, list[str]]:
    """Remove tool watermark strings from PDF binary.

    Only removes traces found in metadata string values and comments.
    Does NOT touch compressed streams (those are safe — the marker would
    only appear after decompression which no forensic tool does).

    Returns (cleaned_data, list of actions taken).
    """
    actions = []
    result = bytearray(data)

    for trace in traces:
        marker = trace["marker"]
        for offset in trace["offsets"]:
            # Only strip if it's in a metadata string, not inside a content stream
            if _is_inside_stream(data, offset):
                continue

            # Verify the marker is still at this offset (not shifted by previous edits)
            if result[offset:offset + len(marker)] != marker:
                continue

            # Replace with spaces (preserves byte offsets so xref stays valid)
            replacement = b" " * len(marker)
            result[offset:offset + len(marker)] = replacement
            actions.append(f"Stripped '{marker.decode()}' at offset {offset}")

    return bytes(result), actions


def _binary_replace_metadata_value(data: bytes, key: bytes, new_value: str) -> tuple[bytes, bool]:
    """Replace a metadata string value at the binary level, preserving byte offsets.

    Searches for /Key (old value) and replaces the value inside parens.
    Pads or truncates with spaces to maintain exact same byte length.
    """
    # Find pattern like /Producer (some value)
    pattern = re.compile(key + rb"\s*\(([^)]*)\)")
    match = pattern.search(data)
    if not match:
        return data, False

    old_value_bytes = match.group(1)
    new_value_bytes = new_value.encode("latin-1", errors="replace")

    # Pad or truncate to same length to preserve xref offsets
    target_len = len(old_value_bytes)
    if len(new_value_bytes) < target_len:
        new_value_bytes = new_value_bytes + b" " * (target_len - len(new_value_bytes))
    elif len(new_value_bytes) > target_len:
        new_value_bytes = new_value_bytes[:target_len]

    result = bytearray(data)
    result[match.start(1):match.end(1)] = new_value_bytes
    return bytes(result), True


def clean_metadata(data: bytes,
                   producer: str | None = None,
                   creator: str | None = None,
                   reset_moddate: bool = True) -> tuple[bytes, dict]:
    """Clean metadata fields at the binary level without re-saving via PyMuPDF.

    This avoids PyMuPDF's save() which itself stamps the Producer field.
    Works by direct byte replacement, preserving file size and xref offsets.

    Args:
        data: Raw PDF bytes
        producer: Override Producer field (None = keep original)
        creator: Override Creator field (None = keep original)
        reset_moddate: If True, set ModDate equal to CreationDate (looks unedited)

    Returns (modified_data, result_dict).
    """
    result = {"actions": [], "metadata_before": {}, "metadata_after": {}}

    # Extract current metadata for reporting
    doc = fitz.open(stream=data, filetype="pdf")
    meta = doc.metadata.copy()
    result["metadata_before"] = meta.copy()
    doc.close()

    changed = False

    if producer is not None and meta.get("producer", "") != producer:
        data, did_replace = _binary_replace_metadata_value(data, b"/Producer", producer)
        if did_replace:
            changed = True
            result["actions"].append(f"Producer: '{meta.get('producer', '')}' -> '{producer}'")

    if creator is not None and meta.get("creator", "") != creator:
        data, did_replace = _binary_replace_metadata_value(data, b"/Creator", creator)
        if did_replace:
            changed = True
            result["actions"].append(f"Creator: '{meta.get('creator', '')}' -> '{creator}'")

    if reset_moddate and meta.get("creationDate") and meta.get("modDate"):
        if meta["modDate"] != meta["creationDate"]:
            data, did_replace = _binary_replace_metadata_value(data, b"/ModDate", meta["creationDate"])
            if did_replace:
                changed = True
                result["actions"].append("ModDate reset to match CreationDate")

    if not changed:
        result["actions"].append("No metadata changes needed")

    # Re-read metadata for the "after" report
    doc = fitz.open(stream=data, filetype="pdf")
    result["metadata_after"] = doc.metadata.copy()
    doc.close()

    return data, result


def flatten_incremental_saves(data: bytes) -> tuple[bytes, list[str]]:
    """Flatten a PDF with multiple %%EOF markers (incremental saves) to a single revision.

    Incremental saves append new objects and a new xref+trailer after the original %%EOF.
    This makes it obvious the file was edited. Flattening rebuilds to a single revision.

    Returns (flattened_data, actions).
    """
    actions = []
    eof_count = data.count(b"%%EOF")

    if eof_count <= 1:
        actions.append("Already clean — single %%EOF")
        return data, actions

    actions.append(f"Found {eof_count} %%EOF markers (incremental saves detected)")

    # Use PyMuPDF to flatten: open and save without incremental mode.
    # This rebuilds the xref and merges all revisions into one.
    doc = fitz.open(stream=data, filetype="pdf")

    # Save to bytes — garbage=3 removes unused objects, no incremental
    flat = doc.tobytes(garbage=3, deflate=True)
    doc.close()

    new_eof_count = flat.count(b"%%EOF")
    actions.append(f"Flattened to {new_eof_count} %%EOF marker(s)")
    actions.append(f"Size: {len(data)} -> {len(flat)} bytes ({len(flat) - len(data):+d})")

    return flat, actions


def clean_pdf(
    pdf_path: str,
    output_path: str,
    strip_traces: bool = True,
    flatten: bool = True,
    reset_metadata: bool = True,
    producer_override: str | None = None,
    creator_override: str | None = None,
) -> dict:
    """Full forensic cleanup pipeline.

    Args:
        pdf_path: Input PDF file
        output_path: Output file path
        strip_traces: Remove tool watermarks from binary
        flatten: Flatten incremental saves (multiple %%EOF)
        reset_metadata: Reset ModDate to match CreationDate
        producer_override: Override the Producer metadata field
        creator_override: Override the Creator metadata field

    Returns dict with all actions taken.
    """
    report = {
        "input": pdf_path,
        "output": output_path,
        "steps": [],
    }

    with open(pdf_path, "rb") as f:
        data = f.read()

    # Step 1: Flatten incremental saves
    if flatten:
        data, flat_actions = flatten_incremental_saves(data)
        report["steps"].append({
            "name": "Flatten incremental saves",
            "actions": flat_actions,
        })

    # Step 2: Clean metadata at binary level (no PyMuPDF save)
    meta_result = {"actions": []}
    if reset_metadata or producer_override or creator_override:
        data, meta_result = clean_metadata(
            data,
            producer=producer_override,
            creator=creator_override,
            reset_moddate=reset_metadata,
        )
        report["steps"].append({
            "name": "Clean metadata",
            "actions": meta_result["actions"],
            "before": meta_result["metadata_before"],
            "after": meta_result["metadata_after"],
        })

    # Step 3: Strip tool traces from binary
    if strip_traces:
        traces = scan_tool_traces(data)
        if traces:
            data, strip_actions = strip_tool_traces(data, traces)
            report["steps"].append({
                "name": "Strip tool traces",
                "actions": strip_actions,
                "traces_found": [
                    {"marker": t["marker"].decode(), "count": t["count"]}
                    for t in traces
                ],
            })
        else:
            report["steps"].append({
                "name": "Strip tool traces",
                "actions": ["No tool traces found"],
            })

    # Write final output
    with open(output_path, "wb") as f:
        f.write(data)

    report["input_size"] = len(open(pdf_path, "rb").read())
    report["output_size"] = len(data)
    report["size_diff"] = len(data) - report["input_size"]

    return report
