"""Ingestion: provider -> quality gate -> store -> ML."""

from alpha_desk.ingest.ml_bridge import (
    TrainingBundle,
    build_training_table,
    load_promoted_bars,
)
from alpha_desk.ingest.service import (
    IngestionService,
    IngestResult,
    LiveRecording,
    default_window,
    summarise,
)
from alpha_desk.ingest.universe import TrialUniverse, build_trial_universe

__all__ = [
    "IngestResult",
    "IngestionService",
    "LiveRecording",
    "TrainingBundle",
    "TrialUniverse",
    "build_training_table",
    "build_trial_universe",
    "default_window",
    "load_promoted_bars",
    "summarise",
]
