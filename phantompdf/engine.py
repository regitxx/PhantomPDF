"""Surgical PDF text replacement engine - binary-level editing."""

import re
import zlib

import fitz

from .fonts import FontInfo, extract_fonts
from .parser import (
    compress_stream,
    decompress_stream,
    extract_length_from_header,
    find_stream_boundaries,
    parse_xref_table,
    rebuild_xref_and_trailer,
    update_length_in_header,
)


def _detect_fontsize(stream_text: str, font_ref: str) -> float:
    """Detect font size from content stream Tf operators.

    Looks for patterns like '/F1 12 Tf' to extract the size.
    Falls back to 10.0 if not found.
    """
    pattern = rf"/{re.escape(font_ref)}\s+([\d.]+)\s+Tf"
    match = re.search(pattern, stream_text)
    if match:
        try:
            return float(match.group(1))
        except ValueError:
            pass
    return 10.0


def escape_cid_for_pdf(cid_bytes: bytes) -> bytes:
    """Escape CID bytes for use inside a PDF string literal (...)."""
    result = b""
    for b in cid_bytes:
        if b == 0x28:
            result += b"\\("
        elif b == 0x29:
            result += b"\\)"
        elif b == 0x5C:
            result += b"\\\\"
        else:
            result += bytes([b])
    return result


def find_text_in_content_stream(
    stream_text: str, font: FontInfo, search_text: str
) -> list[dict]:
    """Find occurrences of text in a decoded content stream.

    Searches through TJ arrays and Tj strings for CID-encoded matches.
    Handles both parenthesized (...) and hex <...> string formats.
    Returns list of dicts with match info.
    """
    results = []
    try:
        search_cid = font.encode_text(search_text)
    except ValueError:
        return results

    lines = stream_text.split("\n")
    for i, line in enumerate(lines):
        if "TJ" not in line and "Tj" not in line:
            continue

        line_bytes = line.encode("latin-1")

        # Extract all CID bytes from both string formats
        all_cid = b""

        # Hex strings: <hex_data>
        for match in re.finditer(rb"<([0-9A-Fa-f\s]+)>", line_bytes):
            hex_str = match.group(1).decode("ascii").replace(" ", "")
            try:
                all_cid += bytes.fromhex(hex_str)
            except ValueError:
                pass

        # Parenthesized strings: (data)
        for match in re.finditer(rb"\(([^)]*(?:\\.[^)]*)*)\)", line_bytes):
            raw = match.group(1)
            raw = raw.replace(b"\\(", b"\x28")
            raw = raw.replace(b"\\)", b"\x29")
            raw = raw.replace(b"\\\\", b"\x5c")
            all_cid += raw

        if search_cid in all_cid:
            results.append({"line_num": i, "line": line})

    return results


def parse_tj_array(line: str) -> list[dict]:
    """Parse a TJ array line into segments.

    Handles both parenthesized strings (...) and hex strings <...>.
    Returns list of {"type": "text"|"adjust", "value": bytes, "hex": bool}
    """
    segments = []
    line_bytes = line.encode("latin-1")

    # Remove leading [ and trailing ]TJ
    inner = line_bytes
    if inner.startswith(b"["):
        inner = inner[1:]
    if inner.endswith(b"]TJ"):
        inner = inner[:-3]
    elif inner.endswith(b"]Tj"):
        inner = inner[:-3]

    pos = 0
    while pos < len(inner):
        if inner[pos : pos + 1] == b"<":
            # Hex string <...>
            end = inner.find(b">", pos)
            if end == -1:
                pos += 1
                continue
            hex_str = inner[pos + 1 : end].decode("ascii").strip()
            cid_bytes = bytes.fromhex(hex_str)
            segments.append({"type": "text", "value": cid_bytes, "hex": True})
            pos = end + 1
        elif inner[pos : pos + 1] == b"(":
            # Parenthesized string - find matching close paren, handling escapes
            depth = 1
            end = pos + 1
            while end < len(inner) and depth > 0:
                if inner[end : end + 1] == b"\\" and end + 1 < len(inner):
                    end += 2
                    continue
                if inner[end : end + 1] == b"(":
                    depth += 1
                elif inner[end : end + 1] == b")":
                    depth -= 1
                end += 1
            raw = inner[pos + 1 : end - 1]
            unescaped = raw.replace(b"\\(", b"\x28")
            unescaped = unescaped.replace(b"\\)", b"\x29")
            unescaped = unescaped.replace(b"\\\\", b"\x5c")
            segments.append({"type": "text", "value": unescaped, "hex": False})
            pos = end
        elif inner[pos : pos + 1] in (b"-", b"0", b"1", b"2", b"3", b"4", b"5", b"6", b"7", b"8", b"9", b"."):
            # Number (spacing adjustment)
            end = pos
            while end < len(inner) and inner[end : end + 1] not in (b"(", b"<", b"]"):
                end += 1
            num_str = inner[pos:end].decode("ascii").strip()
            if num_str:
                segments.append({"type": "adjust", "value": num_str})
            pos = end
        else:
            pos += 1

    return segments


def decode_tj_segments(segments: list[dict], font: FontInfo) -> list[dict]:
    """Add decoded text to TJ segments."""
    for seg in segments:
        if seg["type"] == "text":
            seg["decoded"] = font.decode_cid_bytes(seg["value"])
    return segments


def build_tj_line(segments: list[dict]) -> bytes:
    """Rebuild a TJ array line from segments, preserving encoding format."""
    # Detect if original used hex encoding
    uses_hex = any(seg.get("hex", False) for seg in segments if seg["type"] == "text")

    parts = []
    for seg in segments:
        if seg["type"] == "text":
            if uses_hex:
                parts.append(b"<" + seg["value"].hex().encode("ascii") + b">")
            else:
                escaped = escape_cid_for_pdf(seg["value"])
                parts.append(b"(" + escaped + b")")
        elif seg["type"] == "adjust":
            parts.append(seg["value"].encode("ascii"))
    return b"[" + b"".join(parts) + b"]TJ"


def replace_in_tj_segments(
    segments: list[dict],
    font: FontInfo,
    old_text: str,
    new_text: str,
    fontsize: float = 10.0,
) -> tuple[list[dict], bool]:
    """Replace text across TJ segments, preserving spacing structure.

    Handles the case where old_text spans multiple segments (split at word boundaries).
    Returns (new_segments, was_replaced).
    """
    # Concatenate all text segments to find the full text
    full_text = ""
    seg_boundaries = []  # (start_idx_in_full, segment_index)
    for i, seg in enumerate(segments):
        if seg["type"] == "text":
            decoded = font.decode_cid_bytes(seg["value"])
            seg_boundaries.append((len(full_text), i))
            full_text += decoded

    # Find old_text in the concatenated text
    match_pos = full_text.find(old_text)
    if match_pos == -1:
        return segments, False

    match_end = match_pos + len(old_text)

    # Find which segments are affected
    affected_start_seg = None
    affected_end_seg = None
    for idx, (text_start, seg_idx) in enumerate(seg_boundaries):
        seg = segments[seg_idx]
        decoded = font.decode_cid_bytes(seg["value"])
        text_end = text_start + len(decoded)

        if text_start <= match_pos < text_end and affected_start_seg is None:
            affected_start_seg = idx
        if text_start < match_end <= text_end:
            affected_end_seg = idx
            break

    if affected_start_seg is None or affected_end_seg is None:
        return segments, False

    # Determine the TJ adjustment value used between segments
    tj_adjust = None
    for seg in segments:
        if seg["type"] == "adjust":
            tj_adjust = seg["value"]
            break

    # Split old text at segment boundaries to understand the structure
    old_parts = []
    for idx in range(affected_start_seg, affected_end_seg + 1):
        text_start, seg_idx = seg_boundaries[idx]
        seg = segments[seg_idx]
        decoded = font.decode_cid_bytes(seg["value"])

        # Determine which portion of this segment is part of the match
        rel_start = max(0, match_pos - text_start)
        rel_end = min(len(decoded), match_end - text_start)
        old_parts.append(decoded[rel_start:rel_end])

    # Determine prefix (text before match in first affected segment)
    first_text_start, first_seg_idx = seg_boundaries[affected_start_seg]
    first_decoded = font.decode_cid_bytes(segments[first_seg_idx]["value"])
    prefix = first_decoded[: match_pos - first_text_start]

    # Determine suffix (text after match in last affected segment)
    last_text_start, last_seg_idx = seg_boundaries[affected_end_seg]
    last_decoded = font.decode_cid_bytes(segments[last_seg_idx]["value"])
    suffix = last_decoded[match_end - last_text_start :]

    # Detect hex encoding from existing segments
    uses_hex = any(seg.get("hex", False) for seg in segments if seg["type"] == "text")

    # Now build replacement segments
    # Split new_text into words the same way old_text was split
    # Each old_part starts with a space (word boundary), so split new_text similarly
    new_words = []
    current = ""
    for ch in new_text:
        if ch == " " and current:
            new_words.append(current)
            current = " "
        else:
            current += ch
    if current:
        new_words.append(current)

    # If old_parts started with a space-prefixed segment, match that pattern
    # Otherwise keep as single segment
    if len(old_parts) > 1:
        # Multi-segment: split new_text at space boundaries too
        new_parts = new_words
    else:
        new_parts = [new_text]

    # Build new segments list
    new_segments = []

    # Copy segments before affected range
    first_text_start_idx, first_seg_array_idx = seg_boundaries[affected_start_seg]
    for seg in segments[:first_seg_array_idx]:
        new_segments.append(seg)

    # Add prefix if any
    if prefix:
        new_segments.append({
            "type": "text",
            "value": font.encode_text(prefix),
            "decoded": prefix,
            "hex": uses_hex,
        })
        if tj_adjust:
            new_segments.append({"type": "adjust", "value": tj_adjust})

    # Add new text segments
    for i, part in enumerate(new_parts):
        new_segments.append({
            "type": "text",
            "value": font.encode_text(part),
            "decoded": part,
            "hex": uses_hex,
        })
        if i < len(new_parts) - 1 and tj_adjust:
            new_segments.append({"type": "adjust", "value": tj_adjust})

    # Add suffix if any
    if suffix:
        if tj_adjust:
            new_segments.append({"type": "adjust", "value": tj_adjust})
        new_segments.append({
            "type": "text",
            "value": font.encode_text(suffix),
            "decoded": suffix,
            "hex": uses_hex,
        })

    # Copy segments after affected range
    _, last_seg_array_idx = seg_boundaries[affected_end_seg]
    remaining = segments[last_seg_array_idx + 1 :]
    new_segments.extend(remaining)

    return new_segments, True


def surgical_replace(
    pdf_path: str,
    output_path: str,
    old_text: str,
    new_text: str,
    page_num: int = 0,
) -> dict:
    """Perform a traceless surgical text replacement in a PDF.

    Returns dict with operation details.
    """
    # Read original binary
    with open(pdf_path, "rb") as f:
        orig_data = f.read()

    # Open with PyMuPDF for inspection only
    doc = fitz.open(pdf_path)
    page = doc[page_num]

    # Extract font info
    font_map = extract_fonts(doc, page_num)

    # Find content stream xref
    contents_xrefs = page.get_contents()
    if not contents_xrefs:
        raise ValueError(f"Page {page_num} has no content stream")

    result = {
        "replaced": False,
        "font_used": None,
        "old_text": old_text,
        "new_text": new_text,
        "width_change": 0,
    }

    # Try each content stream
    for content_xref in contents_xrefs:
        offsets, xref_table_offset = parse_xref_table(orig_data)

        if content_xref not in offsets:
            continue

        obj_offset = offsets[content_xref]
        header_end, stream_start, stream_end = find_stream_boundaries(
            orig_data, obj_offset
        )
        header = orig_data[obj_offset:header_end]
        orig_length = extract_length_from_header(header)
        raw_stream = orig_data[stream_start:stream_end]

        # Check if stream is compressed
        is_compressed = b"/FlateDecode" in header or b"/Filter" in header
        if is_compressed:
            decompressed = decompress_stream(orig_data, stream_start, stream_end)
        else:
            decompressed = bytes(raw_stream)

        stream_text = decompressed.decode("latin-1")

        # Try each font to find the text
        for ref_name, font in font_map.items():
            if not font.char_to_cid:
                continue

            # Check for missing glyphs but don't skip - try encoding later
            missing = [ch for ch in font.missing_glyphs(new_text) if ch not in old_text]

            matches = find_text_in_content_stream(stream_text, font, old_text)
            if not matches:
                continue

            # Found it! Now replace
            match_info = matches[0]
            line_num = match_info["line_num"]
            line = match_info["line"]

            # Parse TJ array
            segments = parse_tj_array(line)
            segments = decode_tj_segments(segments, font)

            # Replace text
            new_segments, was_replaced = replace_in_tj_segments(
                segments, font, old_text, new_text
            )

            if not was_replaced:
                continue

            # Build new TJ line
            new_tj_line = build_tj_line(new_segments)

            # Replace in stream
            stream_lines = stream_text.split("\n")
            stream_lines[line_num] = new_tj_line.decode("latin-1")
            new_decompressed = "\n".join(stream_lines).encode("latin-1")

            # Compress or keep raw depending on original
            if is_compressed:
                new_stream_data = compress_stream(new_decompressed)
            else:
                new_stream_data = new_decompressed

            # Calculate size difference
            size_diff = len(new_stream_data) - len(raw_stream)
            length_str_diff = len(str(len(new_stream_data))) - len(str(orig_length))
            total_shift = size_diff + length_str_diff

            # Build new file
            new_header = update_length_in_header(
                header, orig_length, len(new_stream_data)
            )

            new_data = bytearray()
            new_data += orig_data[:obj_offset]
            new_data += new_header
            new_data += orig_data[header_end : stream_start]  # "stream\n"
            new_data += new_stream_data
            new_data += orig_data[stream_end:]

            # Fix xref table
            if total_shift != 0:
                new_data = rebuild_xref_and_trailer(
                    new_data, offsets, obj_offset, total_shift
                )

            # Write output
            with open(output_path, "wb") as f:
                f.write(new_data)

            # Detect font size from content stream
            fontsize = _detect_fontsize(stream_text, ref_name)
            old_width = font.text_width(old_text, fontsize)
            new_width = font.text_width(new_text, fontsize)

            result.update({
                "replaced": True,
                "font_used": font.name,
                "font_ref": ref_name,
                "content_xref": content_xref,
                "size_diff": total_shift,
                "width_change": new_width - old_width,
                "old_width": old_width,
                "new_width": new_width,
            })

            doc.close()
            return result

    doc.close()
    return result
