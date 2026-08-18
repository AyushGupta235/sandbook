"""Sandbox semantics for generated model code.

This file is the single source of truth for how a lesson's model functions are
loaded and called. It runs in two places:

  * the browser, inside a Pyodide web worker (runtime/worker.js fetches it)
  * the verifier, inside a subprocess (verifier/runner.py execs it)

Keeping one copy is what lets the verifier's verdict mean anything: if these
diverged, a lesson could pass verification in CPython and misbehave in Pyodide.
Everything crossing the boundary is JSON.
"""

import io
import json
import math
import traceback
import contextlib

_NS = {}

_SAFE_BUILTINS = {
    "abs": abs, "all": all, "any": any, "bool": bool, "dict": dict,
    "enumerate": enumerate, "float": float, "int": int, "len": len, "list": list,
    "max": max, "min": min, "range": range, "round": round, "sorted": sorted,
    "str": str, "sum": sum, "tuple": tuple, "zip": zip,
    "True": True, "False": False, "None": None,
}


def _sb_default(o):
    if hasattr(o, "tolist"):
        return o.tolist()
    if hasattr(o, "item"):
        return o.item()
    raise TypeError(
        f"{type(o).__name__} is not JSON-serialisable; model functions must "
        "return plain lists/dicts/numbers/strings"
    )


def _sb_dump(obj):
    # allow_nan=False turns a silent NaN/inf into a loud, catchable failure.
    return json.dumps(obj, allow_nan=False, default=_sb_default)


def _sb_load(src):
    _NS.clear()
    _NS["__name__"] = "model"
    exec(compile(src, "model.py", "exec"), _NS)
    return _sb_dump({
        "ok": True,
        "names": sorted(k for k, v in _NS.items() if callable(v) and not k.startswith("_")),
    })


def _sb_call(fn, args_json):
    try:
        f = _NS.get(fn)
        if f is None:
            raise NameError(f"model.py defines no function named {fn!r}")
        return _sb_dump({"ok": True, "result": f(**json.loads(args_json))})
    except Exception as e:
        return json.dumps({
            "ok": False,
            "error": f"{type(e).__name__}: {e}",
            "trace": traceback.format_exc(limit=6),
        })


def _sb_check(fn, args_json, preds_json):
    """Derive an answer instead of trusting an asserted one.

    Calls a model function, then evaluates each option's predicate against the
    result. Callers require that exactly one predicate holds.
    """
    try:
        f = _NS.get(fn)
        if f is None:
            raise NameError(f"model.py defines no function named {fn!r}")
        result = f(**json.loads(args_json))
        env = {"result": result, "math": math}
        flags = [bool(eval(expr, {"__builtins__": _SAFE_BUILTINS}, env))
                 for expr in json.loads(preds_json)]
        return _sb_dump({"ok": True, "result": result, "flags": flags})
    except Exception as e:
        return json.dumps({
            "ok": False,
            "error": f"{type(e).__name__}: {e}",
            "trace": traceback.format_exc(limit=6),
        })


def _sb_exec(learner_src, tests_src):
    """Run learner code, then hidden checks against it. Model functions are in scope."""
    ns = dict(_NS)
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            exec(compile(learner_src, "your_code.py", "exec"), ns)
    except Exception as e:
        return json.dumps({
            "ok": False, "stage": "code", "stdout": buf.getvalue(),
            "error": f"{type(e).__name__}: {e}",
            "trace": traceback.format_exc(limit=4),
        })
    try:
        with contextlib.redirect_stdout(buf):
            exec(compile(tests_src, "checks.py", "exec"), ns)
    except AssertionError as e:
        return json.dumps({
            "ok": False, "stage": "tests", "stdout": buf.getvalue(),
            "error": str(e) or "an assertion failed",
        })
    except Exception as e:
        return json.dumps({
            "ok": False, "stage": "tests", "stdout": buf.getvalue(),
            "error": f"{type(e).__name__}: {e}",
            "trace": traceback.format_exc(limit=4),
        })
    return json.dumps({"ok": True, "stdout": buf.getvalue()})
