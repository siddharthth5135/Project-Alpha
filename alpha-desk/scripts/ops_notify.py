"""Attract attention on ops failures (Telegram + Windows toast + beep).

    python scripts/ops_notify.py --level error --title "AlphaDesk FAIL" --body "live capture exit=1"
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import winsound
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO / "src"))

from ops_desk_state import append_journal, ops_dir  # noqa: E402


def _load_dotenv() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    load_dotenv(REPO / ".env", override=False)


def telegram_send(text: str, *, parse_mode: str | None = "HTML") -> str:
    _load_dotenv()
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat:
        return "telegram_skipped_no_creds"
    try:
        import requests

        url = f"https://api.telegram.org/bot{token}/sendMessage"
        payload: dict = {
            "chat_id": chat,
            "text": text[:3900],
            "disable_web_page_preview": True,
        }
        if parse_mode:
            payload["parse_mode"] = parse_mode
        resp = requests.post(url, json=payload, timeout=15)
        if resp.status_code >= 300:
            return f"telegram_http_{resp.status_code}:{resp.text[:120]}"
        return "telegram_ok"
    except Exception as exc:  # noqa: BLE001
        return f"telegram_error:{exc}"


def windows_toast(title: str, body: str) -> str:
    """Best-effort toast; never raises."""
    try:
        import subprocess

        # PowerShell balloon / toast without extra modules
        ps = f"""
Add-Type -AssemblyName System.Windows.Forms
$n = New-Object System.Windows.Forms.NotifyIcon
$n.Icon = [System.Drawing.SystemIcons]::Error
$n.BalloonTipTitle = @'
{title.replace("'", "''")}
'@
$n.BalloonTipText = @'
{body.replace("'", "''")[:200]}
'@
$n.Visible = $true
$n.ShowBalloonTip(8000)
Start-Sleep -Seconds 2
$n.Dispose()
"""
        subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps],
            check=False,
            capture_output=True,
            timeout=20,
        )
        return "toast_ok"
    except Exception as exc:  # noqa: BLE001
        return f"toast_error:{exc}"


def beep(level: str) -> None:
    try:
        if level == "error":
            winsound.MessageBeep(winsound.MB_ICONHAND)
            winsound.Beep(880, 250)
            winsound.Beep(660, 350)
        elif level == "warn":
            winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)
            winsound.Beep(740, 200)
    except Exception:  # noqa: BLE001
        pass


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--level", default="error", choices=("info", "ok", "warn", "error"))
    p.add_argument("--title", default="AlphaDesk")
    p.add_argument("--body", required=True)
    p.add_argument("--phase", default="notify")
    p.add_argument("--fix", default="you", choices=("none", "auto", "you"))
    p.add_argument("--no-toast", action="store_true")
    p.add_argument("--no-beep", action="store_true")
    p.add_argument("--no-telegram", action="store_true")
    args = p.parse_args()

    append_journal(
        args.phase,
        args.level,
        f"{args.title}: {args.body}",
        fix=args.fix,
    )

    results = {}
    if args.level in {"error", "warn", "info"}:
        if not args.no_beep and args.level in {"error", "warn"}:
            beep(args.level)
        if not args.no_toast and args.level in {"error", "warn"}:
            results["toast"] = windows_toast(args.title, args.body)
        if not args.no_telegram:
            results["telegram"] = telegram_send(f"[{args.level.upper()}] {args.title}\n{args.body}")

    flag = ops_dir() / f"ALERT_{args.level}_{Path(args.phase).name}.txt"
    flag.write_text(
        json.dumps({"title": args.title, "body": args.body, "results": results}, indent=2),
        encoding="utf-8",
    )
    print(json.dumps({"ok": True, "results": results}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
