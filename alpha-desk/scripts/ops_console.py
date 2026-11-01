"""Alpha-desk transparent ops console (view-only).

Work keeps running in Task Scheduler even if you close this window.
Re-open anytime — history comes back from logs/ops/journal.jsonl.

    python scripts/ops_console.py
    python scripts/ops_console.py --once
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))

from ops_desk_state import (  # noqa: E402
    load_progress,
    load_status,
    progress_summary_lines,
    read_journal,
)

# Windows ANSI
os.system("")  # enable VT on Windows

RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"
RED = "\033[91m"
YELLOW = "\033[93m"
GREEN = "\033[92m"
CYAN = "\033[96m"
WHITE = "\033[97m"
BG_RED = "\033[41m"
BG_YELLOW = "\033[43m"
BLACK = "\033[30m"


def color_for(level: str) -> str:
    return {
        "error": RED,
        "warn": YELLOW,
        "ok": GREEN,
        "info": CYAN,
    }.get(level, WHITE)


def fix_label(fix: str) -> str:
    return {
        "auto": f"{GREEN}AUTO-FIX / RETRY{RESET}",
        "you": f"{BG_RED}{WHITE}{BOLD} YOU MUST FIX {RESET}",
        "none": f"{DIM}n/a{RESET}",
    }.get(fix, fix)


def render_header() -> list[str]:
    status = load_status()
    progress = load_progress()
    lines = [
        f"{BOLD}{WHITE}========== AlphaDesk OPS CONSOLE (view-only) =========={RESET}",
        f"{DIM}Closing this window does NOT stop capture/train/paper."
        f" Task Scheduler keeps working while PC is ON.{RESET}",
        f"{DIM}Re-open: powershell -File scripts\\ops_open_console.ps1{RESET}",
        "",
    ]
    if status.get("needs_attention"):
        lines.append(
            f"{BG_RED}{WHITE}{BOLD} !! ATTENTION: {status.get('open_errors', 0)} "
            f"open error(s) - check red lines below !! {RESET}"
        )
        lines.append("")
    allow = status.get("angel_allow_marketdata")
    lines.append(
        f"SoR Angel allow={GREEN+'ON' if allow else YELLOW+'OFF (no API yet)'}{RESET}  "
        f"mode={status.get('mode')}  "
        f"last={status.get('last_phase')} / {status.get('last_level')}"
    )
    lines.append(f"Last msg: {status.get('last_message')}")
    lines.append(f"Who fixes last issue: {fix_label(str(status.get('last_fix') or 'none'))}")
    lines.append("")
    lines.append(f"{BOLD}--- Progress / Gates ---{RESET}")
    for line in progress_summary_lines():
        lines.append(f"  {line}")
    ready = progress.get("go_live_ready")
    if ready:
        lines.append(
            f"  {YELLOW}{BOLD}Ready to ASK for tiny live (1 lot) - still needs your written GO."
            f" I will NOT go live alone.{RESET}"
        )
    else:
        left = progress.get("est_days_to_live", 20)
        lines.append(f"  {CYAN}Keep paper daily. Roughly {left} good day(s) left before ask-live.{RESET}")
    lines.append("")
    lines.append(f"{BOLD}--- Journal (newest at bottom) ---{RESET}")
    return lines


def render_event(row: dict) -> str:
    level = str(row.get("level", "info"))
    col = color_for(level)
    ts = str(row.get("ts", ""))[11:19]
    phase = row.get("phase", "?")
    msg = row.get("message", "")
    fix = row.get("fix", "none")
    prefix = {
        "error": "ERR ",
        "warn": "WARN",
        "ok": "OK  ",
        "info": "INFO",
    }.get(level, "INFO")
    line = f"{DIM}{ts}{RESET} {col}{BOLD}{prefix}{RESET} [{phase}] {col}{msg}{RESET}"
    if fix in {"you", "auto"}:
        line += f"  fix={fix_label(fix)}"
    return line


def loop(refresh: float, once: bool) -> int:
    seen = 0
    while True:
        # Clear screen each refresh for a dashboard feel
        print("\033[2J\033[H", end="")
        for line in render_header():
            print(line)
        rows = read_journal(limit=80)
        for row in rows:
            print(render_event(row))
        if not rows:
            print(f"{DIM}(no journal yet — waiting for first scheduled job){RESET}")
        print()
        print(
            f"{DIM}Refreshing every {refresh:.0f}s | Ctrl+C exits console only "
            f"(jobs keep running){RESET}"
        )
        # Flash attention if new errors arrived
        if len(rows) > seen:
            new = rows[seen:]
            if any(r.get("level") == "error" for r in new):
                print(f"\n{BG_RED}{WHITE}{BOLD} NEW ERROR - shift focus here ASAP {RESET}\n")
            seen = len(rows)
        if once:
            return 0
        try:
            time.sleep(refresh)
        except KeyboardInterrupt:
            print(f"\n{DIM}Console closed. Scheduled work continues.{RESET}")
            return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--refresh", type=float, default=5.0)
    p.add_argument("--once", action="store_true", help="print snapshot and exit")
    args = p.parse_args()
    return loop(args.refresh, args.once)


if __name__ == "__main__":
    raise SystemExit(main())
