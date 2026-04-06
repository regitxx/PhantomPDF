"""CID font decoder - ToUnicode CMaps, W arrays, glyph width tables."""

import re

import fitz


def parse_tounicode_cmap(cmap_bytes: bytes) -> dict[str, int]:
    """Parse a ToUnicode CMap and return {unicode_char: CID} mapping."""
    text = cmap_bytes.decode("utf-8", errors="replace")
    char_to_cid = {}

    # Parse beginbfrange entries: <CID_start><CID_end><Unicode_start>
    for section in re.findall(
        r"beginbfrange\s*(.*?)\s*endbfrange", text, re.DOTALL
    ):
        for match in re.finditer(
            r"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>", section
        ):
            cid_start = int(match.group(1), 16)
            cid_end = int(match.group(2), 16)
            uni_start = int(match.group(3), 16)
            for i in range(cid_end - cid_start + 1):
                char_to_cid[chr(uni_start + i)] = cid_start + i

    # Parse beginbfchar entries: <CID><Unicode> (single char mappings)
    for section in re.findall(
        r"beginbfchar\s*(.*?)\s*endbfchar", text, re.DOTALL
    ):
        for match in re.finditer(
            r"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>", section
        ):
            cid = int(match.group(1), 16)
            uni = int(match.group(2), 16)
            char_to_cid[chr(uni)] = cid

    return char_to_cid


def parse_w_array(w_string: str) -> dict[int, int]:
    """Parse a CIDFont W array into {CID: width_in_thousandths}.

    W array format examples:
        [305[503 520 520]]       - CID 305=503, 306=520, 307=520
        [305 310 520]            - CID 305-310 all have width 520
    """
    cid_widths = {}

    # Handle format: start_cid [w1 w2 w3 ...]
    for match in re.finditer(r"(\d+)\s*\[([^\]]+)\]", w_string):
        start_cid = int(match.group(1))
        widths = [int(w) for w in match.group(2).split()]
        for i, w in enumerate(widths):
            cid_widths[start_cid + i] = w

    # Handle format: start_cid end_cid width (uniform range)
    cleaned = re.sub(r"\[([^\]]*)\]", "", w_string)
    for match in re.finditer(r"(\d+)\s+(\d+)\s+(\d+)", cleaned):
        start_cid = int(match.group(1))
        end_cid = int(match.group(2))
        width = int(match.group(3))
        for cid in range(start_cid, end_cid + 1):
            cid_widths[cid] = width

    return cid_widths


class FontInfo:
    """Decoded font information for a CID font in a PDF."""

    def __init__(
        self,
        name: str,
        ref_name: str,
        xref: int,
        char_to_cid: dict[str, int],
        cid_widths: dict[int, int],
        default_width: int = 1000,
    ):
        self.name = name
        self.ref_name = ref_name  # e.g. "F1"
        self.xref = xref
        self.char_to_cid = char_to_cid
        self.cid_to_char = {v: k for k, v in char_to_cid.items()}
        self.cid_widths = cid_widths
        self.default_width = default_width

    def has_glyph(self, char: str) -> bool:
        """Check if this font has a glyph for the given character."""
        return char in self.char_to_cid

    def missing_glyphs(self, text: str) -> list[str]:
        """Return list of characters in text that have no glyph in this font."""
        missing = []
        seen = set()
        for ch in text:
            if ch not in seen and not self.has_glyph(ch):
                missing.append(ch)
                seen.add(ch)
        return missing

    def char_width(self, char: str, fontsize: float = 10.0) -> float:
        """Get the width of a character in points at the given fontsize."""
        cid = self.char_to_cid.get(char)
        if cid is None:
            return self.default_width * fontsize / 1000
        w = self.cid_widths.get(cid, self.default_width)
        return w * fontsize / 1000

    def text_width(self, text: str, fontsize: float = 10.0) -> float:
        """Get total width of text in points (no word spacing)."""
        return sum(self.char_width(ch, fontsize) for ch in text)

    def encode_text(self, text: str) -> bytes:
        """Encode text as CID byte string for PDF content stream."""
        result = b""
        for ch in text:
            cid = self.char_to_cid.get(ch)
            if cid is None:
                # Try visual equivalents (chars that share the same glyph)
                equivalents = {
                    "-": ["\u00AD", "\u2010", "\u2011"],  # hyphen ↔ soft hyphen, hyphen
                    "\u00AD": ["-", "\u2010"],
                    " ": ["\u00A0"],  # space ↔ non-breaking space
                    "\u00A0": [" "],
                }
                for alt in equivalents.get(ch, []):
                    if alt in self.char_to_cid:
                        cid = self.char_to_cid[alt]
                        self.char_to_cid[ch] = cid
                        break

            if cid is None:
                # Try to infer CID from neighboring characters
                target = ord(ch)
                for known_ch, known_cid in self.char_to_cid.items():
                    delta = target - ord(known_ch)
                    if abs(delta) <= 2 and delta != 0:
                        candidate_cid = known_cid + delta
                        if candidate_cid not in self.cid_to_char and candidate_cid >= 0:
                            cid = candidate_cid
                            self.char_to_cid[ch] = cid
                            self.cid_to_char[cid] = ch
                            break
            if cid is None:
                raise ValueError(
                    f"Character '{ch}' (U+{ord(ch):04X}) not in font '{self.name}'"
                )
            result += cid.to_bytes(2, "big")
        return result

    def decode_cid_bytes(self, data: bytes) -> str:
        """Decode CID byte pairs back to unicode text."""
        result = []
        for i in range(0, len(data), 2):
            if i + 1 < len(data):
                cid = int.from_bytes(data[i : i + 2], "big")
                result.append(self.cid_to_char.get(cid, f"[CID:{cid}]"))
        return "".join(result)


def extract_fonts(doc: fitz.Document, page_num: int = 0) -> dict[str, FontInfo]:
    """Extract all CID font information from a PDF page.

    Returns:
        {ref_name: FontInfo} e.g. {"F1": FontInfo(...)}
    """
    page = doc[page_num]
    fonts = page.get_fonts(full=True)
    font_map = {}

    for xref, ext, subtype, name, ref_name, encoding, _ in fonts:
        if encoding != "Identity-H":
            # Simple font, not CID - limited support
            font_map[ref_name] = FontInfo(
                name=name,
                ref_name=ref_name,
                xref=xref,
                char_to_cid={},
                cid_widths={},
            )
            continue

        # Get ToUnicode CMap
        font_keys = doc.xref_get_keys(xref)
        char_to_cid = {}
        if "ToUnicode" in font_keys:
            tounicode_ref = doc.xref_get_key(xref, "ToUnicode")
            if tounicode_ref[0] == "xref":
                tounicode_xref = int(tounicode_ref[1].split()[0])
                cmap_data = doc.xref_stream(tounicode_xref)
                char_to_cid = parse_tounicode_cmap(cmap_data)

        # Augment CMap with characters found on the page via PyMuPDF
        # This catches chars like space that some CMaps omit
        blocks = page.get_text("rawdict")
        for block in blocks.get("blocks", []):
            if "lines" not in block:
                continue
            for line_data in block["lines"]:
                for span in line_data["spans"]:
                    if span.get("font", "") != name.split("+")[-1]:
                        continue
                    for ch_data in span.get("chars", []):
                        ch = ch_data["c"]
                        if ch and ch not in char_to_cid:
                            # Try to find CID from content stream context
                            # For now, register common unmapped chars
                            pass

        # Fill CID gaps using interpolation from known mappings
        # CID fonts often have sequential CID->Unicode mappings with
        # some CIDs omitted from the CMap. Fill gaps between ranges.
        if char_to_cid:
            cid_to_char = {v: k for k, v in char_to_cid.items()}
            max_cid = max(cid_to_char.keys()) if cid_to_char else 0
            for cid in range(max_cid + 1):
                if cid in cid_to_char:
                    continue
                # Try to infer from neighbors
                # If CID-1 maps to U+XX and CID+1 maps to U+XX+2,
                # then CID maps to U+XX+1
                prev_char = cid_to_char.get(cid - 1)
                next_char = cid_to_char.get(cid + 1)
                if prev_char and next_char:
                    expected = ord(prev_char) + 1
                    if expected == ord(next_char) - 1:
                        inferred = chr(expected)
                        char_to_cid[inferred] = cid
                        cid_to_char[cid] = inferred

        # Common CID mappings often missing from CMaps
        if " " not in char_to_cid:
            char_to_cid[" "] = 3

        # Get CIDFont descendant for W array
        cid_widths = {}
        default_width = 1000
        if "DescendantFonts" in font_keys:
            desc_ref = doc.xref_get_key(xref, "DescendantFonts")
            # Parse array reference like "[15 0 R]"
            desc_match = re.search(r"(\d+)\s+0\s+R", desc_ref[1])
            if desc_match:
                cidfont_xref = int(desc_match.group(1))
                cidfont_keys = doc.xref_get_keys(cidfont_xref)

                if "DW" in cidfont_keys:
                    dw_val = doc.xref_get_key(cidfont_xref, "DW")
                    default_width = int(dw_val[1])

                if "W" in cidfont_keys:
                    w_val = doc.xref_get_key(cidfont_xref, "W")
                    if w_val[0] == "xref":
                        # W is an indirect reference - read the object
                        w_xref = int(w_val[1].split()[0])
                        # Try as stream first, fall back to raw object
                        w_data = doc.xref_stream(w_xref)
                        if w_data:
                            cid_widths = parse_w_array(w_data.decode("ascii", errors="replace"))
                        else:
                            w_obj = doc.xref_object(w_xref)
                            cid_widths = parse_w_array(w_obj)
                    else:
                        cid_widths = parse_w_array(w_val[1])

        font_map[ref_name] = FontInfo(
            name=name,
            ref_name=ref_name,
            xref=xref,
            char_to_cid=char_to_cid,
            cid_widths=cid_widths,
            default_width=default_width,
        )

    return font_map


def print_font_table(font_map: dict[str, FontInfo]) -> None:
    """Pretty-print font information."""
    for ref_name, info in font_map.items():
        print(f"\n{'='*60}")
        print(f"  Font: {info.name}")
        print(f"  Ref:  /{ref_name} (xref {info.xref})")
        print(f"  Default width: {info.default_width}")
        print(f"  Mapped characters: {len(info.char_to_cid)}")
        print(f"  Glyph widths defined: {len(info.cid_widths)}")

        if info.char_to_cid:
            print(f"\n  Character map:")
            # Group by type
            digits = []
            letters = []
            punct = []
            for ch, cid in sorted(info.char_to_cid.items(), key=lambda x: x[1]):
                w = info.cid_widths.get(cid, info.default_width)
                entry = f"'{ch}' CID={cid} W={w}"
                if ch.isdigit():
                    digits.append(entry)
                elif ch.isalpha():
                    letters.append(entry)
                else:
                    punct.append(entry)

            if digits:
                print(f"    Digits:  {', '.join(digits)}")
            if letters:
                for i in range(0, len(letters), 5):
                    chunk = letters[i : i + 5]
                    print(f"    Letters: {', '.join(chunk)}")
            if punct:
                print(f"    Punct:   {', '.join(punct)}")
    print()
