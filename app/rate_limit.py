"""
A small in-process rate limiter for credential endpoints.

Login had no throttle at all, so an attacker could try passwords as fast as
the network allowed. This is deliberately simple -- a fixed window in memory,
no Redis -- which is the right size for a single-instance deployment. It is
per-process, so it does not coordinate across replicas; that is a reason to
add a shared store when scaling out, not a reason to ship nothing.
"""
import time
from collections import defaultdict, deque

from fastapi import HTTPException, Request, status


class RateLimiter:
    def __init__(self, max_attempts: int, window_seconds: int):
        self.max_attempts = max_attempts
        self.window = window_seconds
        self._hits: dict[str, deque] = defaultdict(deque)

    def _key(self, request: Request, extra: str | None) -> str:
        # X-Forwarded-For is set by Render/Vercel; fall back to the socket peer.
        fwd = request.headers.get("x-forwarded-for", "")
        ip = fwd.split(",")[0].strip() if fwd else (request.client.host if request.client else "unknown")
        return f"{ip}|{extra or ''}"

    def check(self, request: Request, extra: str | None = None) -> None:
        now = time.monotonic()
        hits = self._hits[self._key(request, extra)]
        while hits and now - hits[0] > self.window:
            hits.popleft()

        if len(hits) >= self.max_attempts:
            retry_after = int(self.window - (now - hits[0])) + 1
            raise HTTPException(
                status.HTTP_429_TOO_MANY_REQUESTS,
                "Too many attempts. Please wait a moment and try again.",
                headers={"Retry-After": str(retry_after)},
            )
        hits.append(now)

        # Keep the dict from growing without bound on a long-lived process.
        if len(self._hits) > 10_000:
            for k in [k for k, v in self._hits.items() if not v or now - v[-1] > self.window]:
                self._hits.pop(k, None)

    def reset(self, request: Request, extra: str | None = None) -> None:
        """Clear the counter after a success, so one bad typo doesn't count
        against someone who then signs in correctly."""
        self._hits.pop(self._key(request, extra), None)


# Login is keyed on address *and* email, so five attempts per five minutes
# throttles guessing one account without stopping a colleague on the same
# campus NAT from signing in.
login_limiter = RateLimiter(max_attempts=5, window_seconds=300)

# Registration can only be keyed on the address, and a whole department may
# share one. Loose enough for a real cohort signing up together, tight enough
# that scripted account creation stalls.
register_limiter = RateLimiter(max_attempts=20, window_seconds=3600)


def reset_all() -> None:
    """Clear every counter. Used by the test suite, which drives hundreds of
    logins from one address inside a single process."""
    for limiter in (login_limiter, register_limiter):
        limiter._hits.clear()
