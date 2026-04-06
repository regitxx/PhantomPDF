<p align="center">
  <img src="https://img.shields.io/badge/phantom--pdf-v1.0.0-800020?style=for-the-badge&labelColor=1a1a1a" alt="version"/>
  <img src="https://img.shields.io/badge/python-3.9+-d4af37?style=for-the-badge&labelColor=1a1a1a&logo=python&logoColor=d4af37" alt="python"/>
  <img src="https://img.shields.io/badge/license-MIT-800020?style=for-the-badge&labelColor=1a1a1a" alt="license"/>
  <img src="https://img.shields.io/github/stars/regitxx/PhantomPDF?style=for-the-badge&color=d4af37&labelColor=1a1a1a" alt="stars"/>
</p>

<h1 align="center">
  <br>
  PhantomPDF
  <br>
  <sub><sup>traceless pdf editing</sup></sub>
</h1>

<p align="center">
  <strong>Surgical binary-level text replacement that preserves fonts, layout, metadata, and file structure.</strong>
</p>

<p align="center">
  <a href="#how-it-works">How It Works</a> •
  <a href="#installation">Installation</a> •
  <a href="#quick-start">Quick Start</a> •
  <a href="#commands">Commands</a> •
  <a href="#supported-features">Features</a> •
  <a href="#contributing">Contributing</a>
</p>

---

## The Problem

Every PDF editor leaves fingerprints. When tools like PyMuPDF, pdftk, Ghostscript, or iText save a PDF, they rewrite the entire file structure — leaving behind:

- Tool watermarks in the binary (`MuPDF`, `iText`, `cairo`, etc.)
- Changed file headers, xref format, object counts
- Modified metadata (`Producer`, `ModDate`)
- Incremental save markers (multiple `%%EOF`)

**PhantomPDF doesn't do any of that.**

## How It Works

Instead of re-rendering or re-saving, PhantomPDF performs **surgical content stream patching** — the only bytes that change are the ones encoding your text:

1. **Decompresses** only the target content stream
2. **Locates text** via CID font encoding (handles Identity-H CID fonts)
3. **Swaps the encoded bytes** in the TJ/Tj operators
4. **Recompresses** and patches the binary in-place
5. **Recalculates** xref table offsets
6. **Fixes** OS-level timestamps and quarantine attributes

The result is a file that's structurally identical to the original — same header, same objects, same metadata, same fonts. The built-in forensic verifier confirms zero detectable traces.

## Installation

```bash
# Clone and install
git clone https://github.com/regitxx/PhantomPDF.git
cd PhantomPDF
pip install -e .

# Or install directly
pip install .
```

**Requirements:** Python 3.9+, PyMuPDF

## Quick Start

```bash
# 1. Inspect — find the exact text and font details
phantom-pdf inspect document.pdf --search "1000"

# 2. Check — verify font supports your replacement characters
phantom-pdf fonts document.pdf --check-text "9999"

# 3. Preview — dry run to see what would change
phantom-pdf replace document.pdf --old "1000" --new "9999" --dry-run

# 4. Replace — surgical text swap with forensic verification
phantom-pdf replace document.pdf --old "1000" --new "9999" --verify

# 5. Audit — deep forensic check against original
phantom-pdf verify document_phantom.pdf --original document.pdf
```

## Commands

### `inspect` — Find text and font details

```bash
# Show all text with font info
phantom-pdf inspect document.pdf

# Search for specific text
phantom-pdf inspect document.pdf --search "1000"

# Character-level positioning
phantom-pdf inspect document.pdf --search "1000" --verbose

# JSON output for scripting
phantom-pdf inspect document.pdf --json
```

### `fonts` — Decode CID font tables

```bash
# Show all font mappings, CID tables, glyph widths
phantom-pdf fonts document.pdf

# Check if a font supports your replacement text
phantom-pdf fonts document.pdf --check-text "your new text here"
```

> **Why this matters:** CID fonts are subsetted — they only contain glyphs used in the original document. If your replacement text uses characters not in the subset, it will fail. The `fonts` command tells you exactly which characters are available.

### `replace` — Surgical text swap

```bash
# Basic replacement (outputs to document_phantom.pdf)
phantom-pdf replace document.pdf --old "100,00" --new "999,00"

# Dry run — preview without modifying
phantom-pdf replace document.pdf --old "100,00" --new "999,00" --dry-run

# Custom output path
phantom-pdf replace document.pdf --old "100,00" --new "999,00" -o output.pdf

# Replace and verify in one command
phantom-pdf replace document.pdf --old "100,00" --new "999,00" --verify

# In-place edit (overwrites original)
phantom-pdf replace document.pdf --old "100,00" --new "999,00" --in-place

# Target a specific page (0-indexed)
phantom-pdf replace document.pdf --old "Page 2 text" --new "New text" --page 1

# Multiple replacements in one pass
phantom-pdf replace doc.pdf \
  --old "253 028,42" --new "5 021 028,42" \
  --old "two hundred" --new "five million" \
  --verify

# JSON output for scripting/automation
phantom-pdf replace doc.pdf --old "old" --new "new" --json

# Skip OS-level fixes
phantom-pdf replace doc.pdf --old "old" --new "new" --no-timestamps --no-quarantine
```

### `verify` — Forensic audit

```bash
# Basic verification
phantom-pdf verify document.pdf

# Compare against original (most thorough)
phantom-pdf verify edited.pdf --original original.pdf

# JSON output
phantom-pdf verify edited.pdf --original original.pdf --json
```

**Checks performed:**
- Binary tool traces (MuPDF, PyMuPDF, iText, Ghostscript, etc.)
- Incremental save markers (`%%EOF` count)
- Metadata integrity (Producer, Creator, dates)
- Object count and structure
- Font integrity
- File size deviation
- Timestamp consistency
- macOS quarantine attributes

## Supported Features

| Feature | Support |
|---------|---------|
| CID fonts (Identity-H) | Full |
| TrueType subsets | Full |
| TJ arrays (word-spaced text) | Full |
| Tj simple strings | Full |
| FlateDecode streams | Full |
| Traditional xref tables | Full |
| JSON output mode | Full |
| Dry-run mode | Full |
| Forensic verification | Full |
| macOS timestamp + quarantine fix | Full |
| Cross-reference streams | Roadmap |
| AES/RC4 encrypted PDFs | Roadmap |
| Multi-page batch (`--all-pages`) | Roadmap |

## Understanding CID Fonts

Most modern PDFs use CID (Character Identifier) fonts with Identity-H encoding. Characters are stored as 2-byte CID values, not as ASCII/Unicode. Each font has:

- **ToUnicode CMap** — maps CID values to Unicode characters
- **W array** — defines glyph widths for each CID (in 1/1000 of font size)
- **Font subset** — only contains glyphs actually used in the document

The `phantom-pdf fonts` command decodes all of this, showing you exactly what characters are available and their precise widths.

## Width Considerations

When the new text is wider or narrower than the old text, PhantomPDF preserves the TJ spacing adjustments from the original:

- **Slightly wider text** (< 10pt difference): Extends into the right margin — usually invisible
- **Much wider text** (> 10pt): May visually overflow — the tool warns you
- **Narrower text**: Extra whitespace on the right — usually fine

For best results, keep replacement text similar in length to the original.

## Scripting & Automation

All commands support `--json` output for easy integration:

```bash
# Parse with jq
phantom-pdf inspect doc.pdf --json | jq '.spans[].text'

# Use in scripts
result=$(phantom-pdf replace doc.pdf --old "x" --new "y" --json)
echo "$result" | jq '.success'

# Quiet mode for pipelines
phantom-pdf replace doc.pdf --old "x" --new "y" --quiet
```

## Contributing

Contributions are welcome! Feel free to open issues or submit PRs.

```bash
# Development setup
git clone https://github.com/regitxx/PhantomPDF.git
cd PhantomPDF
pip install -e ".[dev]"

# Run tests
pytest
```

## License

MIT License. See [LICENSE](LICENSE).

---

<p align="center">
  <strong>Built by <a href="https://github.com/regitxx">regitxx</a></strong>
  <br><br>
  <a href="https://github.com/regitxx/PhantomPDF">
    <img src="https://img.shields.io/badge/⭐_Star_this_repo-d4af37?style=for-the-badge&labelColor=1a1a1a" alt="Star this repo"/>
  </a>
  <br><br>
  <sub>If PhantomPDF saved you time, please consider giving it a ⭐ on GitHub — it helps others discover the tool!</sub>
</p>
