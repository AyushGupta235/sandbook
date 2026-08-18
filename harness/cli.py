"""sandbook command line entry point.

    sandbook build "<topic>"    generate a lesson into output/
    sandbook promote <slug>     move a reviewed lesson from output/ into lessons/
    sandbook serve              serve the runtime on localhost
    sandbook verify [slug]      run the contract verifier (all lessons by default)
    sandbook selftest           run the verifier's own mutation suite
    sandbook list               list built lessons

Generated lessons land in a gitignored output/ directory. Promote one into
lessons/ once you have read it or worked through it, so unreviewed machine
output never reaches the repository.
"""

from __future__ import annotations

import argparse
import functools
import http.server
import json
import pathlib
import shutil
import socketserver
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
LESSONS = ROOT / "lessons"
OUTPUT = ROOT / "output"
DEFAULT_PORT = 8765


# ------------------------------------------------------------------- helpers


def _lesson_dirs(base: pathlib.Path) -> list[pathlib.Path]:
    if not base.exists():
        return []
    return sorted(p for p in base.iterdir() if (p / "lesson.json").exists())


def _title_of(path: pathlib.Path) -> str:
    try:
        return json.loads((path / "lesson.json").read_text()).get("title", "")
    except (OSError, json.JSONDecodeError):
        return ""


# ------------------------------------------------------------------ commands


def cmd_build(args: argparse.Namespace) -> int:
    sys.path.insert(0, str(ROOT / "harness"))
    from llm import AgentSDKModel, ModelAuthError, ModelError, ScriptedModel, save_recording
    import pipeline

    icons = {"stage": "·", "curriculum": "▸", "ok": "✓", "repair": "↻",
             "drop": "✗", "fail": "✗", "detail": "   ", "wait": "⋯"}

    def on_retry(stage: str, attempt: int, delay: float, reason: str) -> None:
        print(f"  {icons['wait']} {stage}: {reason[:80]}, retrying in {delay:.0f}s "
              f"(attempt {attempt})", flush=True)

    recording: list = []
    if args.replay:
        model = ScriptedModel.from_file(args.replay)
        print(f"replaying {args.replay} (no model calls)")
    else:
        model = AgentSDKModel(record=recording, on_retry=on_retry)

    def on_event(kind: str, message: str) -> None:
        print(f"  {icons.get(kind, '·')} {message}", flush=True)

    grounding = ""
    if args.grounding:
        grounding = pathlib.Path(args.grounding).read_text()

    print(f"building a lesson on: {args.topic}")
    try:
        report = pipeline.build(model, args.topic, output_root=OUTPUT,
                                grounding=grounding, on_event=on_event)
    except ModelAuthError as e:
        print(f"\n{e}", file=sys.stderr)
        return 2
    except ModelError as e:
        print(f"\nthe pipeline could not continue: {e}", file=sys.stderr)
        return 1
    finally:
        if recording and args.record:
            save_recording(args.record, recording)
            print(f"  · recorded {len(recording)} model call(s) to {args.record}")

    print()
    if not report.ok:
        print("FAILED: no lesson written." if report.path is None
              else f"FAILED: wrote {report.path} but it does not verify.")
        for mid, findings in report.dropped[:8]:
            print(f"  dropped {mid}: {findings[0][2][:140]}")
        return 1

    print(f"wrote {report.path.relative_to(ROOT)}")
    print(f"  {len(report.shipped)} module(s) shipped: {', '.join(report.shipped)}")
    if report.repairs:
        print(f"  {report.repairs} repair round(s) along the way")
    if report.dropped:
        print(f"  {len(report.dropped)} module(s) dropped rather than shipped broken:")
        for mid, findings in report.dropped:
            print(f"    {mid}: {findings[0][2][:120]}")
    if report.cost_usd:
        print(f"  cost: ${report.cost_usd:.2f}")
    if getattr(model, "retries", 0):
        print(f"  {model.retries} transient API failure(s) retried rather than dropped")
    _write_index(OUTPUT)
    print(f"\nread it:    ./sandbook serve   then open"
          f" ?lesson={report.slug}&from=output")
    print(f"promote it: ./sandbook promote {report.slug}")
    return 0


def cmd_promote(args: argparse.Namespace) -> int:
    source = OUTPUT / args.slug
    if not (source / "lesson.json").exists():
        print(f"no generated lesson at {source}", file=sys.stderr)
        return 1
    destination = LESSONS / args.slug
    if destination.exists() and not args.force:
        print(f"{destination} already exists; pass --force to replace it", file=sys.stderr)
        return 1

    # Copy first, then verify in place. A lesson that fails here is removed
    # again rather than left half-promoted.
    backup = None
    if destination.exists():
        backup = pathlib.Path(shutil.make_archive(
            str(destination) + ".backup", "zip", root_dir=destination))
        shutil.rmtree(destination)
    shutil.copytree(source, destination)

    if subprocess.call([sys.executable, str(ROOT / "verifier" / "verify.py"), args.slug]) != 0:
        shutil.rmtree(destination)
        if backup:
            shutil.unpack_archive(str(backup), str(destination))
            backup.unlink()
        print("\npromotion reverted: the lesson does not pass the verifier", file=sys.stderr)
        return 1
    if backup:
        backup.unlink()

    _write_index(LESSONS)
    _write_index(OUTPUT)
    print(f"promoted to {destination.relative_to(ROOT)} and refreshed the lesson index")
    return 0


def _write_index(base: pathlib.Path) -> None:
    """Rewrite the index the runtime's library page reads."""
    lessons = []
    for path in _lesson_dirs(base):
        data = json.loads((path / "lesson.json").read_text())
        lessons.append({"slug": path.name,
                        "title": data.get("title", path.name),
                        "subtitle": data.get("subtitle", "")})
    base.mkdir(parents=True, exist_ok=True)
    (base / "index.json").write_text(json.dumps({"lessons": lessons}, indent=2) + "\n")


def cmd_serve(args: argparse.Namespace) -> int:
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(ROOT))

    class Server(socketserver.TCPServer):
        allow_reuse_address = True

    url = f"http://localhost:{args.port}/runtime/index.html"
    with Server(("127.0.0.1", args.port), handler) as httpd:
        print(f"sandbook serving {ROOT}")
        print(f"  library : {url}")
        for path in _lesson_dirs(LESSONS):
            print(f"  lesson  : {url}?lesson={path.name}")
        print("\nctrl-c to stop")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nstopped")
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    return subprocess.call(
        [sys.executable, str(ROOT / "verifier" / "verify.py")] + args.slug)


def cmd_selftest(_args: argparse.Namespace) -> int:
    """Prove all three halves still work: the kernels behave as claimed, the
    verifier catches planted defects, and the pipeline repairs what it can and
    drops what it cannot. Kernels run first; if they are wrong, everything
    measured against them is wrong too."""
    rc = subprocess.call([sys.executable, str(ROOT / "kernels" / "test_kernels.py")])
    rc |= subprocess.call([sys.executable, str(ROOT / "verifier" / "test_mutations.py")])
    rc |= subprocess.call([sys.executable, str(ROOT / "verifier" / "test_pipeline.py")])
    return rc


def cmd_list(_args: argparse.Namespace) -> int:
    rows = [("lessons/", p) for p in _lesson_dirs(LESSONS)]
    rows += [("output/", p) for p in _lesson_dirs(OUTPUT)]
    if not rows:
        print("no lessons yet")
        return 0
    width = max(len(p.name) for _, p in rows)
    for where, path in rows:
        data = json.loads((path / "lesson.json").read_text())
        n = len(data.get("modules", []))
        print(f"  {where:<9} {path.name.ljust(width)}  {n} modules  {_title_of(path)}")
    return 0


# ---------------------------------------------------------------------- main


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="sandbook", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    p_build = sub.add_parser("build", help="generate a lesson into output/")
    p_build.add_argument("topic", help="what the lesson should teach")
    p_build.add_argument("--grounding", help="file of source notes to ground the lesson in")
    p_build.add_argument("--record", help="save the model calls to this file for replay")
    p_build.add_argument("--replay", help="replay a recording instead of calling the model")
    p_build.set_defaults(func=cmd_build)

    p_promote = sub.add_parser("promote", help="move a reviewed lesson into lessons/")
    p_promote.add_argument("slug")
    p_promote.add_argument("--force", action="store_true", help="replace an existing lesson")
    p_promote.set_defaults(func=cmd_promote)

    p_serve = sub.add_parser("serve", help="serve the runtime on localhost")
    p_serve.add_argument("--port", type=int, default=DEFAULT_PORT)
    p_serve.set_defaults(func=cmd_serve)

    p_verify = sub.add_parser("verify", help="run the contract verifier")
    p_verify.add_argument("slug", nargs="*", help="lesson slugs (default: all)")
    p_verify.set_defaults(func=cmd_verify)

    sub.add_parser("selftest", help="prove the verifier catches planted defects").set_defaults(func=cmd_selftest)
    sub.add_parser("list", help="list built lessons").set_defaults(func=cmd_list)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
