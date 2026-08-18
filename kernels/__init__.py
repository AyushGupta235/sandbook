"""Trusted reference implementations.

The verifier can prove a lesson is well-formed, executable and self-consistent,
but not that it is *true*. A lesson whose maths is confidently wrong, with code
that agrees with the wrong maths, passes every other check in this repo. That
is the failure mode worth caring about, because a learner cannot tell the
difference from the inside.

A kernel closes that gap for the parts of a topic that have one right answer.
It is a hand-written implementation of a known primitive, checked into this
repo, reviewed by a person, and never generated. When a lesson claims one of
its functions computes a primitive, the verifier runs both over the kernel's
probe inputs and requires them to agree.

This does not make a lesson true. It makes the specific claims it stakes
against a kernel true, which is the part a machine can settle.

A kernel module defines:

    NAME        str, matching the file name
    SUMMARY     one line, shown when a lesson names a kernel that does not exist
    PROBES      list of kwarg dicts, passed to both implementations
    TOLERANCE   float, for the float comparison (default 1e-9)
    reference() the trusted implementation, taking the probe kwargs

Probes are the kernel's own test vectors, so they must cover the corners where
a plausible-looking wrong implementation diverges from a right one: the
saturating case, the empty case, the case where the naive formula overflows.
A kernel with lazy probes agrees with almost anything and proves nothing.
"""

from __future__ import annotations

import importlib
import math
import pathlib

DIRECTORY = pathlib.Path(__file__).resolve().parent
DEFAULT_TOLERANCE = 1e-9


def available() -> list[str]:
    return sorted(p.stem for p in DIRECTORY.glob("*.py")
                  if not p.stem.startswith(("_", "test_")))


def load(name: str):
    """Import a kernel by name. Raises KeyError if there is no such kernel."""
    if name not in available():
        raise KeyError(name)
    return importlib.import_module(f"kernels.{name}")


def describe() -> str:
    lines = []
    for name in available():
        try:
            lines.append(f"  {name}: {load(name).SUMMARY}")
        except Exception as e:  # noqa: BLE001 - a broken kernel should say so, not vanish
            lines.append(f"  {name}: (failed to load: {e})")
    return "\n".join(lines)


def disagreement(got, want, tol: float, path: str = "") -> str | None:
    """Compare two JSON values, tolerant on floats, exact on everything else.

    Returns a description of the first difference, or None when they agree.
    Reports where the difference is, because "these two dicts differ" is not
    something anyone can act on.
    """
    where = path or "the return value"

    if isinstance(want, bool) or isinstance(got, bool):
        # bool before number: True == 1 is true in Python and is not the kind of
        # agreement we want to accept.
        if got is not want:
            return f"{where}: got {got!r}, the kernel says {want!r}"
        return None

    if isinstance(want, (int, float)) and isinstance(got, (int, float)):
        if not math.isfinite(got):
            return f"{where}: got {got!r}, which is not a finite number"
        if abs(got - want) > tol + tol * abs(want):
            return f"{where}: got {got!r}, the kernel says {want!r}"
        return None

    if isinstance(want, dict):
        if not isinstance(got, dict):
            return f"{where}: got {type(got).__name__}, the kernel returns an object"
        missing = sorted(set(want) - set(got))
        if missing:
            return f"{where}: missing key(s) {missing}, which the kernel returns"
        for key in want:
            found = disagreement(got[key], want[key], tol, f"{where}.{key}")
            if found:
                return found
        return None

    if isinstance(want, list):
        if not isinstance(got, list):
            return f"{where}: got {type(got).__name__}, the kernel returns a list"
        if len(got) != len(want):
            return f"{where}: got {len(got)} item(s), the kernel returns {len(want)}"
        for i, (g, w) in enumerate(zip(got, want)):
            found = disagreement(g, w, tol, f"{where}[{i}]")
            if found:
                return found
        return None

    if got != want:
        return f"{where}: got {got!r}, the kernel says {want!r}"
    return None
