"""Model functions for the "Softmax & Temperature" lesson.

Contract for every function in this file:
  * pure: no I/O, no globals mutated, no randomness without an explicit seed
  * JSON in, JSON out: arguments and return values are plain Python data
  * deterministic: the verifier calls these in CPython and the browser calls
    them in Pyodide, and both must agree

Functions whose name ends in `_view` return a view object that the runtime
knows how to draw. They never touch the DOM.
"""

import math

TOKENS = ["cat", "dog", "bird", "fish", "rock"]

PRESETS = {
    "mixed": [3.0, 2.6, 2.0, 0.5, -0.5],
    "confident": [4.0, 1.0, 0.5, 0.0, -1.0],
    "close": [2.0, 1.9, 1.7, 1.5, 1.2],
    "flat": [0.0, 0.0, 0.0, 0.0, 0.0],
}


def preset_logits(preset):
    """Look up one of the named logit vectors."""
    if preset not in PRESETS:
        raise KeyError(f"unknown preset {preset!r}; expected one of {sorted(PRESETS)}")
    return list(PRESETS[preset])


# --------------------------------------------------------------------- core


def softmax_probs(logits, temperature):
    """Temperature-scaled softmax.

    Subtracting the max before exponentiating cancels out of the ratio but
    keeps every exponent <= 0, so large logits cannot overflow.
    """
    t = max(float(temperature), 1e-6)
    scaled = [float(x) / t for x in logits]
    shift = max(scaled)
    exps = [math.exp(x - shift) for x in scaled]
    total = sum(exps)
    return [e / total for e in exps]


def entropy_nats(logits, temperature):
    """Shannon entropy of the softmax distribution, in nats."""
    probs = softmax_probs(logits, temperature)
    return -sum(p * math.log(p) for p in probs if p > 0.0)


def normalised_entropy(logits, temperature):
    """Entropy as a fraction of the maximum possible for this vocabulary size."""
    n = len(logits)
    if n <= 1:
        return 0.0
    return entropy_nats(logits, temperature) / math.log(n)


def max_prob(logits, temperature):
    """Probability mass sitting on the single most likely token."""
    return max(softmax_probs(logits, temperature))


def perplexity(logits, temperature):
    """exp(entropy), the effective number of choices the model is weighing."""
    return math.exp(entropy_nats(logits, temperature))


# -------------------------------------------------------------------- views


def dist_view(preset, temperature):
    """Bar chart of the distribution for one preset at one temperature."""
    logits = preset_logits(preset)
    probs = softmax_probs(logits, temperature)
    top = probs.index(max(probs))
    eff = perplexity(logits, temperature)
    return {
        "kind": "bars",
        "labels": list(TOKENS),
        "values": probs,
        "y_max": 1.0,
        "highlight": [top],
        "x_label": "candidate next token",
        "y_label": "probability",
        "value_format": ".3f",
        "caption": (
            f"Logits {logits} divided by T = {float(temperature):.2f}. "
            f"The model is effectively choosing between {eff:.2f} tokens."
        ),
    }


def readout_effective_choices(preset, temperature):
    return perplexity(preset_logits(preset), temperature)


def readout_top_prob(preset, temperature):
    return max_prob(preset_logits(preset), temperature)


def readout_entropy(preset, temperature):
    return entropy_nats(preset_logits(preset), temperature)


def sweep_view(preset, t_min=0.05, t_max=3.0, steps=60):
    """How top-token probability and entropy move across the temperature range."""
    logits = preset_logits(preset)
    xs, top, ent = [], [], []
    for i in range(int(steps)):
        t = float(t_min) + (float(t_max) - float(t_min)) * i / (int(steps) - 1)
        xs.append(round(t, 4))
        top.append(max_prob(logits, t))
        ent.append(normalised_entropy(logits, t))
    return {
        "kind": "lines",
        "x": xs,
        "y_min": 0.0,
        "y_max": 1.0,
        "x_label": "temperature",
        "y_label": "0 – 1",
        "series": [
            {"label": "probability of top token", "values": top},
            {"label": "entropy ÷ ln(vocab)", "values": ent},
        ],
        "caption": (
            "Left edge is greedy decoding, right edge approaches a uniform draw. "
            "The two curves are mirror images: sharpening one is flattening the other."
        ),
    }


# ---------------------------------------------------- nucleus (top-p) sampling


def nucleus_init(preset, temperature, top_p):
    """Starting state for a step-by-step walk through nucleus sampling."""
    logits = preset_logits(preset)
    probs = softmax_probs(logits, temperature)
    order = sorted(range(len(probs)), key=lambda i: probs[i], reverse=True)
    return {
        "probs": probs,
        "order": order,
        "cursor": 0,
        "cum": 0.0,
        "kept": [],
        "top_p": float(top_p),
        "temperature": float(temperature),
        "done": False,
    }


def nucleus_step(state):
    """Admit the next most-likely token into the nucleus."""
    s = dict(state)
    if s["done"]:
        return s
    idx = s["order"][s["cursor"]]
    s["kept"] = list(s["kept"]) + [idx]
    s["cum"] = s["cum"] + s["probs"][idx]
    s["cursor"] = s["cursor"] + 1
    if s["cum"] >= s["top_p"] or s["cursor"] >= len(s["order"]):
        s["done"] = True
    return s


def nucleus_view(state):
    """Original distribution with the nucleus highlighted; renormalised once done."""
    probs = state["probs"]
    kept = list(state["kept"])
    cum = state["cum"]
    top_p = state["top_p"]

    if not kept:
        caption = (
            f"Nothing admitted yet. Tokens will be considered in probability order "
            f"until the running total reaches top_p = {top_p:.2f}."
        )
    elif not state["done"]:
        caption = (
            f"{len(kept)} token(s) in the nucleus, cumulative mass {cum:.3f}, "
            f"still under top_p = {top_p:.2f}."
        )
    else:
        dropped = len(probs) - len(kept)
        caption = (
            f"Cumulative mass {cum:.3f} reached top_p = {top_p:.2f}. "
            f"{dropped} token(s) are cut regardless of how plausible they looked."
        )

    panels = [{
        "kind": "bars",
        "labels": list(TOKENS),
        "values": probs,
        "y_max": 1.0,
        "highlight": kept,
        "x_label": "candidate next token",
        "y_label": "probability",
        "value_format": ".3f",
        "caption": caption,
    }]

    if state["done"]:
        keep_set = set(kept)
        renorm = [(p / cum if i in keep_set else 0.0) for i, p in enumerate(probs)]
        panels.append({
            "kind": "bars",
            "labels": list(TOKENS),
            "values": renorm,
            "y_max": 1.0,
            "highlight": kept,
            "x_label": "candidate next token",
            "y_label": "probability after renormalising",
            "value_format": ".3f",
            "caption": (
                "The surviving probabilities are divided by their own total so they "
                "sum to 1 again. This is the distribution actually sampled from."
            ),
        })

    return {"kind": "stack", "panels": panels}
