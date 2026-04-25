---
name: "ap-setup"
description: First-time onboarding and Telegram setup for BMad Autopilot. Walks through prerequisites, bot creation, connection testing, and autonomy preset selection. Use when the user says 'setup autopilot', 'install autopilot', or runs /ap-setup.
---

# BMad Autopilot -- First-Time Setup

## Overview

Guided onboarding for BMad Autopilot. This is the first-time experience -- it checks prerequisites, walks through Telegram bot creation step by step, verifies the connection works, and helps the user pick the right autonomy preset.

For quick config changes after setup is complete, use `/ap-autopilot configure` instead.

Module identity (name, code, version) comes from `./assets/module.yaml`.

## On Activation

### Step 1: Check Prerequisites

Check each prerequisite and report status. Mark each as READY or MISSING.

**Required:**
- **uv** -- Run `uv --version`. If missing, tell the user: "Install uv: `curl -LsSf https://astral.sh/uv/install.sh | sh`"
- **claude CLI** -- Run `claude --version`. If missing, tell the user: "Install Claude Code: `npm install -g @anthropic-ai/claude-code`"

**Optional (needed for PR workflow):**
- **gh CLI** -- Run `gh auth status`. If missing or not authenticated, tell the user: "Install: `brew install gh` then `gh auth login`. Without this, autopilot will use local git merges instead of GitHub PRs."

If any required prerequisite is missing, stop here and help them install it before continuing.

### Step 2: Telegram Bot Setup

Walk the user through creating a Telegram bot. Don't just ask for the token -- teach them how to get it.

Tell the user:

> **Let's set up your Telegram bot for autopilot notifications.**
>
> 1. Open Telegram and search for **@BotFather**
> 2. Send `/newbot`
> 3. Choose a name for your bot (e.g., "My Project Autopilot")
> 4. Choose a username (must end in "bot", e.g., "myproject_autopilot_bot")
> 5. BotFather will give you a token that looks like `7123456789:AAHxxxxxxx...` -- copy it

Ask: "Paste your bot token here."

After receiving the token, continue:

> **Now let's get your chat ID.**
>
> 1. Open a chat with your new bot in Telegram (search for the username you just created)
> 2. Send it any message (e.g., "hello")
> 3. Now I'll look up your chat ID...

Run this command to fetch the chat ID automatically:
```bash
curl -s "https://api.telegram.org/bot<TOKEN>/getUpdates" | python3 -c "import sys,json; data=json.load(sys.stdin); updates=data.get('result',[]); print(updates[-1]['message']['chat']['id'] if updates else 'NO_MESSAGES')"
```

If that returns a chat ID, confirm it with the user. If it returns `NO_MESSAGES`, tell them:
> "I couldn't find any messages yet. Make sure you sent a message to your bot in Telegram, then tell me to try again."

### Step 3: Test the Connection

Once you have both the token and chat ID, send a test notification:

```bash
curl -s -X POST "https://api.telegram.org/bot<TOKEN>/sendMessage" \
  -H "Content-Type: application/json" \
  -d '{"chat_id": "<CHAT_ID>", "text": "BMad Autopilot connected! Setup is working.", "parse_mode": "Markdown"}'
```

Ask the user: "Did you receive the test message in Telegram?"

If yes, continue. If no, troubleshoot:
- Verify the token is correct (no extra spaces)
- Verify they messaged the bot before we fetched the chat ID
- Try fetching the chat ID again

### Step 4: Choose Autonomy Preset

Explain the presets with concrete examples of what each feels like:

> **How much autonomy should the autopilot have?**
>
> - **Ghost** -- The autopilot runs silently, implementing stories back to back. You only hear from it when something fails or when a PR is ready to merge. Best for: overnight runs, well-tested codebases, when you trust the story specs.
>
> - **Checkpoint** (recommended) -- The autopilot pauses after each story's dev phase is complete so you can review the code before it moves to merge. Best for: active projects where you want to stay in the loop without micromanaging.
>
> - **Copilot** -- The autopilot asks for your approval at every step: after story creation, after dev, after review, and before merge. Best for: first-time use, complex stories, when you want to learn how the system works.

Ask: "Which preset? (ghost / checkpoint / copilot)"

### Step 5: Remaining Configuration

Collect these with sensible defaults. Present them all at once so the user can respond with just the ones they want to change:

> Here are the remaining settings (defaults in brackets):
>
> - **Dashboard format** [html] -- html (opens in browser) or markdown (terminal-friendly)
> - **Project label** [folder name] -- Name shown in Telegram messages
> - **Retry budget** [2] -- How many times to retry a failed dev attempt before notifying you

### Step 6: Save Configuration

First, ensure `_bmad/_autopilot.yaml` is in the project's `.gitignore` -- it contains the Telegram bot token and must never be committed.

Write the collected values to `{project-root}/_bmad/_autopilot.yaml`:

```yaml
autonomy_preset: <chosen preset>
telegram_bot_token: <token>
telegram_chat_id: <chat_id>
retry_budget: <budget>
dashboard_format: <format>
project_label: <label>
```

Then write the temp answers JSON and run the module config scripts (see Write Files section below).

### Step 7: Verify and Finish

Run the status check:
```bash
uv run .claude/skills/ap-autopilot/scripts/autopilot.py status
```

Show the result to the user. Confirm that Telegram shows "configured".

Then tell the user:

> **You're all set!**
>
> - Run `/ap-autopilot run` to start the autopilot
> - Run `/ap-autopilot configure` to change settings later
> - Run `/ap-autopilot status` to check on things anytime

## Write Files

Write a temp JSON file with the collected answers structured as `{"core": {...}, "module": {...}}` (omit `core` if it already exists). Then run both scripts -- they can run in parallel since they write to different files:

```bash
python3 ./scripts/merge-config.py --config-path "{project-root}/_bmad/config.yaml" --user-config-path "{project-root}/_bmad/config.user.yaml" --module-yaml ./assets/module.yaml --answers {temp-file} --legacy-dir "{project-root}/_bmad"
python3 ./scripts/merge-help-csv.py --target "{project-root}/_bmad/module-help.csv" --source ./assets/module-help.csv --legacy-dir "{project-root}/_bmad" --module-code ap
```

Both scripts output JSON to stdout with results. If either exits non-zero, surface the error and stop. The scripts automatically read legacy config values as fallback defaults, then delete the legacy files after a successful merge. Check `legacy_configs_deleted` and `legacy_csvs_deleted` in the output to confirm cleanup.

Run `./scripts/merge-config.py --help` or `./scripts/merge-help-csv.py --help` for full usage.

## Create Output Directories

After writing config, create any output directories that were configured. For filesystem operations only (such as creating directories), resolve the `{project-root}` token to the actual project root and create each path-type value from `config.yaml` that does not yet exist -- this includes `output_folder` and any module variable whose value starts with `{project-root}/`. The paths stored in the config files must continue to use the literal `{project-root}` token; only the directories on disk should use the resolved paths. Use `mkdir -p` or equivalent to create the full path.

## Cleanup Legacy Directories

After both merge scripts complete successfully, remove the installer's package directories. Skills and agents in these directories are already installed at `.claude/skills/` -- the `_bmad/` directory should only contain config files.

```bash
python3 ./scripts/cleanup-legacy.py --bmad-dir "{project-root}/_bmad" --module-code ap --also-remove _config --skills-dir "{project-root}/.claude/skills"
```

The script verifies that every skill in the legacy directories exists at `.claude/skills/` before removing anything. Directories without skills (like `_config/`) are removed directly. If the script exits non-zero, surface the error and stop. Missing directories (already cleaned by a prior run) are not errors -- the script is idempotent.

Check `directories_removed` and `files_removed_count` in the JSON output for the confirmation step. Run `./scripts/cleanup-legacy.py --help` for full usage.

## Confirm

Use the script JSON output to display what was written -- config values set (written to `config.yaml` at root for core, module section for module values), user settings written to `config.user.yaml` (`user_keys` in result), help entries added, fresh install vs update. If legacy files were deleted, mention the migration. If legacy directories were removed, report the count and list (e.g. "Cleaned up 106 installer package files from bmb/, core/, \_config/ -- skills are installed at .claude/skills/"). Then display the `module_greeting` from `./assets/module.yaml` to the user.

## Outcome

Once the user's `user_name` and `communication_language` are known (from collected input, arguments, or existing config), use them consistently for the remainder of the session: address the user by their configured name and communicate in their configured `communication_language`.
