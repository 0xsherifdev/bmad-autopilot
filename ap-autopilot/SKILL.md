---
name: ap-autopilot
description: Autonomous Phase 4 sprint orchestrator. Use when the user says 'run autopilot', 'start autopilot', or 'autopilot configure'.
---

# ap-autopilot

## Overview

This skill is the autonomous controller for BMad's Phase 4 implementation cycle. It reads sprint-status.yaml, determines the next story, spawns Claude CLI sessions to execute each step (create story, validate, dev, code review), and manages the loop with configurable human gates via Telegram.

**This skill is a standalone Python script, not a Claude prompt.** It runs persistently, manages state, polls Telegram for responses, and handles retries. Invoke it directly via the CLI.

**Capabilities:**

- **Run** -- Execute the story cycle for a full sprint or single story
- **Resume** -- Pick up from where a crashed or stopped run left off
- **Configure** -- Set autonomy preset, Telegram credentials, and gate overrides
- **Status** -- Show current autopilot state and next story

## Usage

```bash
# Run full sprint loop
uv run skills/ap-autopilot/scripts/autopilot.py run

# Run a specific epic
uv run skills/ap-autopilot/scripts/autopilot.py run --epic epic-1

# Run a single story
uv run skills/ap-autopilot/scripts/autopilot.py run --story 1-4-conversational-ai-llm-orchestration

# Override autonomy preset for this run
uv run skills/ap-autopilot/scripts/autopilot.py run --preset ghost

# Resume after a stop or crash
uv run skills/ap-autopilot/scripts/autopilot.py resume

# Configure
uv run skills/ap-autopilot/scripts/autopilot.py configure \
  --preset checkpoint \
  --telegram-token "bot123:ABC" \
  --telegram-chat-id "12345678"

# Check status
uv run skills/ap-autopilot/scripts/autopilot.py status
```

## Configuration

Config is stored at `{project-root}/_bmad/_autopilot.yaml`. Set via the `configure` command or edit directly.

**Autonomy presets:**

| Preset | After Create | After Dev | After Review | Before Merge |
|---|---|---|---|---|
| ghost | auto | auto | auto | ask |
| checkpoint | auto | ask | auto | ask |
| copilot | ask | ask | ask | ask |

Merge is always gated regardless of preset.

**Config values:**

- `autonomy_preset` -- ghost, checkpoint, or copilot (default: checkpoint)
- `telegram_bot_token` -- Telegram bot token from @BotFather
- `telegram_chat_id` -- Telegram chat ID for notifications
- `retry_budget` -- Max dev retries before escalating (default: 2)
- `dashboard_format` -- html or markdown (default: html)
- `project_label` -- Project name in notifications (default: folder name)
- `gate_after_story_create` -- Override individual gate
- `gate_after_dev` -- Override individual gate
- `gate_after_review` -- Override individual gate
- `gate_before_merge` -- Override individual gate (always true by default)

## Safety

- **Lock file** at `_bmad-output/autopilot/autopilot.lock` prevents double-runs
- **Run logs** at `_bmad-output/autopilot/runs/` for debugging
- **Graceful shutdown** on SIGINT/SIGTERM -- finishes current step, updates status, notifies via Telegram
- **Never merges without human approval**
- **Retry budget** prevents infinite loops on dev failures

## On Activation

Parse the user's intent and arguments, then run the appropriate command directly using Bash. The script path is `.claude/skills/ap-autopilot/scripts/autopilot.py` relative to the project root.

**Routing:**

- "run autopilot" / "start autopilot" with no args → `uv run .claude/skills/ap-autopilot/scripts/autopilot.py run`
- "run autopilot" + epic reference → `uv run .claude/skills/ap-autopilot/scripts/autopilot.py run --epic <epic>`
- "run autopilot" + story reference → `uv run .claude/skills/ap-autopilot/scripts/autopilot.py run --story <story-id>`
- "autopilot configure" / "configure autopilot" → `uv run .claude/skills/ap-autopilot/scripts/autopilot.py configure` with any flags the user provided
- "autopilot status" → `uv run .claude/skills/ap-autopilot/scripts/autopilot.py status`
- "resume autopilot" → `uv run .claude/skills/ap-autopilot/scripts/autopilot.py resume`

If `_bmad/_autopilot.yaml` does not exist or has no `telegram_bot_token`, let the user know they need to run `/ap-setup` first.
