"""Executes a lesson's model code in an isolated subprocess.

The verifier never imports generated code into its own process. It sends a job
on stdin and reads results from stdout, so a hang or a crash in generated code
costs a timeout rather than the whole build.

Job (stdin, JSON):
    {"source": "<model.py>", "ops": [ ... ]}

Ops:
    {"op": "call",  "fn": str, "args": {...}}
    {"op": "check", "fn": str, "args": {...}, "predicates": [str, ...]}
    {"op": "exec",  "code": str, "tests": str}

Result (stdout, JSON):
    {"ok": true, "names": [...], "results": [ ... ]}   # one entry per op
    {"ok": false, "error": "..."}                      # model failed to load
"""

import json
import pathlib
import sys

BOOTSTRAP = pathlib.Path(__file__).resolve().parents[1] / "runtime" / "sandbox_bootstrap.py"


# Pyodide package names do not always match their import name.
_IMPORT_NAME = {"pyyaml": "yaml", "pillow": "PIL", "scikit-learn": "sklearn",
                "beautifulsoup4": "bs4", "python-dateutil": "dateutil"}


def main():
    ns = {}
    exec(compile(BOOTSTRAP.read_text(), str(BOOTSTRAP), "exec"), ns)

    job = json.load(sys.stdin)

    # A lesson declares the packages Pyodide will load for it. The verifier runs
    # in CPython, so report any the host is missing rather than failing later
    # with a confusing ImportError from inside the model.
    missing = []
    for pkg in job.get("packages", []):
        try:
            __import__(_IMPORT_NAME.get(pkg.lower(), pkg))
        except ImportError:
            missing.append(pkg)
    if missing:
        json.dump({"ok": False,
                   "error": "packages declared by the lesson are not installed in the "
                            f"verifier's Python: {', '.join(missing)}"}, sys.stdout)
        return
    try:
        loaded = json.loads(ns["_sb_load"](job["source"]))
    except Exception as e:  # noqa: BLE001 - report any import-time failure verbatim
        import traceback
        json.dump({"ok": False,
                   "error": f"{type(e).__name__}: {e}",
                   "trace": traceback.format_exc(limit=8)}, sys.stdout)
        return

    results = []

    def deref(value):
        """Replace {"$ref": i} with the result of op i, so dependent ops
        (like stepping a simulation forward) fit in a single job."""
        if isinstance(value, dict):
            if "$ref" in value:
                prior = results[value["$ref"]]
                if not prior.get("ok"):
                    raise ValueError(f"op {value['$ref']} failed, cannot chain from it")
                return prior["result"]
            return {k: deref(v) for k, v in value.items()}
        if isinstance(value, list):
            return [deref(v) for v in value]
        return value

    for op in job.get("ops", []):
        kind = op.get("op")
        try:
            args = deref(op.get("args", {}))
        except Exception as e:  # noqa: BLE001
            results.append({"ok": False, "error": str(e)})
            continue
        if kind == "call":
            out = json.loads(ns["_sb_call"](op["fn"], json.dumps(args)))
        elif kind == "check":
            out = json.loads(ns["_sb_check"](op["fn"], json.dumps(args),
                                             json.dumps(op.get("predicates", []))))
        elif kind == "exec":
            out = json.loads(ns["_sb_exec"](op.get("code", ""), op.get("tests", "")))
        else:
            out = {"ok": False, "error": f"unknown op {kind!r}"}
        results.append(out)

    json.dump({"ok": True, "names": loaded.get("names", []), "results": results}, sys.stdout)


if __name__ == "__main__":
    main()
