"""Deterministic tests for TokenBucket.

Time is driven by a manual FakeClock and a recording sleep function, so the
whole suite runs offline in milliseconds and never depends on real wall time.
"""

import threading

import pytest

from token_bucket import TokenBucket, rate_limited


class FakeClock:
    """A clock you advance by hand. Also a sleep() that advances itself."""

    def __init__(self, start: float = 0.0) -> None:
        self.now = start
        self.slept: list[float] = []

    def time(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


def make_bucket(rate, capacity=None, *, start_full=True):
    clock = FakeClock()
    bucket = TokenBucket(
        rate,
        capacity,
        clock=clock.time,
        sleep=clock.sleep,
        start_full=start_full,
    )
    return bucket, clock


# -- construction ---------------------------------------------------------


def test_capacity_defaults_to_rate():
    bucket, _ = make_bucket(5)
    assert bucket.capacity == 5


@pytest.mark.parametrize("rate", [0, -1])
def test_bad_rate_rejected(rate):
    with pytest.raises(ValueError):
        TokenBucket(rate)


def test_bad_capacity_rejected():
    with pytest.raises(ValueError):
        TokenBucket(1, 0)


def test_starts_full_by_default():
    bucket, _ = make_bucket(10, 4)
    assert bucket.tokens == 4


def test_start_empty():
    bucket, _ = make_bucket(10, 4, start_full=False)
    assert bucket.tokens == 0


# -- non-blocking try_acquire --------------------------------------------


def test_initial_burst_allowed():
    bucket, _ = make_bucket(1, 3)
    assert bucket.try_acquire() is True
    assert bucket.try_acquire() is True
    assert bucket.try_acquire() is True
    assert bucket.try_acquire() is False  # burst spent


def test_try_acquire_does_not_consume_on_failure():
    bucket, _ = make_bucket(1, 1, start_full=False)
    assert bucket.try_acquire() is False
    assert bucket.tokens == 0


def test_refill_over_time():
    bucket, clock = make_bucket(2, 2)  # 2 tokens/sec
    assert bucket.try_acquire(2) is True
    assert bucket.try_acquire() is False
    clock.advance(0.5)  # half a second -> 1 token
    assert bucket.try_acquire() is True
    assert bucket.try_acquire() is False


def test_capacity_caps_accumulation():
    bucket, clock = make_bucket(5, 5)
    bucket.try_acquire(5)
    clock.advance(100)  # would be 500 tokens without a cap
    assert bucket.tokens == 5


def test_fractional_rate():
    bucket, clock = make_bucket(0.5, 1, start_full=False)  # 1 token every 2s
    assert bucket.try_acquire() is False
    clock.advance(2)
    assert bucket.try_acquire() is True


def test_request_larger_than_capacity_raises():
    bucket, _ = make_bucket(1, 2)
    with pytest.raises(ValueError):
        bucket.try_acquire(3)


@pytest.mark.parametrize("amount", [0, -1])
def test_non_positive_request_raises(amount):
    bucket, _ = make_bucket(1, 2)
    with pytest.raises(ValueError):
        bucket.try_acquire(amount)


# -- time_until_available -------------------------------------------------


def test_time_until_available_zero_when_ready():
    bucket, _ = make_bucket(1, 2)
    assert bucket.time_until_available() == 0.0


def test_time_until_available_computes_deficit():
    bucket, _ = make_bucket(2, 4, start_full=False)  # 2 tokens/sec
    # need 3 tokens, have 0 -> 1.5 seconds
    assert bucket.time_until_available(3) == pytest.approx(1.5)


# -- blocking acquire -----------------------------------------------------


def test_acquire_sleeps_exactly_until_ready():
    bucket, clock = make_bucket(1, 1)  # 1 token/sec
    assert bucket.acquire() is True          # consumes the initial token
    assert bucket.acquire() is True          # must wait 1s for the next
    assert clock.slept == [pytest.approx(1.0)]


def test_acquire_no_wait_when_tokens_available():
    bucket, clock = make_bucket(1, 2)
    assert bucket.acquire() is True
    assert clock.slept == []


def test_acquire_times_out():
    bucket, clock = make_bucket(1, 1, start_full=False)
    # empty bucket, refills in 1s, but we only allow 0.4s
    assert bucket.acquire(timeout=0.4) is False


def test_acquire_timeout_does_not_consume():
    bucket, _ = make_bucket(1, 1, start_full=False)
    assert bucket.acquire(timeout=0.1) is False
    # Nothing was consumed; the only change is the 0.1 token that accrued
    # during the 0.1s wait. A consuming path would leave fewer than that.
    assert bucket.tokens == pytest.approx(0.1)


def test_acquire_succeeds_within_timeout():
    bucket, clock = make_bucket(1, 1, start_full=False)
    assert bucket.acquire(timeout=5) is True
    assert clock.now == pytest.approx(1.0)


# -- decorator ------------------------------------------------------------


def test_rate_limited_decorator_paces_calls():
    bucket, clock = make_bucket(1, 1)  # one call, then 1s each
    calls = []

    @rate_limited(bucket)
    def work(n):
        calls.append(n)
        return n * 2

    assert work(1) == 2      # uses the initial token, no sleep
    assert work(2) == 4      # waits 1s
    assert calls == [1, 2]
    assert clock.slept == [pytest.approx(1.0)]


def test_decorator_preserves_metadata():
    bucket, _ = make_bucket(10, 10)

    @rate_limited(bucket)
    def documented():
        """I have a docstring."""

    assert documented.__name__ == "documented"
    assert documented.__doc__ == "I have a docstring."


# -- thread safety --------------------------------------------------------


def test_concurrent_try_acquire_hands_out_exactly_capacity():
    # A frozen clock means zero refill during the race, so no matter how the
    # threads interleave, exactly `capacity` acquisitions may succeed. This is
    # the invariant that would break if the refill/consume step were not
    # guarded by the lock.
    frozen = 123.456
    bucket = TokenBucket(rate=1000, capacity=50, clock=lambda: frozen)
    successes = []
    lock = threading.Lock()

    def worker():
        if bucket.try_acquire():
            with lock:
                successes.append(1)

    threads = [threading.Thread(target=worker) for _ in range(200)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(successes) == 50
    assert bucket.tokens == 0
