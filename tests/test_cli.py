"""Tests for the CLI interface."""

import subprocess
import sys

import pytest


def run_cli(*args):
    """Run phantom-pdf CLI and return result."""
    result = subprocess.run(
        [sys.executable, "-m", "phantompdf.cli", *args],
        capture_output=True,
        text=True,
    )
    return result


class TestCLIBasics:
    """Test basic CLI behavior."""

    def test_no_args_shows_help(self):
        result = run_cli()
        assert result.returncode == 0
        assert "PhantomPDF" in result.stdout or "phantom-pdf" in result.stdout

    def test_version_flag(self):
        result = run_cli("--version")
        assert result.returncode == 0
        assert "1.0.0" in result.stdout

    def test_help_flag(self):
        result = run_cli("--help")
        assert result.returncode == 0
        assert "inspect" in result.stdout
        assert "replace" in result.stdout
        assert "verify" in result.stdout
        assert "fonts" in result.stdout

    def test_inspect_help(self):
        result = run_cli("inspect", "--help")
        assert result.returncode == 0
        assert "--search" in result.stdout
        assert "--json" in result.stdout

    def test_replace_help(self):
        result = run_cli("replace", "--help")
        assert result.returncode == 0
        assert "--dry-run" in result.stdout
        assert "--json" in result.stdout
        assert "--verify" in result.stdout

    def test_verify_help(self):
        result = run_cli("verify", "--help")
        assert result.returncode == 0
        assert "--original" in result.stdout
        assert "--json" in result.stdout

    def test_inspect_missing_file(self):
        result = run_cli("inspect", "nonexistent.pdf")
        assert result.returncode != 0

    def test_replace_missing_file(self):
        result = run_cli("replace", "nonexistent.pdf", "--old", "x", "--new", "y")
        assert result.returncode != 0

    def test_replace_mismatched_pairs(self):
        result = run_cli("replace", "test.pdf", "--old", "a", "--old", "b", "--new", "c")
        assert result.returncode != 0
