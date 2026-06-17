"""Prediction Lab application entry point.

Run this file locally with ``python3 app.py``. Production imports ``app:app``.
The Flask application is still assembled in the legacy shared core while the
remaining sport-specific code is moved into ``sports/``.
"""

import os

# Force single-threaded native math BEFORE numpy/xgboost load. XGBoost's OpenMP
# thread pool segfaulted gunicorn's threaded workers on the production host
# (site-wide 502 on prediction pages). Pinning these to 1 must happen before the
# native libraries initialize their thread pools, i.e. before importing the app.
for _v in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS",
           "NUMEXPR_NUM_THREADS", "XGBOOST_NTHREAD"):
    os.environ.setdefault(_v, "1")

from NHL77FINAL import app


def main() -> None:
    """Start the local development server."""
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=False)


if __name__ == "__main__":
    main()
