"""Feature registry, schema and freeze mechanism.

A feature set is frozen before out-of-sample evaluation. The lock file records
the feature names, their lookbacks and a fingerprint of each implementation, so
that changing what `rsi_14` *means* while keeping its name is caught rather than
silently absorbed into the next backtest.

The implementation fingerprint hashes normalised source: comments, docstrings
and blank lines are stripped, so reformatting is free but a logic change is not.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

import pandas as pd

FeatureFn = Callable[[pd.DataFrame], pd.Series]

_COMMENT = re.compile(r"#.*$")
_DOCSTRING = re.compile(r'("""|\'\'\')(?:.|\n)*?\1')


class FeatureFreezeError(RuntimeError):
    pass


class FeatureBuildError(RuntimeError):
    pass


def _fingerprint(fn: FeatureFn) -> str:
    try:
        source = inspect.getsource(fn)
    except (OSError, TypeError):  # pragma: no cover - lambdas from a REPL
        return "unavailable"
    source = _DOCSTRING.sub("", source)
    lines = [_COMMENT.sub("", line).rstrip() for line in source.splitlines()]
    normalised = "\n".join(line for line in lines if line.strip())
    return hashlib.sha256(normalised.encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True)
class FeatureSpec:
    name: str
    lookback: int
    fn: FeatureFn
    description: str

    def __post_init__(self) -> None:
        if self.lookback < 0:
            raise ValueError(f"{self.name}: lookback cannot be negative")

    @property
    def implementation_hash(self) -> str:
        return _fingerprint(self.fn)

    def as_schema(self) -> dict[str, object]:
        return {
            "name": self.name,
            "lookback": self.lookback,
            "description": self.description,
            "implementation_hash": self.implementation_hash,
        }


class FeatureSet:
    def __init__(self, name: str, version: str, specs: Iterable[FeatureSpec]) -> None:
        self.name = name
        self.version = version
        self.specs = tuple(specs)
        if not self.specs:
            raise ValueError("a feature set needs at least one feature")
        names = [s.name for s in self.specs]
        duplicates = {n for n in names if names.count(n) > 1}
        if duplicates:
            raise ValueError(f"duplicate feature names: {sorted(duplicates)}")

    # -- identity ------------------------------------------------------ #

    @property
    def feature_names(self) -> tuple[str, ...]:
        return tuple(s.name for s in self.specs)

    @property
    def max_lookback(self) -> int:
        return max(s.lookback for s in self.specs)

    def schema(self) -> dict[str, object]:
        body = {
            "name": self.name,
            "version": self.version,
            "max_lookback": self.max_lookback,
            "features": [s.as_schema() for s in sorted(self.specs, key=lambda s: s.name)],
        }
        body["hash"] = hashlib.sha256(
            json.dumps(body, sort_keys=True).encode("utf-8")
        ).hexdigest()
        return body

    @property
    def hash(self) -> str:
        return str(self.schema()["hash"])

    @property
    def short_hash(self) -> str:
        return self.hash[:12]

    # -- build --------------------------------------------------------- #

    def build(self, bars: pd.DataFrame, *, drop_warmup: bool = True) -> pd.DataFrame:
        """Compute every feature. Rows inside the warmup window are dropped."""
        if bars.empty:
            return pd.DataFrame(columns=["ts", *self.feature_names])

        frame = bars.sort_values("ts").reset_index(drop=True)
        out = pd.DataFrame({"ts": frame["ts"]})
        for spec in self.specs:
            try:
                values = spec.fn(frame)
            except Exception as exc:  # noqa: BLE001 - surface which feature broke
                raise FeatureBuildError(f"feature {spec.name!r} failed: {exc}") from exc
            if len(values) != len(frame):
                raise FeatureBuildError(
                    f"feature {spec.name!r} returned {len(values)} rows, expected {len(frame)}"
                )
            out[spec.name] = pd.Series(values).to_numpy()

        if drop_warmup:
            out = out.iloc[self.max_lookback :].reset_index(drop=True)
        return out

    # -- freeze -------------------------------------------------------- #

    def lock_path(self, lock_dir: Path) -> Path:
        return Path(lock_dir) / f"{self.name}_{self.version}.json"

    def freeze(self, lock_dir: Path) -> Path:
        """Write the lock file. Refuses to overwrite a differing lock."""
        path = self.lock_path(lock_dir)
        path.parent.mkdir(parents=True, exist_ok=True)
        schema = self.schema()
        if path.exists():
            existing = json.loads(path.read_text(encoding="utf-8"))
            if existing.get("hash") != schema["hash"]:
                raise FeatureFreezeError(
                    f"{path.name} already exists with a different hash. Bump the feature set "
                    "version and open a new trial instead of editing a frozen set."
                )
            return path
        path.write_text(json.dumps(schema, indent=2, sort_keys=True), encoding="utf-8")
        return path

    def verify_freeze(self, lock_dir: Path) -> None:
        """Fail closed when the code no longer matches the frozen schema."""
        path = self.lock_path(lock_dir)
        if not path.exists():
            raise FeatureFreezeError(
                f"no lock file at {path}. Freeze the feature set before evaluation."
            )
        locked = json.loads(path.read_text(encoding="utf-8"))
        current = self.schema()
        if locked.get("hash") == current["hash"]:
            return

        locked_features = {f["name"]: f for f in locked.get("features", [])}
        current_features = {f["name"]: f for f in current["features"]}
        added = sorted(set(current_features) - set(locked_features))
        removed = sorted(set(locked_features) - set(current_features))
        changed = sorted(
            name
            for name in set(locked_features) & set(current_features)
            if locked_features[name] != current_features[name]
        )
        raise FeatureFreezeError(
            "feature set no longer matches its lock file "
            f"(added={added}, removed={removed}, changed={changed})"
        )
