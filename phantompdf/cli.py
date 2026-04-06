"""PhantomPDF CLI - Traceless PDF text editing."""

import argparse
import json
import os
import sys
import time

import fitz

from . import __version__
from .engine import surgical_replace
from .fonts import extract_fonts, print_font_table
from .cleaner import clean_pdf, scan_tool_traces
from .forensics import (
    fix_quarantine,
    fix_timestamps,
    print_verification,
    verify_pdf,
)


# Burgundy + gold color scheme
_BURG = "\033[38;2;128;0;32m"       # burgundy
_GOLD = "\033[38;2;212;175;55m"      # gold / champagne
_GRAY = "\033[38;2;100;100;100m"     # dark gray
_DIM = "\033[38;2;80;80;80m"         # dimmer gray
_WHITE = "\033[38;2;220;220;220m"    # soft white
_RST = "\033[0m"

BANNER = f"""{_BURG}
  ██████╗ ██╗  ██╗ █████╗ ███╗   ██╗████████╗ ██████╗ ███╗   ███╗
  ██╔══██╗██║  ██║██╔══██╗████╗  ██║╚══██╔══╝██╔═══██╗████╗ ████║
  ██████╔╝███████║███████║██╔██╗ ██║   ██║   ██║   ██║██╔████╔██║
  ██╔═══╝ ██╔══██║██╔══██║██║╚██╗██║   ██║   ██║   ██║██║╚██╔╝██║
  ██║     ██║  ██║██║  ██║██║ ╚████║   ██║   ╚██████╔╝██║ ╚═╝ ██║
  ╚═╝     ╚═╝  ╚═╝╚═╝  ╚═╝╚═╝  ╚═══╝   ╚═╝    ╚═════╝ ╚═╝     ╚═╝{_RST}
  {_GOLD}██████╗ ██████╗ ███████╗
  ██╔══██╗██╔══██╗██╔════╝
  ██████╔╝██║  ██║█████╗
  ██╔═══╝ ██╔══╝ ██╔══╝
  ██║     ██║    ██║
  ╚═╝     ╚═╝    ╚═╝{_RST}
  {_DIM}────────────────────────────────────────────{_RST}
  {_GRAY}traceless pdf editing{_RST}  {_DIM}v{__version__}{_RST}
  {_DIM}by{_RST} {_GOLD}regitxx{_RST}  {_DIM}• github.com/regitxx/PhantomPDF{_RST}
"""

BANNER_COMPACT = (
    f"  {_BURG}PhantomPDF{_RST} {_DIM}v{__version__}{_RST}"
    f"  {_GRAY}|{_RST}  {_DIM}by{_RST} {_GOLD}regitxx{_RST}"
    f"  {_GRAY}|{_RST}  {_DIM}traceless pdf editing{_RST}\n"
)


def _spinner(msg: str):
    """Simple inline status message."""
    sys.stdout.write(f"  {_GRAY}{'▸'}{_RST} {msg}...")
    sys.stdout.flush()


def _done(msg: str = "done"):
    """Complete a spinner line."""
    sys.stdout.write(f" {_GOLD}{msg}{_RST}\n")
    sys.stdout.flush()


def _ok(msg: str):
    print(f"  {_GOLD}[OK]{_RST} {msg}")


def _warn(msg: str):
    print(f"  {_BURG}[WARN]{_RST} {msg}")


def _error(msg: str):
    print(f"  {_BURG}[ERROR]{_RST} {msg}")


def _json_output(data: dict):
    """Print JSON output for scripting."""
    print(json.dumps(data, indent=2, default=str))


class OutputMode:
    """Control output formatting."""

    def __init__(self, quiet: bool = False, json_mode: bool = False):
        self.quiet = quiet
        self.json = json_mode
        self._data = {}

    def should_print(self) -> bool:
        return not self.quiet and not self.json


def cmd_inspect(args):
    """Inspect PDF text content with position and font info."""
    out = OutputMode(args.quiet, args.json)

    doc = fitz.open(args.file)
    page = doc[args.page]

    blocks = page.get_text("rawdict")

    if out.json:
        results = []
        for block in blocks["blocks"]:
            if "lines" not in block:
                continue
            for line in block["lines"]:
                for span in line["spans"]:
                    chars = span.get("chars", [])
                    text = "".join(c["c"] for c in chars) if chars else ""
                    if not text.strip():
                        continue
                    if args.search and args.search.lower() not in text.lower():
                        continue
                    results.append({
                        "text": text,
                        "font": span.get("font", "?"),
                        "size": span.get("size", 0),
                        "color": span.get("color", 0),
                        "bbox": list(span["bbox"]),
                    })
        _json_output({"page": args.page, "width": page.rect.width, "height": page.rect.height, "spans": results})
        doc.close()
        return

    if not out.quiet:
        print(BANNER_COMPACT)

    print(f"  Page {args.page} | Size: {page.rect.width:.0f} x {page.rect.height:.0f}")
    print(f"  {'─' * 50}")

    count = 0
    for block in blocks["blocks"]:
        if "lines" not in block:
            continue
        for line in block["lines"]:
            for span in line["spans"]:
                chars = span.get("chars", [])
                text = "".join(c["c"] for c in chars) if chars else ""
                if not text.strip():
                    continue

                if args.search and args.search.lower() not in text.lower():
                    continue

                bbox = span["bbox"]
                font = span.get("font", "?")
                size = span.get("size", 0)
                print(f"\n  {_GRAY}Font:{_RST} {font} {_GRAY}|{_RST} Size: {size} {_GRAY}|{_RST} Color: {span.get('color', 0)}")
                print(f"  {_GRAY}Bbox:{_RST} ({bbox[0]:.1f}, {bbox[1]:.1f}) -> ({bbox[2]:.1f}, {bbox[3]:.1f})")
                print(f"  {_GOLD}Text:{_RST} {text}")
                count += 1

                if args.verbose and chars:
                    print(f"  {_GRAY}Chars:{_RST}")
                    for c in chars:
                        w = c["bbox"][2] - c["bbox"][0]
                        print(f"    '{c['c']}' x={c['bbox'][0]:.1f} w={w:.1f}")

    print(f"\n  {_DIM}Found {count} text span(s){_RST}\n")
    doc.close()


def cmd_fonts(args):
    """Decode and display font information."""
    out = OutputMode(args.quiet, args.json)

    doc = fitz.open(args.file)
    font_map = extract_fonts(doc, args.page)

    if out.json:
        result = {}
        for ref_name, font in font_map.items():
            result[ref_name] = {
                "name": font.name,
                "xref": font.xref,
                "mapped_chars": len(font.char_to_cid),
                "glyph_widths": len(font.cid_widths),
                "default_width": font.default_width,
            }
            if args.check_text:
                missing = font.missing_glyphs(args.check_text)
                result[ref_name]["check_text"] = args.check_text
                result[ref_name]["missing_glyphs"] = missing
                result[ref_name]["can_encode"] = len(missing) == 0
        _json_output({"page": args.page, "fonts": result})
        doc.close()
        return

    if not out.quiet:
        print(BANNER_COMPACT)

    print(f"  Fonts on page {args.page} of {args.file}")
    print_font_table(font_map)

    if args.check_text:
        print(f"  {_GOLD}Glyph check for:{_RST} \"{args.check_text}\"")
        print(f"  {'─' * 40}")
        for ref_name, font in font_map.items():
            if not font.char_to_cid:
                print(f"  /{ref_name} ({font.name}): {_DIM}No CID mapping (simple font){_RST}")
                continue

            missing = font.missing_glyphs(args.check_text)
            if missing:
                _warn(f"/{ref_name} ({font.name}): MISSING glyphs: {missing}")
            else:
                width = font.text_width(args.check_text, 10.0)
                _ok(f"/{ref_name} ({font.name}): All glyphs available (width={width:.1f}pt at 10pt)")
        print()

    doc.close()


def cmd_replace(args):
    """Replace text in a PDF without leaving traces."""
    out = OutputMode(args.quiet, args.json)

    # Validate old/new pairs
    if len(args.old) != len(args.new):
        if out.json:
            _json_output({"error": "Each --old must have a matching --new"})
        else:
            _error("Each --old must have a matching --new")
        sys.exit(1)

    pairs = list(zip(args.old, args.new))

    # Determine output path
    output = args.output or args.file
    if output == args.file and not args.in_place:
        base = args.file.rsplit(".", 1)
        output = f"{base[0]}_phantom.{base[1]}" if len(base) > 1 else f"{args.file}_phantom"

    if not out.quiet and not out.json:
        print(BANNER)
        print(f"  {_GRAY}Input:{_RST}  {args.file}")
        print(f"  {_GRAY}Output:{_RST} {output}")
        if args.dry_run:
            print(f"  {_GOLD}Mode:{_RST}   DRY RUN (no files will be modified)")
        for old, new in pairs:
            print(f"  {_GRAY}Replace:{_RST} \"{old}\" {_GOLD}->{_RST} \"{new}\"")
        print()

    # Pre-flight: check glyph availability
    if not out.quiet and not out.json:
        _spinner("Checking font glyph availability")

    doc = fitz.open(args.file)
    font_map = extract_fonts(doc, args.page)
    doc.close()

    if not out.quiet and not out.json:
        _done(f"{len(font_map)} font(s) loaded")

    glyph_warnings = []
    for i, new_text in enumerate(args.new):
        can_encode = False
        for ref_name, font in font_map.items():
            if not font.char_to_cid:
                continue
            missing = font.missing_glyphs(new_text)
            if not missing:
                can_encode = True
                break

        if not can_encode:
            glyph_warnings.append({"index": i, "text": new_text})
            if not out.quiet and not out.json:
                _warn(f"Glyph check failed for replacement #{i+1}: \"{new_text}\"")
                print(f"    Proceeding anyway - the font may still support these characters.")
                print(f"    Run: phantom-pdf fonts <file> --check-text \"{new_text}\" for details.")
                print()

    # Dry run - stop here
    if args.dry_run:
        if out.json:
            _json_output({
                "dry_run": True,
                "pairs": [{"old": o, "new": n} for o, n in pairs],
                "glyph_warnings": glyph_warnings,
                "output_path": output,
            })
        else:
            _ok("Dry run complete - no files modified")
            if glyph_warnings:
                _warn(f"{len(glyph_warnings)} glyph warning(s) - review before running for real")
            else:
                print(f"  {_GOLD}All checks passed.{_RST} Remove --dry-run to apply.\n")
        return

    # Perform replacements
    current_input = args.file
    results = []
    total = len(pairs)

    for i, (old, new) in enumerate(pairs):
        if i == 0:
            current_output = output
        else:
            current_input = output
            current_output = output

        if not out.quiet and not out.json:
            _spinner(f"Replacing [{i+1}/{total}] \"{old}\" -> \"{new}\"")

        t0 = time.monotonic()
        result = surgical_replace(current_input, current_output, old, new, args.page)
        elapsed = time.monotonic() - t0

        if not result["replaced"]:
            if out.json:
                _json_output({"error": f"Could not find \"{old}\" in page {args.page}"})
            else:
                _done("FAILED")
                _error(f"Could not find \"{old}\" in page {args.page}")
                print(f"    Run: phantom-pdf inspect <file> --search \"{old}\" to locate it.")
            sys.exit(1)

        result["elapsed"] = elapsed
        results.append(result)

        if not out.quiet and not out.json:
            _done(f"{elapsed:.2f}s")
            print(f"    {_GRAY}Font:{_RST} {result['font_used']} (/{result['font_ref']})")
            print(f"    {_GRAY}Size:{_RST} {result['size_diff']:+d} bytes {_GRAY}|{_RST} Width: {result['width_change']:+.1f}pt")

            if abs(result["width_change"]) > 10:
                _warn(f"Width changed by {abs(result['width_change']):.1f}pt - may be noticeable")

    if not out.quiet and not out.json:
        print(f"\n  {_GOLD}{'━' * 50}{_RST}")
        _ok(f"All {total} replacement(s) applied successfully")

    # Fix timestamps
    ts_result = {"fixed": [], "errors": []}
    if not args.no_timestamps:
        if not out.quiet and not out.json:
            _spinner("Fixing file timestamps")
        ts_result = fix_timestamps(output)
        if not out.quiet and not out.json:
            _done()
            for msg in ts_result["fixed"]:
                print(f"    {_DIM}{msg}{_RST}")
            for msg in ts_result["errors"]:
                _warn(msg)

    # Fix quarantine (macOS)
    q_result = {"fixed": [], "errors": [], "skipped": []}
    if not args.no_quarantine:
        if not out.quiet and not out.json:
            _spinner("Setting quarantine attributes")
        q_result = fix_quarantine(output, args.quarantine_app)
        if not out.quiet and not out.json:
            if q_result["fixed"]:
                _done()
                for msg in q_result["fixed"]:
                    print(f"    {_DIM}{msg}{_RST}")
            elif q_result["skipped"]:
                _done("skipped")
            else:
                _done()

    # Auto-verify
    if args.verify:
        if not out.quiet and not out.json:
            print(f"\n  {_GOLD}{'━' * 50}{_RST}")
            _spinner("Running forensic verification")
            print()
        findings = verify_pdf(output, args.file)
        if out.json:
            pass  # included below
        elif not out.quiet:
            print_verification(findings)
    else:
        findings = None
        if not out.quiet and not out.json:
            print(f"\n  {_DIM}Tip: add --verify to run forensic audit automatically{_RST}")

    # JSON output
    if out.json:
        json_data = {
            "success": True,
            "input": args.file,
            "output": output,
            "replacements": [],
            "timestamps": ts_result,
            "quarantine": q_result,
        }
        for i, (pair, res) in enumerate(zip(pairs, results)):
            json_data["replacements"].append({
                "old": pair[0],
                "new": pair[1],
                "font": res["font_used"],
                "font_ref": res["font_ref"],
                "size_diff": res["size_diff"],
                "width_change": res["width_change"],
                "elapsed": res["elapsed"],
            })
        if findings:
            json_data["verification"] = findings
        _json_output(json_data)
    elif not out.quiet:
        print()


def cmd_clean(args):
    """Strip forensic traces from a PDF edited by other tools."""
    out = OutputMode(args.quiet, args.json)

    # Determine output path
    output = args.output or args.file
    if output == args.file and not args.in_place:
        base = args.file.rsplit(".", 1)
        output = f"{base[0]}_clean.{base[1]}" if len(base) > 1 else f"{args.file}_clean"

    if not out.quiet and not out.json:
        print(BANNER)
        print(f"  {_GRAY}Input:{_RST}  {args.file}")
        print(f"  {_GRAY}Output:{_RST} {output}")
        print()

    # Scan first to show what we found
    if not out.quiet and not out.json:
        _spinner("Scanning for forensic traces")

    with open(args.file, "rb") as f:
        raw = f.read()

    traces = scan_tool_traces(raw)
    eof_count = raw.count(b"%%EOF")

    if not out.quiet and not out.json:
        _done()
        if traces:
            for t in traces:
                print(f"    {_BURG}Found:{_RST} {t['description']} ({t['count']}x)")
        else:
            print(f"    {_DIM}No tool watermarks found{_RST}")

        if eof_count > 1:
            print(f"    {_BURG}Found:{_RST} {eof_count} %%EOF markers (incremental saves)")
        else:
            print(f"    {_DIM}Single %%EOF (clean){_RST}")
        print()

    # Dry run
    if args.dry_run:
        if out.json:
            _json_output({
                "dry_run": True,
                "traces": [{"marker": t["marker"].decode(), "count": t["count"], "description": t["description"]} for t in traces],
                "eof_count": eof_count,
                "output_path": output,
            })
        else:
            _ok("Dry run complete — no files modified")
            if traces or eof_count > 1:
                print(f"  {_GOLD}Traces found.{_RST} Remove --dry-run to clean.\n")
            else:
                print(f"  {_DIM}File looks clean already.{_RST}\n")
        return

    # Run the full clean pipeline
    if not out.quiet and not out.json:
        _spinner("Cleaning")

    report = clean_pdf(
        args.file,
        output,
        strip_traces=not args.no_strip,
        flatten=not args.no_flatten,
        reset_metadata=not args.no_metadata,
        producer_override=args.producer,
        creator_override=args.creator,
    )

    if not out.quiet and not out.json:
        _done()
        print()
        for step in report["steps"]:
            print(f"  {_GOLD}{step['name']}{_RST}")
            for action in step["actions"]:
                print(f"    {_GRAY}▸{_RST} {action}")
            print()

        diff = report["size_diff"]
        print(f"  {_GRAY}Size:{_RST} {report['input_size']} -> {report['output_size']} bytes ({diff:+d})")
        print(f"\n  {_GOLD}{'━' * 50}{_RST}")
        _ok("Forensic cleanup complete")

    # Fix timestamps
    if not args.no_timestamps:
        if not out.quiet and not out.json:
            _spinner("Fixing file timestamps")
        ts_result = fix_timestamps(output)
        if not out.quiet and not out.json:
            _done()
            for msg in ts_result["fixed"]:
                print(f"    {_DIM}{msg}{_RST}")

    # Fix quarantine
    if not args.no_quarantine:
        if not out.quiet and not out.json:
            _spinner("Setting quarantine attributes")
        q_result = fix_quarantine(output, args.quarantine_app)
        if not out.quiet and not out.json:
            if q_result["fixed"]:
                _done()
            elif q_result.get("skipped"):
                _done("skipped")
            else:
                _done()

    # Auto-verify (standalone — not against original, since we intentionally changed it)
    if args.verify:
        if not out.quiet and not out.json:
            print(f"\n  {_GOLD}{'━' * 50}{_RST}")
        findings = verify_pdf(output)
        if not out.quiet and not out.json:
            print_verification(findings)

    if out.json:
        report["traces_found"] = [
            {"marker": t["marker"].decode(), "count": t["count"], "description": t["description"]}
            for t in traces
        ]
        _json_output(report)
    elif not out.quiet:
        if not args.verify:
            print(f"\n  {_DIM}Tip: add --verify to run forensic audit on the result{_RST}")
        print()


def cmd_verify(args):
    """Run forensic verification on a PDF."""
    out = OutputMode(args.quiet, args.json)

    if out.json:
        findings = verify_pdf(args.file, args.original)
        _json_output(findings)
        return

    if not out.quiet:
        print(BANNER_COMPACT)

    print(f"  {_GRAY}Verifying:{_RST} {args.file}")
    if args.original:
        print(f"  {_GRAY}Against:{_RST}   {args.original}")

    findings = verify_pdf(args.file, args.original)
    print_verification(findings)


def main():
    parser = argparse.ArgumentParser(
        prog="phantom-pdf",
        description=f"{_BURG}PhantomPDF{_RST} — Traceless PDF text editing via surgical binary replacement",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=f"""{BANNER}
{_GOLD}examples:{_RST}

  {_GRAY}# Inspect text in a PDF{_RST}
  phantom-pdf inspect document.pdf --search "1000"

  {_GRAY}# Show font details and check glyph support{_RST}
  phantom-pdf fonts document.pdf --check-text "replacement text"

  {_GRAY}# Dry run - preview without modifying{_RST}
  phantom-pdf replace doc.pdf --old "100,00" --new "999,00" --dry-run

  {_GRAY}# Single replacement with verification{_RST}
  phantom-pdf replace doc.pdf --old "100,00" --new "999,00" --verify

  {_GRAY}# Multiple replacements in one pass{_RST}
  phantom-pdf replace doc.pdf --old "100" --new "999" --old "old" --new "new" --verify

  {_GRAY}# JSON output for scripting{_RST}
  phantom-pdf replace doc.pdf --old "100" --new "999" --json

  {_GRAY}# Clean traces left by other tools{_RST}
  phantom-pdf clean edited.pdf --verify

  {_GRAY}# Forensic verification{_RST}
  phantom-pdf verify edited.pdf --original original.pdf

{_DIM}by regitxx • https://github.com/regitxx/PhantomPDF{_RST}
{_DIM}If this tool helped you, please give it a star on GitHub!{_RST}
        """,
    )
    parser.add_argument("-V", "--version", action="version",
                        version=f"{_BURG}phantom-pdf{_RST} {_GOLD}{__version__}{_RST} {_DIM}by regitxx{_RST}")

    subparsers = parser.add_subparsers(dest="command", required=True)

    # === inspect ===
    p_inspect = subparsers.add_parser(
        "inspect", help="Inspect PDF text with font and position details"
    )
    p_inspect.add_argument("file", help="PDF file to inspect")
    p_inspect.add_argument("-p", "--page", type=int, default=0, help="Page number (0-indexed, default: 0)")
    p_inspect.add_argument("-s", "--search", help="Filter text containing this string (case-insensitive)")
    p_inspect.add_argument("-v", "--verbose", action="store_true", help="Show character-level details")
    p_inspect.add_argument("-q", "--quiet", action="store_true", help="Suppress banner and decorations")
    p_inspect.add_argument("--json", action="store_true", help="Output as JSON (for scripting)")

    # === fonts ===
    p_fonts = subparsers.add_parser(
        "fonts", help="Decode font tables, CID mappings, and glyph widths"
    )
    p_fonts.add_argument("file", help="PDF file to analyze")
    p_fonts.add_argument("-p", "--page", type=int, default=0, help="Page number (0-indexed)")
    p_fonts.add_argument("-c", "--check-text", help="Check if fonts have all glyphs for this text")
    p_fonts.add_argument("-q", "--quiet", action="store_true", help="Suppress banner and decorations")
    p_fonts.add_argument("--json", action="store_true", help="Output as JSON (for scripting)")

    # === replace ===
    p_replace = subparsers.add_parser(
        "replace",
        help="Replace text in a PDF without leaving traces",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=f"""
{_GOLD}examples:{_RST}

  {_GRAY}# Preview what would change{_RST}
  phantom-pdf replace doc.pdf --old "100" --new "999" --dry-run

  {_GRAY}# Single replacement{_RST}
  phantom-pdf replace doc.pdf --old "100" --new "999"

  {_GRAY}# Multiple replacements (repeat --old/--new pairs){_RST}
  phantom-pdf replace doc.pdf \\
    --old "253 028,42" --new "5 021 028,42" \\
    --old "two hundred" --new "five million" \\
    --verify
        """,
    )
    p_replace.add_argument("file", help="PDF file to edit")
    p_replace.add_argument("--old", required=True, action="append", help="Text to find (repeatable)")
    p_replace.add_argument("--new", required=True, action="append", help="Replacement text (repeatable, must match --old count)")
    p_replace.add_argument("-o", "--output", help="Output path (default: <file>_phantom.pdf)")
    p_replace.add_argument("-p", "--page", type=int, default=0, help="Page number (0-indexed)")
    p_replace.add_argument("--in-place", action="store_true", help="Overwrite the input file")
    p_replace.add_argument("--verify", action="store_true", help="Run forensic verification after replacing")
    p_replace.add_argument("--dry-run", action="store_true", help="Preview changes without modifying any files")
    p_replace.add_argument("--no-timestamps", action="store_true", help="Skip timestamp fixing")
    p_replace.add_argument("--no-quarantine", action="store_true", help="Skip quarantine attribute fixing")
    p_replace.add_argument("--quarantine-app", default="com.google.Chrome",
                          help="App bundle ID for quarantine (default: Chrome)")
    p_replace.add_argument("-q", "--quiet", action="store_true", help="Suppress all output except errors")
    p_replace.add_argument("--json", action="store_true", help="Output results as JSON")

    # === clean ===
    p_clean = subparsers.add_parser(
        "clean",
        help="Strip forensic traces left by other PDF tools",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=f"""
{_GOLD}examples:{_RST}

  {_GRAY}# Scan and preview what would be cleaned{_RST}
  phantom-pdf clean edited.pdf --dry-run

  {_GRAY}# Full cleanup — strip traces, flatten, reset metadata{_RST}
  phantom-pdf clean edited.pdf --verify

  {_GRAY}# Override Producer metadata to match original tool{_RST}
  phantom-pdf clean edited.pdf --producer "macOS Quartz PDFContext"

  {_GRAY}# Skip flattening (keep incremental saves){_RST}
  phantom-pdf clean edited.pdf --no-flatten
        """,
    )
    p_clean.add_argument("file", help="PDF file to clean")
    p_clean.add_argument("-o", "--output", help="Output path (default: <file>_clean.pdf)")
    p_clean.add_argument("--in-place", action="store_true", help="Overwrite the input file")
    p_clean.add_argument("--dry-run", action="store_true", help="Scan and report without modifying")
    p_clean.add_argument("--verify", action="store_true", help="Run forensic verification after cleaning")
    p_clean.add_argument("--producer", help="Override Producer metadata field")
    p_clean.add_argument("--creator", help="Override Creator metadata field")
    p_clean.add_argument("--no-strip", action="store_true", help="Skip stripping tool watermarks")
    p_clean.add_argument("--no-flatten", action="store_true", help="Skip flattening incremental saves")
    p_clean.add_argument("--no-metadata", action="store_true", help="Skip metadata cleanup")
    p_clean.add_argument("--no-timestamps", action="store_true", help="Skip timestamp fixing")
    p_clean.add_argument("--no-quarantine", action="store_true", help="Skip quarantine attribute fixing")
    p_clean.add_argument("--quarantine-app", default="com.google.Chrome",
                        help="App bundle ID for quarantine (default: Chrome)")
    p_clean.add_argument("-q", "--quiet", action="store_true", help="Suppress all output except errors")
    p_clean.add_argument("--json", action="store_true", help="Output as JSON")

    # === verify ===
    p_verify = subparsers.add_parser(
        "verify", help="Run forensic audit on a PDF file"
    )
    p_verify.add_argument("file", help="PDF file to verify")
    p_verify.add_argument("--original", help="Original file to compare against")
    p_verify.add_argument("-q", "--quiet", action="store_true", help="Suppress banner")
    p_verify.add_argument("--json", action="store_true", help="Output as JSON")

    # Show banner when no args
    if len(sys.argv) == 1:
        print(BANNER)
        parser.print_help()
        sys.exit(0)

    args = parser.parse_args()

    commands = {
        "inspect": cmd_inspect,
        "fonts": cmd_fonts,
        "replace": cmd_replace,
        "clean": cmd_clean,
        "verify": cmd_verify,
    }

    try:
        commands[args.command](args)
    except FileNotFoundError as e:
        if hasattr(args, 'json') and args.json:
            _json_output({"error": str(e)})
        else:
            _error(f"File not found: {e.filename}")
        sys.exit(1)
    except fitz.FileDataError:
        if hasattr(args, 'json') and args.json:
            _json_output({"error": "Not a valid PDF file"})
        else:
            _error("Not a valid PDF file or file is corrupted")
        sys.exit(1)
    except ValueError as e:
        if hasattr(args, 'json') and args.json:
            _json_output({"error": str(e)})
        else:
            _error(str(e))
        if "--verbose" in sys.argv or "-v" in sys.argv:
            raise
        sys.exit(1)
    except Exception as e:
        if hasattr(args, 'json') and args.json:
            _json_output({"error": str(e)})
        else:
            _error(f"Unexpected error: {e}")
        if "--verbose" in sys.argv or "-v" in sys.argv:
            raise
        sys.exit(1)


if __name__ == "__main__":
    main()
