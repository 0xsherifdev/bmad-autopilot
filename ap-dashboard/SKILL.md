---
name: ap-dashboard
description: Generate sprint run dashboard reports. Use when the user says 'generate dashboard', 'show sprint status', or 'autopilot report'.
---

# ap-dashboard

## Overview

This skill generates sprint run dashboard reports for the BMad Autopilot module. It reads sprint-status.yaml, cross-references actual git branch state, and produces a self-contained report showing what ran, what passed, what failed, and where things stand.

Act as a clear, accurate reporter. The dashboard is the user's window into what the autopilot did -- accuracy over aesthetics.

**Capabilities:**

- **Generate Report** -- Full sprint dashboard as self-contained HTML (default) or markdown. Includes per-story status cards, progress tracking, branch state, and file change counts.
- **Summary** -- Quick one-line-per-story text output for Telegram messages or terminal checks.

**Args:** `[sprint-status-path]` (optional, defaults to `{implementation_artifacts}/sprint-status.yaml`), `--format html|markdown`, `--summary` (summary mode).

## On Activation

Load available config from `{project-root}/_bmad/config.yaml` and `{project-root}/_bmad/config.user.yaml` (root level and `ap` section). If config is missing, let the user know `ap-setup` can configure the module at any time. Use sensible defaults for anything not configured.

Resolve these config values (defaults in parens):
- `dashboard_format` (`html`) -- output format
- `project_label` (project name from sprint-status.yaml) -- display name in reports
- `implementation_artifacts` (`{project-root}/_bmad-output/implementation-artifacts`) -- where sprint-status.yaml lives

If `--summary` is passed or the user asks for a quick status, produce Summary output only.

## Generate Report

1. Run the data gathering script to get structured sprint data:
   ```
   uv run scripts/gather-sprint-data.py <sprint-status-path> --verbose
   ```
   If the script cannot execute, perform equivalent data gathering by reading sprint-status.yaml directly and checking git branches via `git branch --list`.

2. Using the JSON output, generate the dashboard in the configured format.

**HTML report requirements:**
- Self-contained single file with inline CSS (no external dependencies)
- Clean, modern design with a muted color palette
- Project name and generation timestamp in the header
- Overall progress bar (done/total stories)
- Per-epic sections with story cards showing:
  - Story ID and status (color-coded: done=green, in-progress=blue, review=amber, backlog=gray, ready-for-dev=teal)
  - Branch name (linked if possible) or "No branch" for backlog stories
  - Files changed count (if branch exists)
  - Whether a story file exists
- Summary statistics at the bottom

**Markdown report requirements:**
- Clean table-based layout, readable in terminal via `cat`
- Same information as HTML: progress, per-epic tables, summary
- Status indicators using text labels (no emoji unless user configured them)

3. Write the report to `{implementation_artifacts}/autopilot-dashboard.html` (or `.md`). Inform the user of the output path.

## Summary

Produce a compact text block with one line per story that has activity (skip pure backlog). Format:

```
[Project Label] Sprint Status
Epic 1: 2/5 done
  1-1 first-story .......... done
  1-2 second-story ......... in-progress (branch: story/1-2, 14 files)
  1-3 third-story .......... review
```

This format is designed for Telegram messages and terminal output. Return it as text, do not write to a file unless asked.
