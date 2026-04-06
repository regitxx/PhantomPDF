"""Forensic cleanup and verification - timestamps, quarantine, audit."""

import os
import platform
import re
import subprocess
import sys
from datetime import datetime, timezone, timedelta

import fitz


def parse_pdf_date(date_str: str) -> datetime | None:
    """Parse a PDF date string like D:20260403085752+03'00' into a datetime."""
    if not date_str:
        return None

    # Strip the D: prefix
    date_str = date_str.lstrip("D:")

    # Parse base datetime
    try:
        base = datetime.strptime(date_str[:14], "%Y%m%d%H%M%S")
    except (ValueError, IndexError):
        try:
            base = datetime.strptime(date_str[:12], "%Y%m%d%H%M")
        except (ValueError, IndexError):
            return None

    # Parse timezone offset
    tz_str = date_str[14:].replace("'", "")
    if tz_str:
        sign = 1 if tz_str[0] == "+" else -1
        tz_match = re.match(r"[+-](\d{2})(\d{2})?", tz_str)
        if tz_match:
            hours = int(tz_match.group(1))
            minutes = int(tz_match.group(2) or "0")
            tz = timezone(timedelta(hours=sign * hours, minutes=sign * minutes))
            base = base.replace(tzinfo=tz)

    return base


def fix_timestamps(pdf_path: str, reference_date: datetime | None = None) -> dict:
    """Fix file timestamps to match the PDF's internal creation date.

    If reference_date is None, reads it from the PDF metadata.
    Returns dict with what was done.
    """
    result = {"fixed": [], "errors": []}

    if reference_date is None:
        doc = fitz.open(pdf_path)
        creation_date = doc.metadata.get("creationDate", "")
        doc.close()
        reference_date = parse_pdf_date(creation_date)

    if reference_date is None:
        result["errors"].append("Could not determine reference date from PDF")
        return result

    # Convert to local time for file timestamps
    local_dt = reference_date.astimezone()
    timestamp = local_dt.timestamp()

    # Add a few seconds for "download delay"
    mod_timestamp = timestamp + 15  # 15 seconds after creation

    # Set modification time
    os.utime(pdf_path, (mod_timestamp, mod_timestamp))
    result["fixed"].append(f"Modified time set to {datetime.fromtimestamp(mod_timestamp)}")

    # Set creation time (macOS only)
    if platform.system() == "Darwin":
        creation_str = datetime.fromtimestamp(timestamp + 3).strftime("%m/%d/%Y %H:%M:%S")
        try:
            subprocess.run(
                ["SetFile", "-d", creation_str, pdf_path],
                check=True,
                capture_output=True,
            )
            result["fixed"].append(f"Creation time set to {creation_str}")
        except (subprocess.CalledProcessError, FileNotFoundError) as e:
            result["errors"].append(f"Could not set creation time: {e}")

    return result


def fix_quarantine(pdf_path: str, source_app: str = "com.google.Chrome") -> dict:
    """Fix macOS quarantine attribute to look like a browser download.

    Args:
        source_app: Bundle ID of the "source" app (default: Chrome)
    """
    result = {"fixed": [], "errors": [], "skipped": []}

    if platform.system() != "Darwin":
        result["skipped"].append("Quarantine attributes only apply to macOS")
        return result

    # Get the file's modification time for the quarantine timestamp
    stat = os.stat(pdf_path)
    ts_hex = f"{int(stat.st_mtime):08x}"

    # 0081 = downloaded from internet
    quarantine_value = f"0081;{ts_hex};{source_app};"

    try:
        subprocess.run(
            ["xattr", "-w", "com.apple.quarantine", quarantine_value, pdf_path],
            check=True,
            capture_output=True,
        )
        result["fixed"].append(f"Quarantine set to: {quarantine_value}")
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        result["errors"].append(f"Could not set quarantine: {e}")

    return result


def verify_pdf(pdf_path: str, original_path: str | None = None) -> dict:
    """Run forensic verification on a PDF file.

    If original_path is provided, compares against it.
    Returns dict with all findings.
    """
    findings = {
        "clean": True,
        "checks": [],
    }

    with open(pdf_path, "rb") as f:
        data = f.read()

    doc = fitz.open(pdf_path)

    # 1. Check for tool traces in binary
    tool_markers = [
        b"MuPDF", b"PyMuPDF", b"pymupdf", b"fitz",
        b"QPDF", b"qpdf", b"pdftk", b"Ghostscript",
        b"iText", b"cairo", b"Acrobat Distiller",
        b"phantom-pdf",  # our own tool
    ]

    traces_found = []
    for marker in tool_markers:
        if marker in data:
            traces_found.append(marker.decode("ascii", errors="replace"))

    if traces_found:
        # Check if these exist in original too
        if original_path:
            with open(original_path, "rb") as f:
                orig_data = f.read()
            new_traces = [t for t in traces_found if t.encode() not in orig_data]
            if new_traces:
                findings["clean"] = False
                findings["checks"].append(
                    {"name": "Tool traces", "status": "FAIL", "detail": f"New traces: {new_traces}"}
                )
            else:
                findings["checks"].append(
                    {"name": "Tool traces", "status": "PASS", "detail": "All traces from original"}
                )
        else:
            findings["checks"].append(
                {"name": "Tool traces", "status": "INFO", "detail": f"Found: {traces_found}"}
            )
    else:
        findings["checks"].append(
            {"name": "Tool traces", "status": "PASS", "detail": "None found"}
        )

    # 2. Check %%EOF count (incremental saves)
    eof_count = data.count(b"%%EOF")
    if eof_count > 1:
        findings["clean"] = False
        findings["checks"].append(
            {"name": "Incremental saves", "status": "FAIL", "detail": f"{eof_count} %%EOF markers (expected 1)"}
        )
    else:
        findings["checks"].append(
            {"name": "Incremental saves", "status": "PASS", "detail": "Single %%EOF"}
        )

    # 3. Compare with original if available
    if original_path:
        with open(original_path, "rb") as f:
            orig_data = f.read()

        orig_doc = fitz.open(original_path)

        # Header match
        if data[:20] == orig_data[:20]:
            findings["checks"].append({"name": "PDF header", "status": "PASS", "detail": "Identical"})
        else:
            findings["clean"] = False
            findings["checks"].append({"name": "PDF header", "status": "FAIL", "detail": "Modified"})

        # Metadata match
        meta_match = doc.metadata == orig_doc.metadata
        if meta_match:
            findings["checks"].append({"name": "Metadata", "status": "PASS", "detail": "Identical"})
        else:
            findings["clean"] = False
            changed = {k for k in doc.metadata if doc.metadata[k] != orig_doc.metadata.get(k)}
            findings["checks"].append(
                {"name": "Metadata", "status": "FAIL", "detail": f"Changed fields: {changed}"}
            )

        # Object count
        if doc.xref_length() == orig_doc.xref_length():
            findings["checks"].append(
                {"name": "Object count", "status": "PASS", "detail": f"{doc.xref_length()} objects"}
            )
        else:
            findings["clean"] = False
            findings["checks"].append(
                {"name": "Object count", "status": "FAIL",
                 "detail": f"Orig: {orig_doc.xref_length()}, Edit: {doc.xref_length()}"}
            )

        # File size
        size_diff = len(data) - len(orig_data)
        pct = abs(size_diff) / len(orig_data) * 100
        status = "PASS" if pct < 1 else "WARN"
        findings["checks"].append(
            {"name": "File size", "status": status,
             "detail": f"{len(data)} bytes ({size_diff:+d}, {pct:.2f}%)"}
        )

        # Font integrity
        try:
            orig_fonts = orig_doc[0].get_fonts(full=True)
            edit_fonts = doc[0].get_fonts(full=True)
            if orig_fonts == edit_fonts:
                findings["checks"].append(
                    {"name": "Font integrity", "status": "PASS", "detail": f"{len(edit_fonts)} fonts identical"}
                )
            else:
                findings["clean"] = False
                findings["checks"].append(
                    {"name": "Font integrity", "status": "FAIL", "detail": "Font definitions changed"}
                )
        except Exception:
            pass

        orig_doc.close()
    else:
        # Basic checks without original
        findings["checks"].append(
            {"name": "Producer", "status": "INFO", "detail": doc.metadata.get("producer", "N/A")}
        )
        findings["checks"].append(
            {"name": "Creator", "status": "INFO", "detail": doc.metadata.get("creator", "N/A")}
        )
        findings["checks"].append(
            {"name": "File size", "status": "INFO", "detail": f"{len(data)} bytes"}
        )

    # 4. Timestamp consistency
    creation_date = parse_pdf_date(doc.metadata.get("creationDate", ""))
    mod_date = parse_pdf_date(doc.metadata.get("modDate", ""))
    stat = os.stat(pdf_path)
    file_mtime = datetime.fromtimestamp(stat.st_mtime)

    if creation_date and mod_date:
        if creation_date == mod_date:
            findings["checks"].append(
                {"name": "PDF dates", "status": "PASS", "detail": "Creation == ModDate (never re-saved)"}
            )
        else:
            findings["checks"].append(
                {"name": "PDF dates", "status": "WARN", "detail": f"Creation != ModDate"}
            )

    # 5. macOS quarantine
    if platform.system() == "Darwin":
        try:
            result = subprocess.run(
                ["xattr", "-p", "com.apple.quarantine", pdf_path],
                capture_output=True, text=True,
            )
            if result.returncode == 0:
                qval = result.stdout.strip()
                if qval.startswith("0081") or qval.startswith("0001"):
                    findings["checks"].append(
                        {"name": "Quarantine", "status": "PASS", "detail": f"Downloaded flag: {qval}"}
                    )
                elif qval.startswith("0082"):
                    findings["checks"].append(
                        {"name": "Quarantine", "status": "WARN", "detail": f"Locally modified flag: {qval}"}
                    )
                else:
                    findings["checks"].append(
                        {"name": "Quarantine", "status": "INFO", "detail": qval}
                    )
            else:
                findings["checks"].append(
                    {"name": "Quarantine", "status": "INFO", "detail": "No quarantine attribute"}
                )
        except FileNotFoundError:
            pass

    doc.close()
    return findings


def print_verification(findings: dict) -> None:
    """Pretty-print verification results."""
    _BURG = "\033[38;2;128;0;32m"
    _GOLD = "\033[38;2;212;175;55m"
    _GRAY = "\033[38;2;100;100;100m"
    _DIM = "\033[38;2;80;80;80m"
    _GREEN = "\033[38;2;80;180;80m"
    _RED = "\033[38;2;200;60;60m"
    _YELLOW = "\033[38;2;200;180;60m"
    _BLUE = "\033[38;2;100;140;200m"
    _RST = "\033[0m"

    status_symbols = {
        "PASS": f"{_GREEN}[PASS]{_RST}",
        "FAIL": f"{_RED}[FAIL]{_RST}",
        "WARN": f"{_YELLOW}[WARN]{_RST}",
        "INFO": f"{_BLUE}[INFO]{_RST}",
    }

    print(f"\n  {_GOLD}Forensic Verification Report{_RST}")
    print(f"  {_DIM}{'━' * 50}{_RST}")

    passed = sum(1 for c in findings["checks"] if c["status"] == "PASS")
    total = len(findings["checks"])

    for check in findings["checks"]:
        symbol = status_symbols.get(check["status"], check["status"])
        print(f"  {symbol} {_GRAY}{check['name']}:{_RST} {check['detail']}")

    print(f"  {_DIM}{'━' * 50}{_RST}")
    print(f"  {_GRAY}Checks: {passed}/{total} passed{_RST}")
    if findings["clean"]:
        print(f"  {_GREEN}VERDICT: CLEAN — No detectable traces{_RST}")
    else:
        print(f"  {_RED}VERDICT: TRACES DETECTED — See failures above{_RST}")
    print()
