#!/usr/bin/env python3
"""Fidelity check: compare this tool's transcribed constants and property names
with a local copy of the upstream spec.

    python3 tools/check_fidelity.py /path/to/update-lifecycle.qnt

Exit 0 = every property name and every constant matches the spec file.
Exit 1 = at least one mismatch (printed). Exit 2 = usage / unreadable file.
This exists because "the URL exists" is not the same as "the rule is what we
claim it is": run it whenever the upstream spec moves.
"""
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import plannotator_trace_guard as tg  # noqa: E402

CONSTANTS = [
    "CHECK_INTERVAL", "CHECK_RETRY", "START_FRESH", "NOTES_INTERVAL", "NOTES_FAIL_RETRY",
    "NOTES_PENDING_RETRY", "NOTES_YOUNG", "AUTO_WINDOW", "LOCK_STALE", "LOCK_LIVE_MAX",
    "WHATS_NEW_BOUND", "OFFER_BOUND", "NOTES_LAG",
]


def main(argv):
    if len(argv) != 2:
        print("usage: check_fidelity.py <path to update-lifecycle.qnt>", file=sys.stderr)
        return 2
    path = argv[1]
    try:
        text = open(path, "r", encoding="utf-8").read()
    except OSError as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 2

    problems = []
    for name in CONSTANTS:
        m = re.search(r"^\s*(?:pure\s+)?val\s+%s\s*(?::[^=]+)?=\s*(-?\d+)" % re.escape(name), text, re.M)
        if not m:
            problems.append("constant %s: not found in the spec" % name)
            continue
        spec_value = int(m.group(1))
        ours = getattr(tg, name)
        if spec_value != ours:
            problems.append("constant %s: spec=%d tool=%d" % (name, spec_value, ours))
    for name in tg.PROP_NAMES:
        if not re.search(r"^\s*val\s+%s\s*=" % re.escape(name), text, re.M):
            problems.append("property %s: no `val %s =` in the spec" % (name, name))

    for p in problems:
        print("MISMATCH %s" % p)
    if problems:
        print("\n%d mismatch(es) against %s" % (len(problems), path))
        return 1
    print("ok: %d constants and %d property names match %s" % (len(CONSTANTS), len(tg.PROP_NAMES), path))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
