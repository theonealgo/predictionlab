"""Train Tennis models on demo/synthetic data. Replace with real historical dumps later."""
from __future__ import annotations

from tennis.ml import TennisModel


def main() -> None:
    m = TennisModel()
    m.fit_demo(n=200)
    print("Tennis demo train complete. Models:", list(m.ensemble.models))
    print("Feature importance:", m.feature_importance()[:5])


if __name__ == "__main__":
    main()
