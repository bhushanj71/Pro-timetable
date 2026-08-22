"""
Vercel Python serverless entrypoint. Vercel's @vercel/python runtime
detects the `app` ASGI application exported here and routes all requests
configured in vercel.json to it.
"""
import sys
from pathlib import Path

# Ensure the project root is importable when this file is executed as the
# serverless function handler (Vercel's build isolates api/ otherwise).
sys.path.append(str(Path(__file__).resolve().parent.parent))

from app.main import app  # noqa: E402

__all__ = ["app"]
