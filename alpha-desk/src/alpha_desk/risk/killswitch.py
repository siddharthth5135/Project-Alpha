"""Kill switch.

Backed by a file on disk so it can be engaged by a human without the process
running, and observed by every process that shares the data root. Engaging is
cheap; clearing requires a written reason that lands in the audit trail.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path


class KillSwitch:
    FILENAME = "KILL_SWITCH.json"

    def __init__(self, state_dir: Path) -> None:
        self._path = Path(state_dir) / self.FILENAME
        self._path.parent.mkdir(parents=True, exist_ok=True)

    @property
    def path(self) -> Path:
        return self._path

    def is_engaged(self) -> bool:
        return self._path.exists()

    def reason(self) -> str:
        if not self.is_engaged():
            return ""
        try:
            return json.loads(self._path.read_text(encoding="utf-8")).get("reason", "")
        except (json.JSONDecodeError, OSError):
            # An unreadable kill file still means "stop trading".
            return "unreadable kill switch file"

    def engage(self, reason: str, engaged_by: str = "system") -> None:
        payload = {
            "reason": reason,
            "engaged_by": engaged_by,
            "engaged_at": datetime.now(timezone.utc).isoformat(),
        }
        self._path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def clear(self, cleared_by: str, written_reason: str) -> None:
        if not cleared_by.strip() or not written_reason.strip():
            raise ValueError("clearing the kill switch requires an owner and a written reason")
        self._path.unlink(missing_ok=True)
