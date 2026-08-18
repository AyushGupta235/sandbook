"""sandbook command line entry point.

    sandbook serve            serve the runtime on localhost
    sandbook verify [slug]    run the contract verifier (all lessons by default)
    sandbook selftest         run the verifier's own mutation suite
    sandbook list             list built lessons

`sandbook build "<topic>"` arrives in M1, once the generation pipeline exists.
Until then a lesson is authored by hand and held to the same verifier.
"""

from __future__ import annotations

import argparse
import functools
import http.server
import json
import pathlib
import socketserver
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
LESSONS = ROOT / "lessons"
DEFAULT_PORT = 8765


def cmd_serve(args: argparse.Namespace) -> int:
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(ROOT))

    class Server(socketserver.TCPServer):
        allow_reuse_address = True

    url = f"http://localhost:{args.port}/runtime/index.html"
    with Server(("127.0.0.1", args.port), handler) as httpd:
        print(f"sandbook serving {ROOT}")
        print(f"  library : {url}")
        for slug in sorted(p.name for p in LESSONS.iterdir() if (p / "lesson.json").exists()):
            print(f"  lesson  : {url}?lesson={slug}")
        print("\nctrl-c to stop")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nstopped")
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    cmd = [sys.executable, str(ROOT / "verifier" / "verify.py")] + args.slug
    return subprocess.call(cmd)


def cmd_selftest(_args: argparse.Namespace) -> int:
    return subprocess.call([sys.executable, str(ROOT / "verifier" / "test_mutations.py")])


def cmd_list(_args: argparse.Namespace) -> int:
    found = []
    for p in sorted(LESSONS.iterdir()):
        lp = p / "lesson.json"
        if lp.exists():
            data = json.loads(lp.read_text())
            found.append((p.name, data.get("title", ""), len(data.get("modules", []))))
    if not found:
        print("no lessons yet")
        return 0
    width = max(len(s) for s, _, _ in found)
    for slug, title, n in found:
        print(f"  {slug.ljust(width)}  {n} modules  {title}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="sandbook", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

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
