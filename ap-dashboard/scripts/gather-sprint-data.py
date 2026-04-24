#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = ["pyyaml>=6.0"]
# ///

"""
Gather sprint data from sprint-status.yaml and git branch state.

Reads the sprint status file, cross-references git branches, and outputs
a JSON payload with per-story data for dashboard rendering.

Usage:
    uv run scripts/gather-sprint-data.py <sprint-status-path> [options]

Output JSON structure:
    {
        "project": "project-name",
        "generated_at": "ISO timestamp",
        "epics": [
            {
                "id": "epic-1",
                "status": "in-progress",
                "stories": [
                    {
                        "id": "1-1-story-name",
                        "status": "done|in-progress|backlog|review|ready-for-dev",
                        "branch": "story/1-1-story-name" or null,
                        "branch_exists": true/false,
                        "files_changed": 12 or null,
                        "has_story_file": true/false,
                        "story_file_path": "path" or null
                    }
                ]
            }
        ],
        "summary": {
            "total_stories": 30,
            "done": 2,
            "in_progress": 1,
            "backlog": 27,
            "review": 0,
            "ready_for_dev": 0
        }
    }
"""

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml


def get_git_branches(project_root: str) -> set[str]:
    """Get all local git branch names."""
    try:
        result = subprocess.run(
            ["git", "branch", "--list", "--format=%(refname:short)"],
            capture_output=True,
            text=True,
            cwd=project_root,
        )
        if result.returncode == 0:
            return {b.strip() for b in result.stdout.strip().split("\n") if b.strip()}
    except FileNotFoundError:
        pass
    return set()


def get_files_changed(branch: str, project_root: str) -> int | None:
    """Count files changed on a branch compared to main."""
    try:
        result = subprocess.run(
            ["git", "diff", "--name-only", "main..." + branch],
            capture_output=True,
            text=True,
            cwd=project_root,
        )
        if result.returncode == 0 and result.stdout.strip():
            return len(result.stdout.strip().split("\n"))
    except FileNotFoundError:
        pass
    return None


def find_story_file(story_id: str, story_location: str, project_root: str) -> str | None:
    """Find the story file for a given story ID."""
    story_dir = Path(project_root) / story_location
    if not story_dir.exists():
        return None
    for f in story_dir.iterdir():
        if f.is_file() and f.suffix == ".md" and story_id in f.stem:
            return str(f.relative_to(project_root))
    return None


def parse_sprint_status(path: str) -> dict:
    """Parse sprint-status.yaml and return structured data."""
    with open(path) as f:
        data = yaml.safe_load(f)

    project = data.get("project", "unknown")
    story_location = data.get("story_location", "_bmad-output/implementation-artifacts")
    project_root = str(Path(path).parent)

    # Walk up to find git root
    git_root = project_root
    while git_root != "/" and not (Path(git_root) / ".git").exists():
        git_root = str(Path(git_root).parent)

    # If sprint-status is inside the project, use git root
    if (Path(git_root) / ".git").exists():
        project_root = git_root

    dev_status = data.get("development_status", {})
    branches = get_git_branches(project_root)

    epics = []
    current_epic = None
    summary = {
        "total_stories": 0,
        "done": 0,
        "in_progress": 0,
        "backlog": 0,
        "review": 0,
        "ready_for_dev": 0,
    }

    for key, status in dev_status.items():
        # Epic entries like "epic-1"
        if re.match(r"^epic-\d+$", key):
            if current_epic:
                epics.append(current_epic)
            current_epic = {"id": key, "status": status, "stories": []}
            continue

        # Retrospective entries
        if key.endswith("-retrospective"):
            continue

        # Story entries
        if current_epic is not None:
            branch_name = f"story/{key}"
            branch_exists = branch_name in branches
            files_changed = get_files_changed(branch_name, project_root) if branch_exists else None
            story_file = find_story_file(key, story_location, project_root)

            current_epic["stories"].append(
                {
                    "id": key,
                    "status": status,
                    "branch": branch_name if branch_exists else None,
                    "branch_exists": branch_exists,
                    "files_changed": files_changed,
                    "has_story_file": story_file is not None,
                    "story_file_path": story_file,
                }
            )

            summary["total_stories"] += 1
            status_key = status.replace("-", "_")
            if status_key in summary:
                summary[status_key] += 1

    if current_epic:
        epics.append(current_epic)

    return {
        "project": project,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "sprint_status_path": path,
        "epics": epics,
        "summary": summary,
    }


def main():
    parser = argparse.ArgumentParser(
        description="Gather sprint data from sprint-status.yaml and git state"
    )
    parser.add_argument("sprint_status_path", help="Path to sprint-status.yaml")
    parser.add_argument("-o", "--output", help="Output file (default: stdout)")
    parser.add_argument("--verbose", action="store_true", help="Print diagnostics to stderr")
    args = parser.parse_args()

    if not Path(args.sprint_status_path).exists():
        print(f"Error: {args.sprint_status_path} not found", file=sys.stderr)
        sys.exit(2)

    if args.verbose:
        print(f"Reading: {args.sprint_status_path}", file=sys.stderr)

    data = parse_sprint_status(args.sprint_status_path)

    output = json.dumps(data, indent=2)

    if args.output:
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output).write_text(output)
        if args.verbose:
            print(f"Written to: {args.output}", file=sys.stderr)
    else:
        print(output)

    sys.exit(0)


if __name__ == "__main__":
    main()
