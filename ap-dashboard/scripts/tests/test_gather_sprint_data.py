#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# ///

"""Tests for gather-sprint-data.py"""

import json
import subprocess
import sys
import tempfile
from pathlib import Path

SCRIPT_PATH = Path(__file__).parent.parent / "gather-sprint-data.py"

SAMPLE_SPRINT_STATUS = """
generated: 2026-04-24
project: test-project
story_location: stories

development_status:
  epic-1: in-progress
  1-1-first-story: done
  1-2-second-story: in-progress
  1-3-third-story: backlog
  epic-1-retrospective: optional

  epic-2: backlog
  2-1-fourth-story: backlog
  epic-2-retrospective: optional
"""


def run_script(*args: str) -> tuple[int, str, str]:
    """Run the script via uv run and return (exit_code, stdout, stderr)."""
    result = subprocess.run(
        ["uv", "run", str(SCRIPT_PATH), *args],
        capture_output=True,
        text=True,
    )
    return result.returncode, result.stdout, result.stderr


def test_basic_parsing():
    """Test that the script parses sprint-status.yaml correctly."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write(SAMPLE_SPRINT_STATUS)
        f.flush()

        exit_code, stdout, stderr = run_script(f.name)

        assert exit_code == 0, f"Script failed: {stderr}"
        data = json.loads(stdout)

        assert data["project"] == "test-project"
        assert len(data["epics"]) == 2

        epic1 = data["epics"][0]
        assert epic1["id"] == "epic-1"
        assert epic1["status"] == "in-progress"
        assert len(epic1["stories"]) == 3

        assert epic1["stories"][0]["id"] == "1-1-first-story"
        assert epic1["stories"][0]["status"] == "done"
        assert epic1["stories"][1]["status"] == "in-progress"
        assert epic1["stories"][2]["status"] == "backlog"

        summary = data["summary"]
        assert summary["total_stories"] == 4
        assert summary["done"] == 1
        assert summary["in_progress"] == 1
        assert summary["backlog"] == 2

    Path(f.name).unlink()


def test_missing_file():
    """Test that the script exits with code 2 for missing files."""
    exit_code, stdout, stderr = run_script("/nonexistent/path.yaml")
    assert exit_code == 2


def test_output_to_file():
    """Test -o flag writes to file."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write(SAMPLE_SPRINT_STATUS)
        f.flush()

        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as out:
            exit_code, stdout, stderr = run_script(f.name, "-o", out.name)
            assert exit_code == 0

            data = json.loads(Path(out.name).read_text())
            assert data["project"] == "test-project"

            Path(out.name).unlink()
    Path(f.name).unlink()


def test_help():
    """Test --help flag works."""
    exit_code, stdout, stderr = run_script("--help")
    assert exit_code == 0
    assert "sprint-status" in stdout.lower() or "sprint_status" in stdout.lower()


if __name__ == "__main__":
    tests = [test_basic_parsing, test_missing_file, test_output_to_file, test_help]
    passed = 0
    failed = 0
    for test in tests:
        try:
            test()
            print(f"  PASS: {test.__name__}")
            passed += 1
        except AssertionError as e:
            print(f"  FAIL: {test.__name__}: {e}")
            failed += 1
        except Exception as e:
            print(f"  ERROR: {test.__name__}: {e}")
            failed += 1

    print(f"\n{passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)
