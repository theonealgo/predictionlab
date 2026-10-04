"""Rollback shim: legacy Markov Takedown preserved from pre-proto_v1 soccer_models.

Kill-switch (preferred):
  export TAKEDOWN_MODEL_VERSION=legacy

Or import MarkovChainModelLegacy from this module / soccer_models.
"""
from soccer_models import MarkovChainModel, MarkovChainModelLegacy

__all__ = ["MarkovChainModel", "MarkovChainModelLegacy"]
