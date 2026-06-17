"""Prediction Lab application entry point.

Run this file locally with ``python3 app.py``. Production imports ``app:app``.
The Flask application is still assembled in the legacy shared core while the
remaining sport-specific code is moved into ``sports/``.
"""

import os

from NHL77FINAL import app


def main() -> None:
    """Start the local development server."""
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=False)


if __name__ == "__main__":
    main()
