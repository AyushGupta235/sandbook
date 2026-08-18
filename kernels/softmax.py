"""Temperature-scaled softmax."""

NAME = "softmax"
SUMMARY = "temperature-scaled softmax over a list of logits, returns probabilities"
TOLERANCE = 1e-12

PROBES = [
    # Ordinary case, temperature 1.
    {"logits": [3.0, 2.6, 2.0, 0.5, -0.5], "temperature": 1.0},
    # Low temperature sharpens. A wrong implementation that divides after
    # exponentiating instead of before shows up here first.
    {"logits": [3.0, 2.6, 2.0, 0.5, -0.5], "temperature": 0.1},
    # High temperature flattens toward uniform.
    {"logits": [3.0, 2.6, 2.0, 0.5, -0.5], "temperature": 8.0},
    # Already uniform: every temperature must leave it uniform.
    {"logits": [0.0, 0.0, 0.0, 0.0], "temperature": 2.0},
    # Single element: the answer is 1.0 whatever the logit.
    {"logits": [-4.2], "temperature": 1.0},
    # Large logits. Exponentiating these directly overflows, so this probe
    # separates a numerically stable implementation from one that only looks
    # right on small numbers.
    {"logits": [1000.0, 1001.0], "temperature": 1.0},
    # Large and negative, the same trap from the other side.
    {"logits": [-1000.0, -1001.0], "temperature": 1.0},
    # Wide spread at low temperature, where an unstable version underflows to
    # all zeros and then divides by zero.
    {"logits": [50.0, 0.0, -50.0], "temperature": 0.5},
]


def reference(logits, temperature):
    """Softmax of `logits` scaled by `temperature`.

    Subtracting the maximum before exponentiating cancels out of the ratio, so
    the result is unchanged, but it keeps every exponent at or below zero and
    therefore finite.
    """
    import math

    if temperature <= 0:
        raise ValueError("temperature must be positive")
    scaled = [x / temperature for x in logits]
    top = max(scaled)
    exps = [math.exp(x - top) for x in scaled]
    total = sum(exps)
    return [e / total for e in exps]
