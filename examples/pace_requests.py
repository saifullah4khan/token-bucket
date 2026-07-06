"""Pace a burst of outbound calls so you never exceed 5 requests/second.

Run it:  python examples/pace_requests.py

The bucket starts full, so the first 5 calls fire immediately (the allowed
burst), and every call after that is spaced ~0.2s apart at the 5/sec rate.
"""

import time

from token_bucket import TokenBucket, rate_limited

# 5 requests/second, with a burst allowance of 5.
limiter = TokenBucket(rate=5, capacity=5)


@rate_limited(limiter)
def call_api(n: int) -> None:
    print(f"{time.monotonic():7.3f}s  ->  request {n}")


if __name__ == "__main__":
    print("firing 12 requests through a 5/sec limiter...\n")
    for i in range(1, 13):
        call_api(i)
    print("\ndone. Note the first 5 burst, then a steady ~0.2s cadence.")
