"""Build (and optionally Telegram-send) the end-of-day desk report.

Uses Telegram HTML for clean formatting.

    python scripts/ops_daily_report.py              # print only
    python scripts/ops_daily_report.py --demo       # sample layout (no live data required)
    python scripts/ops_daily_report.py --send
"""

from __future__ import annotations

import argparse
import html
import json
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO / "src"))

from ops_desk_state import (  # noqa: E402
    append_journal,
    load_progress,
    load_status,
    now_ist,
    ops_dir,
    read_journal,
)
from ops_notify import telegram_send  # noqa: E402


def _load_dotenv() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    load_dotenv(REPO / ".env", override=False)


def _esc(value: object) -> str:
    return html.escape(str(value), quote=False)


def _gate(ok: bool, waiting_label: str = "WAIT") -> str:
    return "✅ PASS" if ok else f"⏳ {waiting_label}"


def _level_icon(level: str) -> str:
    return {
        "ok": "✅",
        "info": "ℹ️",
        "warn": "⚠️",
        "error": "🔴",
    }.get(level, "•")


def build_report(*, demo: bool = False) -> str:
    if demo:
        return _demo_report()

    status = load_status()
    progress = load_progress()
    today = now_ist().strftime("%Y-%m-%d")
    updated = status.get("updated_at") or "-"
    if isinstance(updated, str) and "T" in updated:
        updated = updated.replace("T", " ")[:19]

    angel_on = bool(status.get("angel_allow_marketdata"))
    attention = bool(status.get("needs_attention"))
    open_err = int(status.get("open_errors") or 0)
    ok_days = int(progress.get("paper_days_ok") or 0)
    fail_days = int(progress.get("paper_days_fail") or 0)
    target = int(progress.get("target_paper_days") or 20)
    left = int(progress.get("est_days_to_live") or target)
    gates = progress.get("gates") or {}
    last_paper = progress.get("last_paper") or {}
    ready = bool(progress.get("go_live_ready"))

    health = "🔴 Needs attention" if attention else "🟢 Healthy"
    angel = "🟢 ON" if angel_on else "🟡 OFF (no API yet)"

    # Compact journal: one line per phase outcome today (prefer last ok/error/warn)
    todays = [r for r in read_journal(200) if str(r.get("ts", "")).startswith(today)]
    phase_last: dict[str, dict] = {}
    for row in todays:
        phase = str(row.get("phase") or "?")
        if row.get("level") in {"ok", "warn", "error", "info"}:
            phase_last[phase] = row

    pipeline_order = ["preopen", "live", "eod", "paper", "train", "report"]
    pipeline_lines: list[str] = []
    for phase in pipeline_order:
        row = phase_last.get(phase)
        if not row:
            pipeline_lines.append(f"• <b>{_esc(phase)}</b>: —")
            continue
        icon = _level_icon(str(row.get("level")))
        msg = _esc(str(row.get("message") or ""))[:90]
        ts = str(row.get("ts", ""))[11:16]
        pipeline_lines.append(f"• <b>{_esc(phase)}</b> {icon} <i>{ts}</i> — {msg}")

    # Recent errors only (max 5), short
    errors = [r for r in todays if r.get("level") == "error"][-5:]
    if errors:
        err_block = "\n".join(
            f"• {_esc(str(r.get('ts', ''))[11:16])} [{_esc(r.get('phase'))}] "
            f"{_esc(str(r.get('message') or '')[:100])}"
            for r in errors
        )
    else:
        err_block = "• none"

    paper_line = (
        f"score={_esc(last_paper.get('score'))}  signal={_esc(last_paper.get('signal'))}  "
        f"ok={_esc(last_paper.get('ok'))}"
        if last_paper
        else "no paper run yet"
    )

    ask = (
        "🟡 <b>ASK-LIVE ready</b> — still needs YOUR written GO (1 lot only)."
        if ready
        else f"⏳ Ask-live in ~<b>{left}</b> good paper day(s)."
    )

    return "\n".join(
        [
            f"<b>📊 AlphaDesk Daily Report</b>",
            f"<code>{_esc(today)}</code>",
            "",
            f"<b>Status</b>  {health}",
            f"Updated     <code>{_esc(updated)}</code>",
            f"Angel data  {angel}",
            f"Open errors <code>{open_err}</code>",
            "",
            "<b>📈 Paper progress</b>",
            f"Good days   <code>{ok_days}/{target}</code>   Fail <code>{fail_days}</code>",
            f"Last paper  <code>{paper_line}</code>",
            "",
            "<b>🚪 Gates</b>",
            f"G1 Feed           {_gate(bool(gates.get('G1_feed')))}",
            f"G2 Ops stable     {_gate(bool(gates.get('G2_ops_stable')))}",
            f"G3 Edge review    {_gate(bool(gates.get('G3_edge_after_costs')), 'YOU review')}",
            f"G5 Live GO        {_gate(bool(gates.get('G5_live_go')), 'BLOCKED')}",
            "",
            "<b>🛠️ Today pipeline</b>",
            *pipeline_lines,
            "",
            "<b>🔴 Errors (today)</b>",
            err_block,
            "",
            "<b>🔧 Fix before next day</b>",
            *_fix_before_next_day(status, progress, todays, angel_on),
            "",
            ask,
            "🔒 Orders: <b>OFF</b> until Gate G5.",
        ]
    )


def _fix_before_next_day(
    status: dict,
    progress: dict,
    todays: list[dict],
    angel_on: bool,
) -> list[str]:
    """Actionable bullets so the team knows what to fix tonight."""
    actions: list[str] = []
    if not angel_on:
        actions.append(
            "1. Set <code>ANGELONE_ALLOW_MARKETDATA=true</code> only after you approve "
            "Angel market-data (then capture can run)."
        )
    err_phases = {str(r.get("phase")) for r in todays if r.get("level") == "error"}
    if "paper" in err_phases:
        actions.append(
            "2. Paper failed — usually <b>no bars yet</b>. After Angel is ON, run live "
            "capture; paper needs stored NIFTY bars."
        )
    if "train" in err_phases:
        actions.append(
            "3. Train failed — need enough Angel-stored history. Fix capture/backfill first."
        )
    if "live" in err_phases:
        actions.append(
            "4. Live capture failed — check Angel login/TOTP, internet, and PC awake at 09:15."
        )
    if "preopen" in err_phases or "eod" in err_phases:
        actions.append(
            "5. Preopen/EOD failed — open console (red lines) and fix the flagged step."
        )
    if not actions:
        actions.append("• Nothing blocking — keep PC ON for tomorrow’s schedule.")
    # Always remind about console
    actions.append(
        "• See full detail: desktop <b>AlphaDesk OPS Console</b> (red = you fix)."
    )
    return actions


def _demo_report() -> str:
    today = now_ist().strftime("%Y-%m-%d")
    return "\n".join(
        [
            f"<b>📊 AlphaDesk Daily Report</b>",
            f"<code>{today}</code>  <i>(DEMO layout)</i>",
            "",
            "<b>Status</b>  🟢 Healthy",
            f"Updated     <code>{today} 16:50:02</code>",
            "Angel data  🟢 ON",
            "Open errors <code>0</code>",
            "",
            "<b>📈 Paper progress</b>",
            "Good days   <code>3/20</code>   Fail <code>0</code>",
            "Last paper  <code>score=0.41  signal=FLAT  ok=True</code>",
            "",
            "<b>🚪 Gates</b>",
            "G1 Feed           ✅ PASS",
            "G2 Ops stable     ⏳ WAIT",
            "G3 Edge review    ⏳ YOU review",
            "G5 Live GO        ⏳ BLOCKED",
            "",
            "<b>🛠️ Today pipeline</b>",
            "• <b>preopen</b> ✅ <i>09:01</i> — Pre-market checks PASSED",
            "• <b>live</b> ✅ <i>15:30</i> — Live capturing DONE for this window",
            "• <b>eod</b> ✅ <i>15:50</i> — Post-market DONE",
            "• <b>paper</b> ✅ <i>16:08</i> — Paper done signal=FLAT score=0.41",
            "• <b>train</b> ✅ <i>16:40</i> — Model training DONE",
            "• <b>report</b> ✅ <i>16:50</i> — Daily report sent",
            "",
            "<b>🔴 Errors (today)</b>",
            "• none",
            "",
            "<b>🔧 Fix before next day</b>",
            "• Nothing blocking — keep PC ON for tomorrow’s schedule.",
            "• See full detail: desktop <b>AlphaDesk OPS Console</b> (red = you fix).",
            "",
            "⏳ Ask-live in ~<b>17</b> good paper day(s).",
            "🔒 Orders: <b>OFF</b> until Gate G5.",
        ]
    )


def send_report(text: str) -> dict[str, str]:
    _load_dotenv()
    results: dict[str, str] = {}
    ids: list[str] = []
    primary = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if primary:
        ids.append(primary)
    extra = os.environ.get("TELEGRAM_CHAT_IDS", "").strip()
    if extra:
        ids.extend(x.strip() for x in extra.split(",") if x.strip())
    seen: set[str] = set()
    uniq = []
    for i in ids:
        if i not in seen:
            seen.add(i)
            uniq.append(i)
    if not uniq:
        return {"telegram": "telegram_skipped_no_creds"}
    if not os.environ.get("TELEGRAM_BOT_TOKEN", "").strip():
        return {"telegram": "telegram_skipped_no_token"}

    for chat in uniq:
        os.environ["TELEGRAM_CHAT_ID"] = chat
        results[chat] = telegram_send(text, parse_mode="HTML")
    if primary:
        os.environ["TELEGRAM_CHAT_ID"] = primary
    return results


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--send", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--demo", action="store_true", help="print demo layout only")
    args = p.parse_args()

    text = build_report(demo=args.demo)
    plain_path = ops_dir() / f"daily_report_{now_ist().strftime('%Y%m%d')}.html.txt"
    plain_path.write_text(text, encoding="utf-8")
    try:
        print(text)
    except UnicodeEncodeError:
        sys.stdout.buffer.write((text + "\n").encode("utf-8", errors="replace"))
    print(f"\n[saved] {plain_path}")

    if args.demo:
        return 0

    append_journal("report", "info", f"Daily report built -> {plain_path.name}")

    if args.send and not args.dry_run:
        results = send_report(text)
        print(json.dumps({"send": results}, indent=2))
        if any(v == "telegram_ok" for v in results.values()):
            append_journal("report", "ok", "Daily report sent to Telegram", fix="none")
            return 0
        if all("skipped" in v for v in results.values()):
            append_journal(
                "report",
                "warn",
                "Telegram not configured - report saved locally only.",
                fix="you",
            )
            return 0
        append_journal("report", "error", f"Telegram send failed: {results}", fix="you")
        return 1

    if args.send and args.dry_run:
        print("[dry-run] would send Telegram (skipped)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
