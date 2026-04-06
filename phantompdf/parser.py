"""PDF binary parser - xref tables, content streams, font mappings."""

import re
import zlib


def parse_xref_table(data: bytes) -> tuple[dict[int, int], int]:
    """Parse the xref table from raw PDF bytes.

    Returns:
        (offsets dict {obj_num: byte_offset}, xref_table_offset)
    """
    startxref_pos = data.rfind(b"startxref\n")
    if startxref_pos == -1:
        startxref_pos = data.rfind(b"startxref\r\n")
    if startxref_pos == -1:
        raise ValueError("Cannot find startxref in PDF")

    after = data[startxref_pos + len(b"startxref") :].strip()
    xref_offset = int(after.split(b"\n")[0].split(b"\r")[0])

    xref_end = data.find(b"trailer", xref_offset)
    if xref_end == -1:
        raise ValueError("Cannot find trailer after xref table")

    xref_text = data[xref_offset:xref_end].decode("ascii", errors="replace")
    lines = xref_text.strip().split("\n")

    # lines[0] = "xref", lines[1] = "start_obj count"
    if not lines[0].strip().startswith("xref"):
        raise ValueError(f"Expected 'xref' but got: {lines[0]}")

    offsets = {}
    i = 1
    while i < len(lines):
        header = lines[i].strip().split()
        if len(header) == 2 and header[0].isdigit() and header[1].isdigit():
            start_obj = int(header[0])
            count = int(header[1])
            for j in range(count):
                entry = lines[i + 1 + j].strip().split()
                if len(entry) == 3 and entry[2] == "n":
                    offsets[start_obj + j] = int(entry[0])
            i += 1 + count
        else:
            i += 1

    return offsets, xref_offset


def find_stream_boundaries(data: bytes, obj_offset: int) -> tuple[int, int, int]:
    """Find stream data start, end, and the header region for an object.

    Returns:
        (header_end_offset, stream_data_start, stream_data_end)
    """
    # Find 'stream' keyword
    search_region = data[obj_offset : obj_offset + 4096]
    stream_marker = search_region.find(b"stream\r\n")
    if stream_marker != -1:
        data_start = obj_offset + stream_marker + len(b"stream\r\n")
    else:
        stream_marker = search_region.find(b"stream\n")
        if stream_marker == -1:
            raise ValueError(f"Cannot find 'stream' keyword for object at {obj_offset}")
        data_start = obj_offset + stream_marker + len(b"stream\n")

    header_end = obj_offset + stream_marker

    # Find endstream - try with preceding newline first
    end_marker = data.find(b"\nendstream", data_start)
    if end_marker == -1:
        end_marker = data.find(b"\rendstream", data_start)
    if end_marker == -1:
        end_marker = data.find(b"endstream", data_start)
    if end_marker == -1:
        raise ValueError(f"Cannot find 'endstream' for object at {obj_offset}")

    return header_end, data_start, end_marker


def decompress_stream(data: bytes, start: int, end: int) -> bytes:
    """Decompress a FlateDecode stream."""
    compressed = data[start:end]
    return zlib.decompress(compressed)


def compress_stream(decompressed: bytes, level: int = 9) -> bytes:
    """Compress stream data with FlateDecode."""
    return zlib.compress(decompressed, level)


def update_length_in_header(header: bytes, old_length: int, new_length: int) -> bytes:
    """Update the /Length value in an object header."""
    old_pat = f"/Length {old_length}".encode()
    new_pat = f"/Length {new_length}".encode()
    result = header.replace(old_pat, new_pat, 1)
    if result == header:
        # Try regex for different spacing
        result = re.sub(
            rb"/Length\s+" + str(old_length).encode(),
            f"/Length {new_length}".encode(),
            header,
            count=1,
        )
    return result


def extract_length_from_header(header: bytes) -> int:
    """Extract the /Length value from an object header."""
    match = re.search(rb"/Length\s+(\d+)", header)
    if not match:
        raise ValueError("Cannot find /Length in object header")
    return int(match.group(1))


def rebuild_xref_and_trailer(
    data: bytearray, offsets: dict[int, int], pivot_offset: int, shift: int
) -> bytearray:
    """Rebuild xref table with adjusted offsets for objects after the pivot point.

    Args:
        data: The modified PDF bytearray
        offsets: Original object offsets {obj_num: offset}
        pivot_offset: Objects after this offset get shifted
        shift: Number of bytes to shift (positive = grew, negative = shrank)
    """
    # Find xref table using startxref pointer (shifted by our edit)
    startxref_pos = data.rfind(b"startxref")
    old_xref_offset_str = data[startxref_pos + len(b"startxref"):].strip().split(b"\n")[0].split(b"\r")[0]
    old_xref_offset = int(old_xref_offset_str)

    # The xref table itself may have shifted
    xref_pos = old_xref_offset
    # Verify it starts with "xref"
    if not data[xref_pos:xref_pos+4] == b"xref":
        # Try with shift applied
        xref_pos = old_xref_offset + shift
    if not data[xref_pos:xref_pos+5] in (b"xref\n", b"xref\r"):
        raise ValueError(f"Cannot locate xref table at offset {xref_pos}")

    trailer_pos = data.find(b"trailer", xref_pos)

    xref_text = bytes(data[xref_pos:trailer_pos]).decode("ascii", errors="replace")
    lines = xref_text.strip().split("\n")

    # Rebuild entries
    new_lines = [lines[0]]  # "xref"

    i = 1
    while i < len(lines):
        header_parts = lines[i].strip().split()
        if len(header_parts) == 2 and header_parts[0].isdigit() and header_parts[1].isdigit():
            start_obj = int(header_parts[0])
            count = int(header_parts[1])
            new_lines.append(lines[i])

            for j in range(count):
                entry = lines[i + 1 + j].strip().split()
                if entry[2] == "f":
                    new_lines.append(f"{entry[0]} {entry[1]} f ")
                else:
                    old_off = int(entry[0])
                    obj_num = start_obj + j
                    if old_off > pivot_offset:
                        new_off = old_off + shift
                    else:
                        new_off = old_off
                    new_lines.append(f"{new_off:010d} {entry[1]} n ")
            i += 1 + count
        else:
            i += 1

    new_xref_text = "\n".join(new_lines) + "\n"
    data[xref_pos:trailer_pos] = new_xref_text.encode("ascii")

    # Update startxref to point to the new xref position
    # The xref table is right where we just wrote it
    # Find where it actually starts in the updated data
    actual_xref_pos = data.find(b"xref\n0 ", xref_pos - 100)
    if actual_xref_pos == -1:
        actual_xref_pos = data.find(b"xref\n", xref_pos - 100)

    startxref_pos2 = data.rfind(b"startxref")
    eof_pos = data.find(b"%%EOF", startxref_pos2)

    new_startxref = f"startxref\n{actual_xref_pos}\n".encode()
    data[startxref_pos2:eof_pos] = new_startxref

    return data
