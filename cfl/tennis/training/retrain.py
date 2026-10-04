"""Retrain hook after Tennis event/game completion."""
from __future__ import annotations

from tennis.training.train import main as train_main


def retrain_after_event(event_id: str | None = None) -> None:
    # Stub: pull new graded rows → refit. For now re-runs demo fit.
    print(f"[{event_id}] retrain hook starting for Tennis")
    train_main()


if __name__ == "__main__":
    retrain_after_event("latest")
