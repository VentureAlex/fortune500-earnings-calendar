#!/usr/bin/env python3
"""
Local development server.
Run: python run.py
Then open http://localhost:8000

The FastAPI app (api/index.py) serves:
  • /api/*   — JSON endpoints
  • /*       — static files from public/
"""

import sys
import os
from pathlib import Path

# Make sure project root is on the path so `api` is importable
sys.path.insert(0, str(Path(__file__).parent))

import uvicorn

if __name__ == "__main__":
    uvicorn.run(
        "api.index:app",
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 8000)),
        reload=True,
        reload_dirs=["api", "public"],
        log_level="info",
    )
