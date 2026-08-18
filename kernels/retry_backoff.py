"""Retry schedule under an exponential backoff policy.

Modelled on Temporal's RetryPolicy, which is also the shape Kubernetes,
AWS SDKs and most job runners use: an initial interval, a multiplier, a cap on
any single interval, and a limit on the number of attempts.

Jitter is deliberately absent. A schedule with randomness in it cannot be
compared against a reference, and every property worth teaching here (that the
cap flattens growth, that maximum_attempts counts attempts rather than
retries) is visible without it.
"""

NAME = "retry_backoff"
SUMMARY = ("delays for an exponential-backoff retry policy: initial interval, "
           "multiplier, per-interval cap, attempt limit")
TOLERANCE = 1e-9

PROBES = [
    # Textbook doubling, no cap reached.
    {"initial_interval_s": 1.0, "backoff_coefficient": 2.0,
     "maximum_interval_s": 100.0, "maximum_attempts": 5},
    # The cap binds partway through, which is where a schedule that keeps
    # doubling past the cap diverges.
    {"initial_interval_s": 1.0, "backoff_coefficient": 2.0,
     "maximum_interval_s": 10.0, "maximum_attempts": 8},
    # Cap binds immediately: every interval is the cap.
    {"initial_interval_s": 30.0, "backoff_coefficient": 2.0,
     "maximum_interval_s": 5.0, "maximum_attempts": 4},
    # A single attempt means no retries at all, so no delays.
    {"initial_interval_s": 1.0, "backoff_coefficient": 2.0,
     "maximum_interval_s": 100.0, "maximum_attempts": 1},
    # Unlimited attempts (0 in Temporal) with a horizon supplied by the caller.
    {"initial_interval_s": 0.5, "backoff_coefficient": 3.0,
     "maximum_interval_s": 60.0, "maximum_attempts": 0, "horizon": 7},
    # Coefficient 1 is constant backoff, not exponential.
    {"initial_interval_s": 2.0, "backoff_coefficient": 1.0,
     "maximum_interval_s": 60.0, "maximum_attempts": 4},
    # Fractional seconds, the common real setting.
    {"initial_interval_s": 0.1, "backoff_coefficient": 2.0,
     "maximum_interval_s": 1.0, "maximum_attempts": 6},
]


def reference(initial_interval_s, backoff_coefficient, maximum_interval_s,
              maximum_attempts, horizon=10):
    """Delays before each retry, and the total time spent waiting.

    `maximum_attempts` counts attempts, not retries, so a policy with N
    attempts waits N-1 times. Zero means unlimited, and `horizon` then bounds
    how many delays to report.

    Returns:
        delays_s        one delay per retry, in order
        total_wait_s    their sum
        capped_from     index of the first delay that hit the cap, or -1
    """
    if initial_interval_s <= 0:
        raise ValueError("initial_interval_s must be positive")
    if backoff_coefficient < 1:
        raise ValueError("backoff_coefficient below 1 would shrink the interval")

    retries = horizon if maximum_attempts == 0 else max(0, maximum_attempts - 1)

    delays = []
    capped_from = -1
    for i in range(retries):
        uncapped = initial_interval_s * (backoff_coefficient ** i)
        delay = min(uncapped, maximum_interval_s)
        if delay < uncapped and capped_from == -1:
            capped_from = i
        delays.append(delay)

    return {
        "delays_s": delays,
        "total_wait_s": sum(delays),
        "capped_from": capped_from,
    }
