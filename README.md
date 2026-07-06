# token-bucket

A small, dependency-free token-bucket rate limiter for staying under a third-party API's rate limit, instead of finding the limit the hard way.

## The problem

Most APIs cap how fast you can call them. When you go over, they answer with `429 Too Many Requests`, and the naive fix (catch the 429, wait, retry) still lets you slam into the wall on every burst. Retry logic reacts *after* you have already been throttled. A rate limiter is the other half of the story: it paces your calls so you rarely get throttled in the first place. This library is the pacing half. It gives you a single object you ask for permission before each call, with no background threads and no third-party dependencies.

## Install

```bash
pip install token-bucket-limiter
```

Or just copy `token_bucket/__init__.py` into your project. It is one file and imports only the standard library.

## Quickstart

```python
from token_bucket import TokenBucket, rate_limited

# 5 requests per second, allowing a burst of up to 5.
bucket = TokenBucket(rate=5, capacity=5)

# Blocking: wait until a token is free, then proceed.
bucket.acquire()
send_request()

# Non-blocking: only proceed if a token is available right now.
if bucket.try_acquire():
    send_request()
else:
    shed_or_queue()

# Or wrap the call and forget about it.
@rate_limited(bucket)
def send_request():
    ...
```

There is a runnable demo in [`examples/pace_requests.py`](examples/pace_requests.py):

```bash
python examples/pace_requests.py
```

## How a token bucket works

The bucket holds up to `capacity` tokens and refills at `rate` tokens per second. Each call costs one token (or more, if you say so). If the bucket has enough tokens the call goes through and they are deducted; if not, you either wait (`acquire`) or get told no (`try_acquire`). `capacity` is your burst size, the most you will ever let through at once. `rate` is the sustained throughput once that burst is spent. Setting `capacity` equal to `rate` gives you "at most one second of burst," which is a sane default for most APIs.

## Design decisions

**Lazy refill, no background thread.** Tokens are not added by a timer. Each time you touch the bucket it looks at how much time has passed on the clock and credits the tokens that would have accrued, capped at `capacity`. That means nothing to start, stop, or leak, and the object is cheap to create and throw away.

**The clock and sleep are injectable.** `TokenBucket(rate, capacity, clock=..., sleep=...)` lets you pass your own time source and sleeper. In production you get `time.monotonic` and `time.sleep`. In tests you pass a fake clock you advance by hand, so the entire test suite is deterministic and runs in milliseconds without ever touching real wall time. Reliability code you cannot test quickly is reliability code nobody trusts, so testability was a first-class goal rather than an afterthought.

**Monotonic by default.** The default clock is `time.monotonic`, not `time.time`, so a system clock adjustment (NTP, DST) cannot make the limiter suddenly think a large amount of time passed and dump a flood of tokens. The refill step also ignores any backwards jump defensively.

**Thread-safe by construction.** A single lock guards the read-refill-consume sequence, so concurrent callers can share one bucket without two of them both seeing the last token. This is the invariant the concurrency test pins down: with a frozen clock and 200 racing threads against a capacity of 50, exactly 50 acquisitions succeed.

**Requests larger than capacity fail loudly.** Asking for more tokens than the bucket can ever hold raises `ValueError` immediately rather than blocking forever, because that call is unsatisfiable by definition and silent hangs are the worst failure mode.

**Failed `try_acquire` consumes nothing.** A non-blocking check that comes up short leaves the bucket untouched, so you can poll it cheaply or use it as a load-shedding gate.

## Configuration

| Parameter    | Type              | Default          | Meaning                                                        |
|--------------|-------------------|------------------|----------------------------------------------------------------|
| `rate`       | float             | required         | Tokens replenished per second (your sustained call rate).      |
| `capacity`   | float             | equal to `rate`  | Maximum tokens held at once (your burst size).                 |
| `clock`      | `() -> float`     | `time.monotonic` | Monotonic time source in seconds. Override for tests.          |
| `sleep`      | `(float) -> None` | `time.sleep`     | Sleeper used by the blocking `acquire`. Override for tests.    |
| `start_full` | bool              | `True`           | Start at `capacity` (allow an initial burst) or empty.         |

### Methods

| Call                                | Blocks? | Returns | Notes                                                        |
|-------------------------------------|---------|---------|-------------------------------------------------------------|
| `try_acquire(tokens=1)`             | no      | `bool`  | Consume if available, else leave the bucket unchanged.      |
| `acquire(tokens=1, timeout=None)`   | yes     | `bool`  | Wait for tokens; `False` if `timeout` elapses first.        |
| `time_until_available(tokens=1)`    | no      | `float` | Seconds until the request could be satisfied (`0.0` if now).|
| `tokens` (property)                 | no      | `float` | Tokens available at this instant.                           |

## Testing

The suite is pure and offline. Time is driven by a hand-advanced fake clock and a recording sleep function, so tests assert exact wait durations and exact token counts with no flakiness.

```bash
pip install pytest
pytest
```

## License

MIT. See [LICENSE](LICENSE).
