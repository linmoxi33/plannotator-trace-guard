#!/usr/bin/env python3
"""Fidelity check: compare this tool's transcription with a local copy of the
upstream spec, two ways.

    python3 tools/check_fidelity.py /path/to/update-lifecycle.qnt

1. Constants and property names: every constant used here must have the same
   value in the spec, and every property name must exist as a `val`.
2. Per-property references: for each property, the set of UPPER-CASE constants
   its spec body names must equal the set the matching implementation names.
   This is the audit that "the URL exists" cannot give you — a transcription
   that reads a different bound than the spec is caught here.

Exit 0 = all checks agree. Exit 1 = at least one mismatch. Exit 2 = usage.
"""
import inspect
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
# Instance parameters and model sets that appear inside property bodies.
PARAMS = ["WINDOWS", "NOTES_FIX", "WHATS_NEW_FIX", "DRAFT_FLOW", "IDS", "VERSIONS"]
ALL_UPPER = set(CONSTANTS) | set(PARAMS)

STOP_RE = re.compile(
    r"^\s*(val\s+[A-Z]|pure\s+def\s|def\s|run\s+[a-zA-Z]|action\s|type\s|var\s|\}|// -{3,})", re.M
)


def spec_property_body(text: str, name: str) -> str | None:
    m = re.search(r"^\s*val\s+%s\s*=" % re.escape(name), text, re.M)
    if not m:
        return None
    rest = text[m.end():]
    stop = STOP_RE.search(rest)
    return rest[: stop.start()] if stop else rest


def upper_refs(blob: str) -> set:
    """Numeric/limit constants a body names.

    Instance parameters (WINDOWS, NOTES_FIX, ...) and model sets (IDS) are
    excluded: this tool receives them as call arguments, so they legitimately
    do not appear as names inside the implementation body.
    """
    return {t for t in re.findall(r"\b[A-Z][A-Z0-9_]{2,}\b", blob) if t in set(CONSTANTS)}


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
    # --- 1. names and constant values -------------------------------------
    for name in CONSTANTS:
        m = re.search(r"^\s*(?:pure\s+)?val\s+%s\s*(?::[^=]+)?=\s*(-?\d+)" % re.escape(name), text, re.M)
        if not m:
            problems.append("constant %s: not found in the spec" % name)
            continue
        if int(m.group(1)) != getattr(tg, name):
            problems.append("constant %s: spec=%s tool=%s" % (name, m.group(1), getattr(tg, name)))
    for name in tg.PROP_NAMES:
        if not re.search(r"^\s*val\s+%s\s*=" % re.escape(name), text, re.M):
            problems.append("property %s: no `val %s =` in the spec" % (name, name))

    # --- 2. per-property constant references ------------------------------
    checked = 0
    for name, _kind, fn, _requires in tg.PROPERTIES:
        body = spec_property_body(text, name)
        if body is None:
            continue  # already reported above
        checked += 1
        spec_refs = upper_refs(body)
        impl_refs = upper_refs(inspect.getsource(fn))
        if spec_refs != impl_refs:
            only_spec = sorted(spec_refs - impl_refs)
            only_impl = sorted(impl_refs - spec_refs)
            problems.append(
                "property %s: constants differ (spec only: %s; tool only: %s)"
                % (name, only_spec or "-", only_impl or "-")
            )

    for p in problems:
        print("MISMATCH %s" % p)
    if problems:
        print("\n%d mismatch(es) against %s" % (len(problems), path))
        return 1
    print("ok: %d constants, %d property names, %d property bodies agree with %s"
          % (len(CONSTANTS), len(tg.PROP_NAMES), checked, path))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
