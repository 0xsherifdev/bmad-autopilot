# BMad Autopilot

Autonomous Phase 4 orchestration for [BMad Method](https://docs.bmad-method.org/). Turns the repetitive story cycle (create, validate, dev, review) into an autonomous loop with configurable human involvement.

## What it does

- Reads your `sprint-status.yaml` and works through stories automatically
- Spawns Claude CLI sessions for each step (create story, validate, dev, code review)
- Sends Telegram notifications at configurable gates
- Never merges without your approval
- Generates HTML/markdown sprint dashboards

## Autonomy Presets

| Preset | Behavior |
|---|---|
| **Ghost** | Fully autonomous. Only notifies on failures and before merges. |
| **Checkpoint** | Pauses at key gates (after dev by default). Recommended. |
| **Copilot** | Human approval at every step. |

## Install

Requires [BMad Method](https://docs.bmad-method.org/) to be installed in your project.

```bash
npx bmad-method install --custom-source https://github.com/0xsherifdev/bmad-autopilot --tools claude-code
```

Then run the setup skill in Claude:

```
/ap-setup
```

## Telegram Setup

The autopilot uses Telegram to notify you and ask for approval at gates.

1. Open Telegram, search for **@BotFather**
2. Send `/newbot`, give it a name and username
3. Copy the token BotFather gives you (e.g. `7123456789:AAH...`)
4. Send any message to your new bot
5. Open `https://api.telegram.org/botYOUR_TOKEN/getUpdates` in your browser
6. Find your chat ID in the response: `"chat":{"id":123456789}`
7. Configure:

```bash
uv run .claude/skills/ap-autopilot/scripts/autopilot.py configure \
  --telegram-token "YOUR_TOKEN" \
  --telegram-chat-id "YOUR_CHAT_ID"
```

## Quick Start

```bash
# Configure (set autonomy preset, Telegram)
uv run .claude/skills/ap-autopilot/scripts/autopilot.py configure \
  --preset checkpoint \
  --telegram-token "YOUR_TOKEN" \
  --telegram-chat-id "YOUR_CHAT_ID"

# Run the autopilot
uv run .claude/skills/ap-autopilot/scripts/autopilot.py run

# Run a specific epic
uv run .claude/skills/ap-autopilot/scripts/autopilot.py run --epic epic-1

# Run a single story
uv run .claude/skills/ap-autopilot/scripts/autopilot.py run --story 1-4-story-name

# Check status
uv run .claude/skills/ap-autopilot/scripts/autopilot.py status

# Resume after a stop or crash
uv run .claude/skills/ap-autopilot/scripts/autopilot.py resume
```

## Requirements

- [Claude Code CLI](https://claude.ai/claude-code) installed and authenticated
- [BMad Method module](https://docs.bmad-method.org/) installed in your project
- Python 3.10+ with [uv](https://docs.astral.sh/uv/)
- Git
- Telegram bot (optional but recommended)

## Module Structure

```
ap-autopilot/     # Controller script -- the autonomous loop
ap-dashboard/     # Sprint reporting (HTML/markdown)
ap-setup/         # Installation and configuration
```

## License

MIT
