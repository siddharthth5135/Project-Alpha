"""Versioned model artifacts.

A bundle is the only thing inference may load. It binds the fitted object to the
feature-set hash and label definition it was trained on. A mismatch at load
time fails closed.

Plumbing sources (yahoo, synthetic) can never be promote_eligible=True.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import joblib

PLUMBING_SOURCES = frozenset({"yahoo", "synthetic"})


@dataclass
class ArtifactMeta:
    model_id: str
    model_kind: str  # ridge_alpha | lgbm_alpha | loss_hit_stop
    version: str
    feature_names: list[str]
    feature_set_hash: str
    label_def_id: str
    target_column: str
    source: str
    symbols: list[str]
    metrics: dict[str, Any] = field(default_factory=dict)
    train_rows: int = 0
    test_rows: int = 0
    created_at: str = ""
    promote_eligible: bool = False
    notes: str = ""

    def __post_init__(self) -> None:
        if not self.created_at:
            self.created_at = datetime.now(timezone.utc).isoformat()
        if self.source in PLUMBING_SOURCES:
            self.promote_eligible = False

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def content_hash(self) -> str:
        body = {
            k: v
            for k, v in self.as_dict().items()
            if k not in {"created_at", "notes"}
        }
        return hashlib.sha256(json.dumps(body, sort_keys=True, default=str).encode()).hexdigest()[:16]


class ArtifactStore:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def path_for(self, meta: ArtifactMeta) -> Path:
        return self.root / meta.model_kind / meta.model_id

    def save(self, model: Any, meta: ArtifactMeta) -> Path:
        if meta.source in PLUMBING_SOURCES:
            meta.promote_eligible = False
            if not meta.notes:
                meta.notes = f"source={meta.source} is plumbing; not promote-eligible"
        folder = self.path_for(meta)
        folder.mkdir(parents=True, exist_ok=True)
        joblib.dump(model, folder / "model.joblib")
        payload = meta.as_dict()
        payload["content_hash"] = meta.content_hash
        (folder / "meta.json").write_text(
            json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8"
        )
        return folder

    def load(self, model_kind: str, model_id: str) -> tuple[Any, ArtifactMeta]:
        folder = self.root / model_kind / model_id
        meta_path = folder / "meta.json"
        model_path = folder / "model.joblib"
        if not meta_path.exists() or not model_path.exists():
            raise FileNotFoundError(f"missing artifact at {folder}")
        raw = json.loads(meta_path.read_text(encoding="utf-8"))
        raw.pop("content_hash", None)
        meta = ArtifactMeta(**raw)
        model = joblib.load(model_path)
        return model, meta

    def latest(self, model_kind: str) -> tuple[Any, ArtifactMeta] | None:
        base = self.root / model_kind
        if not base.exists():
            return None
        dirs = sorted([p for p in base.iterdir() if p.is_dir()], key=lambda p: p.name)
        if not dirs:
            return None
        return self.load(model_kind, dirs[-1].name)
