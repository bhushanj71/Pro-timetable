"""
Vercel Python serverless entrypoint. Vercel's @vercel/python runtime
detects the `app` ASGI application exported here and routes all requests
configured in vercel.json to it.
"""
import sys
import traceback
from pathlib import Path

# Ensure the project root is importable when this file is executed as the
# serverless function handler (Vercel's build isolates api/ otherwise).
sys.path.append(str(Path(__file__).resolve().parent.parent))

try:
    from app.main import app  # noqa: E402
except Exception as exc:  # pragma: no cover - only runs on a broken deployment
    # An exception while importing the application gives the platform nothing
    # but "FUNCTION_INVOCATION_FAILED" on every route, with the real cause
    # visible only in logs the deployer may not be able to reach. Serve a tiny
    # ASGI app that reports the failure instead, so the deployment can be
    # diagnosed over HTTP.
    _detail = f"{type(exc).__name__}: {exc}"
    _missing = getattr(exc, "name", None)
    traceback.print_exc()

    async def app(scope, receive, send):  # type: ignore[misc]
        if scope["type"] != "http":
            return
        body = (
            "ProfSchedule AI failed to start.\n\n"
            f"{_detail}\n"
            + (f"missing module: {_missing}\n" if _missing else "")
            + f"\npython {sys.version.split()[0]}\n"
            "\nThis is a deployment/packaging problem, not a request error.\n"
        ).encode()
        await send({
            "type": "http.response.start",
            "status": 503,
            "headers": [(b"content-type", b"text/plain; charset=utf-8"),
                        (b"cache-control", b"no-store")],
        })
        await send({"type": "http.response.body", "body": body})

__all__ = ["app"]
