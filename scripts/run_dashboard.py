#!/usr/bin/env python3
"""
Run the Vector Odyssey dashboard backend for local development.

Requires the optional `dashboard` dependency group:
    pip install -e ".[dashboard]"   (or: uv sync --extra dashboard)

Then:
    python scripts/run_dashboard.py

and open http://127.0.0.1:8000/ in a browser. The page will show
DISCONNECTED until a real EA process (Phase 10+) connects to
127.0.0.1:8765 (configurable via VO_EA_TELEMETRY_HOST/PORT) and starts
writing newline-delimited RuntimeState JSON -- see dashboard/backend/ea_link.py.
"""

from __future__ import annotations

import uvicorn


def main() -> None:
    uvicorn.run(
        "dashboard.backend.app:app",
        host="127.0.0.1",
        port=8000,
        reload=True,
    )


if __name__ == "__main__":
    main()
