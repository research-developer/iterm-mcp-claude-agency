#!/usr/bin/env python3
"""Per-module test runner with timeouts.

The full `unittest discover` suite hangs on live-iTerm2 integration tests
when no interactive iTerm2 API session is available. This driver runs each
test module in its own subprocess with a timeout, so hanging modules are
reported (and skippable) instead of stalling the whole run.

Usage:
    python scripts/test_baseline.py            # run all tests/test_*.py
    python scripts/test_baseline.py --timeout 60
"""

import argparse
import pathlib
import subprocess
import sys


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--timeout", type=int, default=60)
    args = parser.parse_args()

    root = pathlib.Path(__file__).resolve().parent.parent
    modules = sorted(p.stem for p in (root / "tests").glob("test_*.py"))

    results = {}
    for mod in modules:
        try:
            proc = subprocess.run(
                [sys.executable, "-m", "unittest", f"tests.{mod}", "-v"],
                cwd=root, capture_output=True, text=True, timeout=args.timeout,
            )
            tail = proc.stderr.strip().splitlines()
            verdict = tail[-1] if tail else "?"
            results[mod] = ("PASS" if proc.returncode == 0 else "FAIL", verdict)
        except subprocess.TimeoutExpired:
            results[mod] = ("HANG", f"timed out after {args.timeout}s")

    width = max(len(m) for m in results)
    for mod, (status, detail) in results.items():
        print(f"{status:<5} {mod:<{width}}  {detail}")

    counts = {}
    for status, _ in results.values():
        counts[status] = counts.get(status, 0) + 1
    print(f"\nTOTAL: {len(results)} modules — " +
          ", ".join(f"{k}={v}" for k, v in sorted(counts.items())))
    return 0 if counts.get("FAIL", 0) == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
