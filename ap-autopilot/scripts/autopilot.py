#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = ["pyyaml>=6.0", "httpx>=0.27"]
# ///

"""
BMad Autopilot Controller

Autonomous orchestrator for BMad Phase 4 implementation workflows.
Reads sprint-status.yaml, spawns Claude CLI sessions to execute skills
(create-story, validate, dev-story, code-review), and manages the cycle
with configurable human gates via Telegram.

Usage:
    uv run scripts/autopilot.py run [--epic EPIC_ID] [--story STORY_ID] [--preset ghost|checkpoint|copilot]
    uv run scripts/autopilot.py resume
    uv run scripts/autopilot.py configure --preset <preset> [--telegram-token TOKEN] [--telegram-chat-id CHAT_ID]
    uv run scripts/autopilot.py status

Autonomy presets:
    ghost       -- Fully autonomous. Only notifies on failures and before merges.
    checkpoint  -- Pauses at configurable gates. Waits for Telegram GO/STOP.
    copilot     -- Human approval at every step.
"""

import argparse
import json
import os
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx
import yaml

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

LOCK_FILE = "_bmad-output/autopilot/autopilot.lock"
LOG_DIR = "_bmad-output/autopilot/runs"
CONFIG_FILE = "_bmad/_autopilot.yaml"
SPRINT_STATUS_DEFAULT = "_bmad-output/implementation-artifacts/sprint-status.yaml"

PRESETS = {
    "ghost": {
        "gate_after_story_create": False,
        "gate_after_dev": False,
        "gate_after_review": False,
        "gate_before_merge": True,  # Always true
    },
    "checkpoint": {
        "gate_after_story_create": False,
        "gate_after_dev": True,
        "gate_after_review": False,
        "gate_before_merge": True,
    },
    "copilot": {
        "gate_after_story_create": True,
        "gate_after_dev": True,
        "gate_after_review": True,
        "gate_before_merge": True,
    },
}

MAX_RETRIES = 2

# Story lifecycle steps
STEPS = ["create", "validate", "dev", "review", "merge"]

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------


class RunLogger:
    """Append-only run log with both file and stderr output."""

    def __init__(self, project_root: str):
        self.project_root = project_root
        log_dir = Path(project_root) / LOG_DIR
        log_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y-%m-%d-%H%M%S")
        self.log_path = log_dir / f"{timestamp}.log"
        self.entries: list[str] = []

    def log(self, level: str, message: str):
        ts = datetime.now(timezone.utc).isoformat()
        entry = f"[{ts}] [{level}] {message}"
        self.entries.append(entry)
        print(entry, file=sys.stderr)
        # Append to file immediately
        with open(self.log_path, "a") as f:
            f.write(entry + "\n")

    def info(self, msg: str):
        self.log("INFO", msg)

    def error(self, msg: str):
        self.log("ERROR", msg)

    def warn(self, msg: str):
        self.log("WARN", msg)


# ---------------------------------------------------------------------------
# Lock file management
# ---------------------------------------------------------------------------


def acquire_lock(project_root: str) -> bool:
    """Acquire PID lock file. Returns False if another instance is running."""
    lock_path = Path(project_root) / LOCK_FILE
    lock_path.parent.mkdir(parents=True, exist_ok=True)

    if lock_path.exists():
        try:
            old_pid = int(lock_path.read_text().strip())
            # Check if process is still running
            os.kill(old_pid, 0)
            return False  # Process is alive
        except (ProcessLookupError, ValueError):
            pass  # Stale lock, safe to overwrite
        except PermissionError:
            return False  # Process exists but we can't signal it

    lock_path.write_text(str(os.getpid()))
    return True


def release_lock(project_root: str):
    """Release PID lock file."""
    lock_path = Path(project_root) / LOCK_FILE
    if lock_path.exists():
        try:
            pid = int(lock_path.read_text().strip())
            if pid == os.getpid():
                lock_path.unlink()
        except (ValueError, OSError):
            pass


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


def load_config(project_root: str) -> dict:
    """Load autopilot config, merging defaults with project config."""
    config = {
        "autonomy_preset": "checkpoint",
        "telegram_bot_token": None,
        "telegram_chat_id": None,
        "retry_budget": MAX_RETRIES,
        "dashboard_format": "html",
        "project_label": Path(project_root).name,
        "sprint_status_path": SPRINT_STATUS_DEFAULT,
        **PRESETS["checkpoint"],
    }

    # Load from _bmad/_autopilot.yaml
    config_path = Path(project_root) / CONFIG_FILE
    if config_path.exists():
        with open(config_path) as f:
            user_config = yaml.safe_load(f) or {}
        config.update(user_config)

        # Apply preset gates if preset is set but individual gates aren't overridden
        preset = config.get("autonomy_preset", "checkpoint")
        if preset in PRESETS:
            for gate, default_val in PRESETS[preset].items():
                if gate not in user_config:
                    config[gate] = default_val

    # Also check _bmad/config.yaml and config.user.yaml for ap section
    for cfg_name in ["config.yaml", "config.user.yaml"]:
        cfg_path = Path(project_root) / "_bmad" / cfg_name
        if cfg_path.exists():
            with open(cfg_path) as f:
                data = yaml.safe_load(f) or {}
            ap_section = data.get("ap", {})
            if ap_section:
                config.update(ap_section)

    return config


def save_config(project_root: str, config: dict):
    """Save autopilot config, preserving comments if file exists."""
    config_path = Path(project_root) / CONFIG_FILE
    config_path.parent.mkdir(parents=True, exist_ok=True)

    if config_path.exists():
        # Preserve comments: read existing file, update values in-place
        lines = config_path.read_text().split("\n")
        updated_keys = set()
        new_lines = []
        for line in lines:
            # Check if this line is a key: value pair (not a comment or blank)
            stripped = line.strip()
            if stripped and not stripped.startswith("#"):
                key = stripped.split(":")[0].strip()
                if key in config:
                    indent = line[: len(line) - len(line.lstrip())]
                    value = config[key]
                    if value is None:
                        new_lines.append(f"{indent}{key}: null")
                    elif isinstance(value, bool):
                        new_lines.append(f"{indent}{key}: {str(value).lower()}")
                    elif isinstance(value, int):
                        new_lines.append(f"{indent}{key}: {value}")
                    elif isinstance(value, str) and value.isdigit():
                        new_lines.append(f"{indent}{key}: '{value}'")
                    else:
                        new_lines.append(f"{indent}{key}: {value}")
                    updated_keys.add(key)
                    continue
            new_lines.append(line)

        # Append any new keys not in the original file
        for key, value in config.items():
            if key not in updated_keys:
                if value is None:
                    new_lines.append(f"{key}: null")
                elif isinstance(value, bool):
                    new_lines.append(f"{key}: {str(value).lower()}")
                else:
                    new_lines.append(f"{key}: {value}")

        config_path.write_text("\n".join(new_lines))
    else:
        with open(config_path, "w") as f:
            yaml.dump(config, f, default_flow_style=False, sort_keys=False)


# ---------------------------------------------------------------------------
# Sprint status
# ---------------------------------------------------------------------------


def load_sprint_status(path: str) -> dict:
    """Load and parse sprint-status.yaml."""
    with open(path) as f:
        return yaml.safe_load(f)


def update_story_status(path: str, story_id: str, new_status: str):
    """Update a story's status in sprint-status.yaml."""
    with open(path) as f:
        content = f.read()

    # Replace the status value for this story ID
    lines = content.split("\n")
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith(f"{story_id}:"):
            indent = line[: len(line) - len(line.lstrip())]
            lines[i] = f"{indent}{story_id}: {new_status}"
            break

    with open(path, "w") as f:
        f.write("\n".join(lines))


def find_next_story(sprint_status: dict, epic_id: str | None = None) -> tuple[str, str] | None:
    """Find the next story to work on. Returns (story_id, current_status) or None."""
    dev_status = sprint_status.get("development_status", {})

    for key, status in dev_status.items():
        # Skip epic entries and retrospectives
        if key.startswith("epic-") or key.endswith("-retrospective"):
            continue

        # If epic filter is set, check story belongs to that epic
        if epic_id:
            epic_num = epic_id.replace("epic-", "")
            if not key.startswith(f"{epic_num}-"):
                continue

        # Find first non-done story (in-progress first, then backlog)
        if status == "in-progress":
            return (key, status)

    # No in-progress, find first backlog
    for key, status in dev_status.items():
        if key.startswith("epic-") or key.endswith("-retrospective"):
            continue
        if epic_id:
            epic_num = epic_id.replace("epic-", "")
            if not key.startswith(f"{epic_num}-"):
                continue
        if status in ("backlog", "ready-for-dev"):
            return (key, status)

    return None


# ---------------------------------------------------------------------------
# Telegram
# ---------------------------------------------------------------------------


class TelegramNotifier:
    """Send messages and receive responses via Telegram Bot API."""

    def __init__(self, bot_token: str | None, chat_id: str | None, project_label: str):
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.project_label = project_label
        self.enabled = bool(bot_token and chat_id)
        self._last_update_id = 0

    def send(self, message: str) -> bool:
        """Send a message. Returns True if sent successfully."""
        if not self.enabled:
            print(f"[TELEGRAM] {self.project_label}: {message}", file=sys.stderr)
            return True

        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        try:
            resp = httpx.post(
                url,
                json={
                    "chat_id": self.chat_id,
                    "text": f"*{self.project_label}*: {message}",
                    "parse_mode": "Markdown",
                },
                timeout=10,
            )
            return resp.status_code == 200
        except httpx.HTTPError:
            return False

    def ask(self, question: str, timeout_minutes: int = 60) -> str | None:
        """Send a question and wait for GO/STOP reply. Returns 'go', 'stop', or None on timeout."""
        self.send(f"{question}\n\nReply *GO* to continue or *STOP* to halt.")

        if not self.enabled:
            # No Telegram configured -- auto-proceed with a warning
            print(f"\n[GATE] {question}", file=sys.stderr)
            print("[GATE] No Telegram configured -- auto-proceeding (GO)", file=sys.stderr)
            return "go"

        # Poll for reply
        deadline = time.time() + (timeout_minutes * 60)
        url = f"https://api.telegram.org/bot{self.bot_token}/getUpdates"

        while time.time() < deadline:
            try:
                resp = httpx.get(
                    url,
                    params={"offset": self._last_update_id + 1, "timeout": 30},
                    timeout=35,
                )
                if resp.status_code == 200:
                    data = resp.json()
                    for update in data.get("result", []):
                        self._last_update_id = update["update_id"]
                        msg = update.get("message", {})
                        text = msg.get("text", "").strip().lower()
                        chat = str(msg.get("chat", {}).get("id", ""))
                        if chat == str(self.chat_id) and text in ("go", "stop"):
                            return text
            except httpx.HTTPError:
                time.sleep(5)

        return None  # Timeout


# ---------------------------------------------------------------------------
# Claude CLI execution
# ---------------------------------------------------------------------------


def run_claude(
    prompt: str,
    project_root: str,
    logger: RunLogger,
    allowed_tools: str = "Read,Edit,Bash,Write,Glob,Grep",
    timeout_minutes: int = 30,
) -> dict:
    """
    Run a Claude CLI session in headless mode.
    Returns {"success": bool, "output": str, "session_id": str|None, "exit_code": int}
    """
    cmd = [
        "claude",
        "-p", prompt,
        "--output-format", "json",
        "--allowedTools", allowed_tools,
    ]

    logger.info(f"Spawning: claude -p \"{prompt[:80]}...\"")

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            cwd=project_root,
            timeout=timeout_minutes * 60,
        )

        exit_code = result.returncode
        logger.info(f"Claude exit code: {exit_code}")

        # Try to parse JSON output
        output_text = result.stdout
        session_id = None
        result_text = output_text

        try:
            parsed = json.loads(output_text)
            session_id = parsed.get("session_id")
            result_text = parsed.get("result", output_text)
        except (json.JSONDecodeError, TypeError):
            pass

        if result.stderr:
            logger.warn(f"Claude stderr: {result.stderr[:500]}")

        return {
            "success": exit_code == 0,
            "output": result_text,
            "session_id": session_id,
            "exit_code": exit_code,
        }

    except subprocess.TimeoutExpired:
        logger.error(f"Claude timed out after {timeout_minutes}m")
        return {"success": False, "output": "Timeout", "session_id": None, "exit_code": -1}
    except FileNotFoundError:
        logger.error("claude CLI not found. Is it installed?")
        return {"success": False, "output": "claude not found", "session_id": None, "exit_code": -1}


# ---------------------------------------------------------------------------
# Git operations
# ---------------------------------------------------------------------------


def git(args: list[str], project_root: str) -> tuple[bool, str]:
    """Run a git command. Returns (success, output)."""
    result = subprocess.run(
        ["git"] + args,
        capture_output=True,
        text=True,
        cwd=project_root,
    )
    return result.returncode == 0, result.stdout.strip()


def create_story_branch(story_id: str, project_root: str, logger: RunLogger) -> bool:
    """Create and checkout a new branch for a story."""
    branch = f"story/{story_id}"

    # Check if branch already exists
    success, branches = git(["branch", "--list", branch], project_root)
    if branch in branches:
        logger.info(f"Branch {branch} already exists, checking out")
        ok, _ = git(["checkout", branch], project_root)
        return ok

    logger.info(f"Creating branch: {branch}")
    ok, _ = git(["checkout", "-b", branch], project_root)
    return ok


def checkout_main(project_root: str, logger: RunLogger) -> bool:
    """Return to main branch."""
    ok, _ = git(["checkout", "main"], project_root)
    if not ok:
        logger.warn("Failed to checkout main, trying master")
        ok, _ = git(["checkout", "master"], project_root)
    return ok


# ---------------------------------------------------------------------------
# Story cycle
# ---------------------------------------------------------------------------


def run_story_cycle(
    story_id: str,
    config: dict,
    project_root: str,
    logger: RunLogger,
    notifier: TelegramNotifier,
    sprint_status_path: str,
) -> bool:
    """
    Execute the full cycle for one story:
    create -> validate -> dev -> review -> (merge gate)

    Returns True if the story completed successfully.
    """
    retry_budget = config.get("retry_budget", MAX_RETRIES)
    story_location = "_bmad-output/implementation-artifacts"

    logger.info(f"=== Starting story cycle: {story_id} ===")
    notifier.send(f"Starting story: `{story_id}`")

    # --- Step 1: Create Story ---
    logger.info("Step 1: Create story")
    story_file = Path(project_root) / story_location / f"{story_id}.md"

    if story_file.exists():
        logger.info(f"Story file already exists: {story_file.name}")
    else:
        result = run_claude(
            f"Create the story spec for story {story_id}. "
            f"Read the epics file and sprint status to understand context. "
            f"Write the story file to {story_location}/{story_id}.md",
            project_root,
            logger,
        )
        if not result["success"]:
            logger.error(f"Failed to create story: {result['output'][:200]}")
            notifier.send(f"Failed to create story `{story_id}`: {result['output'][:200]}")
            return False

        if not story_file.exists():
            logger.error("Story file was not created")
            notifier.send(f"Story creation ran but no file was produced for `{story_id}`")
            return False

    update_story_status(sprint_status_path, story_id, "ready-for-dev")

    # Gate: after story create
    if config.get("gate_after_story_create"):
        response = notifier.ask(
            f"Story `{story_id}` created.\nReview: `{story_location}/{story_id}.md`"
        )
        if response == "stop":
            logger.info("User stopped after story creation")
            notifier.send(f"Stopped at story creation for `{story_id}`")
            return False

    # --- Step 2: Validate Story ---
    logger.info("Step 2: Validate story")
    result = run_claude(
        f"Validate the story at {story_location}/{story_id}.md. "
        f"Check that it has all required sections, acceptance criteria, and technical guidance. "
        f"If issues are found, fix them in the story file.",
        project_root,
        logger,
    )
    if not result["success"]:
        logger.warn(f"Story validation had issues: {result['output'][:200]}")
        # Validation failure is non-fatal -- continue with best effort

    # --- Step 3: Dev Story ---
    logger.info("Step 3: Dev story")

    # Create branch
    if not create_story_branch(story_id, project_root, logger):
        logger.error(f"Failed to create branch for {story_id}")
        notifier.send(f"Failed to create git branch for `{story_id}`")
        return False

    update_story_status(sprint_status_path, story_id, "in-progress")

    dev_success = False
    for attempt in range(1, retry_budget + 1):
        logger.info(f"Dev attempt {attempt}/{retry_budget}")

        result = run_claude(
            f"Implement story {story_id}. "
            f"The story spec is at {story_location}/{story_id}.md. "
            f"Follow the acceptance criteria and technical guidance. "
            f"Run tests after implementation to verify everything works. "
            f"When done, stage all changed files and commit with a descriptive message.",
            project_root,
            logger,
            allowed_tools="Read,Edit,Bash,Write,Glob,Grep",
            timeout_minutes=45,
        )

        if result["success"]:
            dev_success = True
            break
        else:
            logger.warn(f"Dev attempt {attempt} failed: {result['output'][:200]}")
            if attempt < retry_budget:
                logger.info("Retrying...")
                notifier.send(
                    f"Dev attempt {attempt}/{retry_budget} failed for `{story_id}`. Retrying..."
                )

    if not dev_success:
        logger.error(f"Dev failed after {retry_budget} attempts")
        notifier.send(
            f"Dev FAILED for `{story_id}` after {retry_budget} attempts.\n"
            f"Last error: {result['output'][:300]}"
        )
        update_story_status(sprint_status_path, story_id, "in-progress")
        checkout_main(project_root, logger)
        return False

    # Gate: after dev
    if config.get("gate_after_dev"):
        response = notifier.ask(
            f"Dev complete for `{story_id}` on branch `story/{story_id}`."
        )
        if response == "stop":
            logger.info("User stopped after dev")
            checkout_main(project_root, logger)
            return False

    # --- Step 4: Code Review ---
    logger.info("Step 4: Code review")
    update_story_status(sprint_status_path, story_id, "review")

    result = run_claude(
        f"Run a code review on the changes for story {story_id}. "
        f"The story spec is at {story_location}/{story_id}.md. "
        f"Review all changed files on this branch against the acceptance criteria. "
        f"If you find issues, fix them. Then verify tests still pass. "
        f"If you made any fixes, stage and commit them with a message describing the review fixes.",
        project_root,
        logger,
    )

    if not result["success"]:
        logger.warn(f"Code review had issues: {result['output'][:200]}")
        notifier.send(
            f"Code review flagged issues for `{story_id}`: {result['output'][:300]}"
        )

    # Gate: after review
    if config.get("gate_after_review"):
        response = notifier.ask(
            f"Code review complete for `{story_id}`. Branch: `story/{story_id}`"
        )
        if response == "stop":
            logger.info("User stopped after review")
            checkout_main(project_root, logger)
            return False

    # --- Step 5: Merge Gate (always) ---
    logger.info("Step 5: Merge gate")

    # Get summary of changes
    _, diff_stat = git(["diff", "--stat", f"main...story/{story_id}"], project_root)

    response = notifier.ask(
        f"Story `{story_id}` ready to merge.\n\n"
        f"Changes:\n```\n{diff_stat[:500]}\n```"
    )

    if response == "stop":
        logger.info("User declined merge")
        notifier.send(f"Merge declined for `{story_id}`. Branch preserved: `story/{story_id}`")
        checkout_main(project_root, logger)
        return False

    if response == "go":
        # Merge
        checkout_main(project_root, logger)
        ok, merge_output = git(["merge", "--no-ff", f"story/{story_id}"], project_root)
        if ok:
            logger.info(f"Merged story/{story_id} to main")
            update_story_status(sprint_status_path, story_id, "done")
            notifier.send(f"Merged `{story_id}` to main.")
        else:
            logger.error(f"Merge failed: {merge_output}")
            notifier.send(f"Merge FAILED for `{story_id}`: {merge_output[:300]}")
            return False
    else:
        # Timeout or no response
        logger.warn("No response at merge gate, leaving branch unmerged")
        notifier.send(f"No response at merge gate for `{story_id}`. Branch preserved.")
        checkout_main(project_root, logger)
        return False

    logger.info(f"=== Story {story_id} completed ===")
    return True


# ---------------------------------------------------------------------------
# Main commands
# ---------------------------------------------------------------------------


def cmd_run(args, project_root: str):
    """Run the autopilot loop."""
    logger = RunLogger(project_root)
    config = load_config(project_root)

    # Apply CLI overrides
    if args.preset:
        config["autonomy_preset"] = args.preset
        if args.preset in PRESETS:
            config.update(PRESETS[args.preset])

    sprint_path = str(Path(project_root) / config["sprint_status_path"])

    if not Path(sprint_path).exists():
        logger.error(f"Sprint status not found: {sprint_path}")
        sys.exit(2)

    # Acquire lock
    if not acquire_lock(project_root):
        logger.error("Another autopilot instance is already running (lock file exists)")
        sys.exit(1)

    notifier = TelegramNotifier(
        config.get("telegram_bot_token"),
        config.get("telegram_chat_id"),
        config.get("project_label", Path(project_root).name),
    )

    # Graceful shutdown handler
    shutdown_requested = False

    def handle_signal(signum, frame):
        nonlocal shutdown_requested
        shutdown_requested = True
        logger.info(f"Shutdown signal received ({signum}), finishing current step...")

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    logger.info(f"Autopilot started -- preset: {config['autonomy_preset']}")
    notifier.send(f"Autopilot started (preset: {config['autonomy_preset']})")

    completed = 0
    failed = 0

    try:
        if args.story:
            # Single story mode
            success = run_story_cycle(
                args.story, config, project_root, logger, notifier, sprint_path
            )
            completed += 1 if success else 0
            failed += 0 if success else 1
        else:
            # Sprint loop
            epic_filter = args.epic
            while not shutdown_requested:
                sprint_status = load_sprint_status(sprint_path)
                next_story = find_next_story(sprint_status, epic_filter)

                if next_story is None:
                    logger.info("No more stories to process")
                    notifier.send("All stories complete! No more work to do.")
                    break

                story_id, current_status = next_story
                success = run_story_cycle(
                    story_id, config, project_root, logger, notifier, sprint_path
                )

                if success:
                    completed += 1
                else:
                    failed += 1
                    # On failure, continue to next story unless shutdown requested
                    if shutdown_requested:
                        break

            if shutdown_requested:
                logger.info("Graceful shutdown complete")
                notifier.send(
                    f"Autopilot stopped. Completed: {completed}, Failed: {failed}. "
                    f"Resume with: `uv run scripts/autopilot.py resume`"
                )

    finally:
        # Generate dashboard
        logger.info("Generating dashboard...")
        dashboard_script = Path(project_root) / "skills" / "ap-dashboard" / "scripts" / "gather-sprint-data.py"
        if dashboard_script.exists():
            try:
                subprocess.run(
                    ["uv", "run", str(dashboard_script), sprint_path, "-o",
                     str(Path(project_root) / "_bmad-output" / "autopilot" / "sprint-data.json")],
                    cwd=project_root,
                    timeout=30,
                )
            except (subprocess.TimeoutExpired, FileNotFoundError):
                logger.warn("Dashboard data generation failed")

        checkout_main(project_root, logger)
        release_lock(project_root)

        summary = f"Autopilot finished. Completed: {completed}, Failed: {failed}"
        logger.info(summary)
        notifier.send(summary)


def cmd_resume(args, project_root: str):
    """Resume from current sprint state -- equivalent to run with no filters."""
    args.epic = None
    args.story = None
    args.preset = None
    cmd_run(args, project_root)


def cmd_configure(args, project_root: str):
    """Set or update autopilot configuration."""
    config = load_config(project_root)

    if args.preset:
        config["autonomy_preset"] = args.preset
        if args.preset in PRESETS:
            config.update(PRESETS[args.preset])

    if args.telegram_token:
        config["telegram_bot_token"] = args.telegram_token
    if args.telegram_chat_id:
        config["telegram_chat_id"] = args.telegram_chat_id
    if args.dashboard_format:
        config["dashboard_format"] = args.dashboard_format
    if args.project_label:
        config["project_label"] = args.project_label
    if args.retry_budget is not None:
        config["retry_budget"] = args.retry_budget

    save_config(project_root, config)
    print(f"Config saved to {CONFIG_FILE}")
    print(yaml.dump(config, default_flow_style=False))


def cmd_status(_args, project_root: str):
    """Show current autopilot status as JSON."""
    config = load_config(project_root)
    sprint_path = str(Path(project_root) / config["sprint_status_path"])

    lock_path = Path(project_root) / LOCK_FILE
    lock_active = False
    lock_pid = None
    if lock_path.exists():
        lock_pid = lock_path.read_text().strip()
        try:
            os.kill(int(lock_pid), 0)
            lock_active = True
        except (ProcessLookupError, ValueError, PermissionError):
            lock_active = False

    next_story_id = None
    next_story_status = None
    if Path(sprint_path).exists():
        sprint = load_sprint_status(sprint_path)
        next_story = find_next_story(sprint)
        if next_story:
            next_story_id, next_story_status = next_story

    status = {
        "project": config.get("project_label", "unknown"),
        "preset": config.get("autonomy_preset", "checkpoint"),
        "telegram": "configured" if config.get("telegram_bot_token") else "not configured",
        "dashboard_format": config.get("dashboard_format", "html"),
        "lock": {"active": lock_active, "pid": lock_pid},
        "next_story": {"id": next_story_id, "status": next_story_status},
    }

    print(json.dumps(status, indent=2))


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(
        description="BMad Autopilot -- autonomous Phase 4 orchestrator"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # run
    run_parser = subparsers.add_parser("run", help="Run the autopilot loop")
    run_parser.add_argument("--epic", help="Filter to a specific epic (e.g., epic-1)")
    run_parser.add_argument("--story", help="Run a single story (e.g., 1-4-story-name)")
    run_parser.add_argument(
        "--preset", choices=["ghost", "checkpoint", "copilot"],
        help="Override autonomy preset for this run"
    )

    # resume
    subparsers.add_parser("resume", help="Resume from current sprint state")

    # configure
    cfg_parser = subparsers.add_parser("configure", help="Set autopilot configuration")
    cfg_parser.add_argument(
        "--preset", choices=["ghost", "checkpoint", "copilot"],
        help="Autonomy preset"
    )
    cfg_parser.add_argument("--telegram-token", help="Telegram bot token")
    cfg_parser.add_argument("--telegram-chat-id", help="Telegram chat ID")
    cfg_parser.add_argument("--dashboard-format", choices=["html", "markdown"])
    cfg_parser.add_argument("--project-label", help="Project name for notifications")
    cfg_parser.add_argument("--retry-budget", type=int, help="Max retries on dev failure")

    # status
    subparsers.add_parser("status", help="Show autopilot status")

    args = parser.parse_args()

    # Resolve project root (walk up to find .git)
    project_root = os.getcwd()
    while project_root != "/" and not (Path(project_root) / ".git").exists():
        project_root = str(Path(project_root).parent)

    if not (Path(project_root) / ".git").exists():
        print("Error: not in a git repository", file=sys.stderr)
        sys.exit(2)

    {
        "run": cmd_run,
        "resume": cmd_resume,
        "configure": cmd_configure,
        "status": cmd_status,
    }[args.command](args, project_root)


if __name__ == "__main__":
    main()
