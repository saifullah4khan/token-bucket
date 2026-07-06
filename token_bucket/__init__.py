"""A small, dependency-free token-bucket rate limiter.

Use it to pace outbound calls so you stay under a third-party API's rate
limit instead of discovering the limit the hard way (a burst of 429s).

The public surface is deliberately tiny:

    from token_bucket import TokenBucket, rate_limited

    bucket = TokenBucket(rate=5, capacity=5)   # 5 requests/second, burst of 5

    if bucket.try_acquire():
        do_the_call()          # non-blocking: skip or shed load when empty

    bucket.acquire()           # blocking: wait until a token is free

    @rate_limited(bucket)
    def call_api(...):
        ...

Everything that touches wall-clock time is injectable (``clock`` and
``sleep``), which is what makes the behaviour deterministic under test.
"""

from __future__ import annotations

import functools
import threading
import time
from typing import Callable, TypeVar

__all__ = ["TokenBucket", "rate_limited"]
__version__ = "0.1.0"

F = TypeVar("F", bound=Callable[..., object])


class TokenBucket:
    """A thread-safe token bucket.

    Tokens are added continuously at ``rate`` tokens per second up to a
    ceiling of ``capacity`` tokens. Each unit of work costs one or more
    tokens; work proceeds only when the bucket holds enough. ``capacity``
    is the largest burst you will ever allow through at once; ``rate`` is
    the sustained throughput once that burst is spent.

    Refill is computed lazily from elapsed time, so there is no background
    thread and no timer to clean up.

    Args:
        rate: Tokens replenished per second. Must be > 0.
        capacity: Maximum tokens the bucket can hold (the burst size).
            Defaults to ``rate`` (one second of burst). Must be > 0.
        clock: Zero-argument callable returning a monotonically increasing
            time in seconds. Defaults to :func:`time.monotonic`. Inject a
            fake clock in tests.
        sleep: One-argument callable that sleeps for the given number of
            seconds. Defaults to :func:`time.sleep`. Only used by the
            blocking :meth:`acquire`.
        start_full: If True (default) the bucket starts at ``capacity`` so
            an initial burst is allowed. If False it starts empty.
    """

    def __init__(
        self,
        rate: float,
        capacity: float | None = None,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        start_full: bool = True,
    ) -> None:
        if rate <= 0:
            raise ValueError("rate must be greater than 0")
        if capacity is None:
            capacity = rate
        if capacity <= 0:
            raise ValueError("capacity must be greater than 0")

        self.rate = float(rate)
        self.capacity = float(capacity)
        self._clock = clock
        self._sleep = sleep
        self._lock = threading.Lock()
        self._tokens = self.capacity if start_full else 0.0
        self._last = clock()

    # -- internal ---------------------------------------------------------

    def _refill_locked(self) -> None:
        """Add tokens accrued since the last check. Caller holds the lock."""
        now = self._clock()
        elapsed = now - self._last
        # A non-monotonic clock (or none at all) should never remove tokens.
        if elapsed > 0:
            self._tokens = min(self.capacity, self._tokens + elapsed * self.rate)
            self._last = now

    # -- inspection -------------------------------------------------------

    @property
    def tokens(self) -> float:
        """Tokens currently available (refilled to the present moment)."""
        with self._lock:
            self._refill_locked()
            return self._tokens

    def time_until_available(self, tokens: float = 1.0) -> float:
        """Seconds until ``tokens`` tokens would be available.

        Returns 0.0 if they are available right now. Raises ``ValueError``
        if ``tokens`` exceeds the bucket capacity, since that request can
        never be satisfied.
        """
        self._check_request(tokens)
        with self._lock:
            self._refill_locked()
            deficit = tokens - self._tokens
            if deficit <= 0:
                return 0.0
            return deficit / self.rate

    # -- acquisition ------------------------------------------------------

    def try_acquire(self, tokens: float = 1.0) -> bool:
        """Consume ``tokens`` if available. Never blocks.

        Returns True and deducts the tokens on success, or False and
        changes nothing if the bucket is short.
        """
        self._check_request(tokens)
        with self._lock:
            self._refill_locked()
            if self._tokens >= tokens:
                self._tokens -= tokens
                return True
            return False

    def acquire(self, tokens: float = 1.0, timeout: float | None = None) -> bool:
        """Block until ``tokens`` can be consumed, then consume them.

        Args:
            tokens: How many tokens this call costs.
            timeout: Maximum seconds to wait. ``None`` (default) waits as
                long as needed. ``0`` behaves like :meth:`try_acquire`.

        Returns True once the tokens are consumed, or False if the timeout
        elapsed first (in which case nothing is consumed).
        """
        self._check_request(tokens)
        deadline = None if timeout is None else self._clock() + timeout

        while True:
            with self._lock:
                self._refill_locked()
                if self._tokens >= tokens:
                    self._tokens -= tokens
                    return True
                wait = (tokens - self._tokens) / self.rate

            if deadline is not None:
                remaining = deadline - self._clock()
                if remaining <= 0:
                    return False
                wait = min(wait, remaining)

            self._sleep(wait)

    def _check_request(self, tokens: float) -> None:
        if tokens <= 0:
            raise ValueError("tokens must be greater than 0")
        if tokens > self.capacity:
            raise ValueError(
                f"requested {tokens} tokens but bucket capacity is "
                f"{self.capacity}; this can never be satisfied"
            )


def rate_limited(bucket: TokenBucket, tokens: float = 1.0) -> Callable[[F], F]:
    """Decorator that blocks on ``bucket`` before each call.

    Example::

        limiter = TokenBucket(rate=2, capacity=2)

        @rate_limited(limiter)
        def fetch(page):
            ...
    """

    def decorator(func: F) -> F:
        @functools.wraps(func)
        def wrapper(*args: object, **kwargs: object) -> object:
            bucket.acquire(tokens)
            return func(*args, **kwargs)

        return wrapper  # type: ignore[return-value]

    return decorator
