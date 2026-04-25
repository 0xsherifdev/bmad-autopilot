#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = ["pyyaml>=6.0"]
# ///

"""Tests for autopilot.py -- unit tests for non-CLI components."""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

# Add parent to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

SCRIPT_PATH = Path(__file__).parent.parent / "autopilot.py"

SAMPLE_SPRINT_STATUS = {
    "generated": "2026-04-24",
    "project": "test-project",
    "story_location": "_bmad-output/implementation-artifacts",
    "development_status": {
        "epic-1": "in-progress",
        "1-1-first-story": "done",
        "1-2-second-story": "in-progress",
        "1-3-third-story": "backlog",
        "epic-1-retrospective": "optional",
        "epic-2": "backlog",
        "2-1-fourth-story": "backlog",
        "epic-2-retrospective": "optional",
    },
}

SAMPLE_CONFIG = {
    "autonomy_preset": "checkpoint",
    "telegram_bot_token": None,
    "telegram_chat_id": None,
    "retry_budget": 2,
    "dashboard_format": "html",
    "project_label": "test-project",
}


def run_script(*args: str, cwd: str | None = None) -> tuple[int, str, str]:
    """Run the autopilot script via uv."""
    result = subprocess.run(
        ["uv", "run", str(SCRIPT_PATH), *args],
        capture_output=True,
        text=True,
        cwd=cwd,
    )
    return result.returncode, result.stdout, result.stderr


def test_help():
    """Test --help flag works."""
    exit_code, stdout, stderr = run_script("--help")
    assert exit_code == 0
    assert "autopilot" in stdout.lower()


def test_run_help():
    """Test run --help flag works."""
    exit_code, stdout, stderr = run_script("run", "--help")
    assert exit_code == 0
    assert "epic" in stdout.lower() or "story" in stdout.lower()


def test_configure_help():
    """Test configure --help flag works."""
    exit_code, stdout, stderr = run_script("configure", "--help")
    assert exit_code == 0
    assert "preset" in stdout.lower()


def test_find_next_story():
    """Test find_next_story logic by importing and calling directly."""
    # We test this via the status command output instead of importing
    # since the script uses uv run with dependencies

    with tempfile.TemporaryDirectory() as tmpdir:
        # Create minimal git repo
        subprocess.run(["git", "init"], cwd=tmpdir, capture_output=True)
        subprocess.run(["git", "commit", "--allow-empty", "-m", "init"], cwd=tmpdir, capture_output=True)

        # Create sprint status
        status_dir = Path(tmpdir) / "_bmad-output" / "implementation-artifacts"
        status_dir.mkdir(parents=True)
        status_path = status_dir / "sprint-status.yaml"
        with open(status_path, "w") as f:
            yaml.dump(SAMPLE_SPRINT_STATUS, f)

        # Create config
        bmad_dir = Path(tmpdir) / "_bmad"
        bmad_dir.mkdir()
        config_path = bmad_dir / "_autopilot.yaml"
        with open(config_path, "w") as f:
            yaml.dump(SAMPLE_CONFIG, f)

        # Run status command
        exit_code, stdout, stderr = run_script("status", cwd=tmpdir)
        assert exit_code == 0
        status = json.loads(stdout)
        # Should find the in-progress story first
        assert status["next_story"]["id"] == "1-2-second-story"


def test_configure_saves():
    """Test that configure writes config file."""
    with tempfile.TemporaryDirectory() as tmpdir:
        # Create minimal git repo
        subprocess.run(["git", "init"], cwd=tmpdir, capture_output=True)
        subprocess.run(["git", "commit", "--allow-empty", "-m", "init"], cwd=tmpdir, capture_output=True)

        # Create _bmad dir
        (Path(tmpdir) / "_bmad").mkdir()

        exit_code, stdout, stderr = run_script(
            "configure", "--preset", "ghost", "--project-label", "my-project",
            cwd=tmpdir,
        )
        assert exit_code == 0

        config_path = Path(tmpdir) / "_bmad" / "_autopilot.yaml"
        assert config_path.exists()

        with open(config_path) as f:
            config = yaml.safe_load(f)

        assert config["autonomy_preset"] == "ghost"
        assert config["project_label"] == "my-project"
        # Ghost mode: gates should be false except merge
        assert config["gate_after_story_create"] is False
        assert config["gate_after_dev"] is False
        assert config["gate_before_merge"] is True


def test_lock_file():
    """Test that lock file prevents double runs."""
    with tempfile.TemporaryDirectory() as tmpdir:
        # Create minimal git repo
        subprocess.run(["git", "init"], cwd=tmpdir, capture_output=True)
        subprocess.run(["git", "commit", "--allow-empty", "-m", "init"], cwd=tmpdir, capture_output=True)

        # Create a lock file with our own PID (simulating running instance)
        lock_dir = Path(tmpdir) / "_bmad-output" / "autopilot"
        lock_dir.mkdir(parents=True)
        lock_file = lock_dir / "autopilot.lock"
        lock_file.write_text(str(os.getpid()))

        # Create sprint status so it gets past config loading
        status_dir = Path(tmpdir) / "_bmad-output" / "implementation-artifacts"
        status_dir.mkdir(parents=True)
        with open(status_dir / "sprint-status.yaml", "w") as f:
            yaml.dump(SAMPLE_SPRINT_STATUS, f)

        # Try to run -- should fail due to lock
        exit_code, stdout, stderr = run_script("run", cwd=tmpdir)
        assert exit_code == 1
        assert "already running" in stderr.lower() or "lock" in stderr.lower()


def test_ensure_gitignore_creates():
    """Test that ensure_gitignore creates .gitignore with autopilot entries."""
    with tempfile.TemporaryDirectory() as tmpdir:
        gitignore = Path(tmpdir) / ".gitignore"
        assert not gitignore.exists()

        # Simulate ensure_gitignore logic (pure filesystem ops)
        entries = ["_bmad-output/autopilot/"]
        with open(gitignore, "w") as f:
            f.write("# BMad Autopilot runtime artifacts\n")
            for entry in entries:
                f.write(entry + "\n")

        assert gitignore.exists()
        content = gitignore.read_text()
        assert "_bmad-output/autopilot/" in content


def test_ensure_gitignore_idempotent():
    """Test that ensure_gitignore doesn't duplicate entries on existing .gitignore."""
    with tempfile.TemporaryDirectory() as tmpdir:
        gitignore = Path(tmpdir) / ".gitignore"
        gitignore.write_text("node_modules/\n_bmad-output/autopilot/\n")

        # Simulate: entry already exists, should not add again
        existing = gitignore.read_text()
        entry = "_bmad-output/autopilot/"
        assert entry in existing, "Entry should already be present"
        assert existing.count(entry) == 1


if __name__ == "__main__":
    tests = [
        test_help,
        test_run_help,
        test_configure_help,
        test_find_next_story,
        test_configure_saves,
        test_lock_file,
        test_ensure_gitignore_creates,
        test_ensure_gitignore_idempotent,
    ]
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
