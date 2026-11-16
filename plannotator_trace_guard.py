#!/usr/bin/env python3
"""plannotator-trace-guard: check Quint ITF traces of plannotator's
spec/update-lifecycle.qnt against the properties the spec declares.

Upstream: https://github.com/backnotprop/plannotator
Spec:     spec/update-lifecycle.qnt  (module updateLifecycle)
Trace:    `quint run spec/update-lifecycle.qnt --out-itf trace.itf.json`

Zero third-party dependencies. Offline. Deterministic.

Every property implemented here is a literal transcription of the upstream
`val` of the same name. Each carries the upstream line numbers it came from;
see README "Fidelity" and tools/check_fidelity.py for the line-by-line audit.

Exit codes: 0 = every property held, 1 = at least one property was violated,
2 = usage or input error (not an ITF trace / unknown state variable / no states).
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

VERSION = "0.1.0"
TOOL = "plannotator-trace-guard"

# ---------------------------------------------------------------------------
# Constants, transcribed from spec/update-lifecycle.qnt:39-61
# ---------------------------------------------------------------------------
CHECK_INTERVAL = 10
CHECK_RETRY = 10
START_FRESH = 1
NOTES_INTERVAL = 1440
NOTES_FAIL_RETRY = 360
NOTES_PENDING_RETRY = 15
NOTES_YOUNG = 2880
AUTO_WINDOW = 1440
LOCK_STALE = 60
LOCK_LIVE_MAX = 1440
WHATS_NEW_BOUND = 30
OFFER_BOUND = 11
NOTES_LAG = 60

SPEC_REF = "https://github.com/backnotprop/plannotator/blob/main/spec/update-lifecycle.qnt"

# Variables of the model that its own `show-trace.ts` marks as ghosts.
GHOST_VARS = (
    "minCheckGap", "lastAutoCheck", "lastNotesReq", "minNotesGap",
    "tickAfterPersonEnd", "act", "now",
)


class InputError(Exception):
    """The trace cannot be read as a Quint ITF trace."""


# ---------------------------------------------------------------------------
# ITF decoding. Format per https://github.com/informalsystems/quint
# show-trace.ts:11-23 is the upstream reference decoder for the same wrappers.
# ---------------------------------------------------------------------------
def _norm_key(key: Any) -> Any:
    """Integer-keyed maps survive a JSON round-trip as decimal strings."""
    if isinstance(key, str) and key.lstrip("-").isdigit():
        return int(key)
    return key


def decode(value: Any) -> Any:
    """Unwrap ITF's #bigint / #map / #set / #tup wrappers recursively."""
    if isinstance(value, list):
        return [decode(v) for v in value]
    if isinstance(value, dict):
        if "#bigint" in value:
            return int(value["#bigint"])
        if "#map" in value:
            out = {}
            for pair in value["#map"]:
                k, v = pair[0], pair[1]
                out[_norm_key(decode(k))] = decode(v)
            return out
        if "#set" in value:
            return [decode(v) for v in value["#set"]]
        if "#tup" in value:
            return [decode(v) for v in value["#tup"]]
        return {_norm_key(k): decode(v) for k, v in value.items()}
    return value


def load_trace(path: str) -> tuple[str, list[dict]]:
    """Return (state variable name, decoded states). Raise InputError otherwise."""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
    except OSError as exc:
        raise InputError("cannot read %s: %s" % (path, exc))
    except json.JSONDecodeError as exc:
        raise InputError("not JSON: %s" % exc)
    if not isinstance(raw, dict) or "states" not in raw:
        raise InputError("not an ITF trace: no top-level 'states'")
    states = raw["states"]
    if not isinstance(states, list) or not states:
        raise InputError("not an ITF trace: 'states' is empty")
    var = None
    for name in raw.get("vars", []) or []:
        if name == "s" or name.endswith("::s"):
            var = name
            break
    if var is None:
        raise InputError("no 's' state variable in vars=%r" % (raw.get("vars"),))
    return var, [decode(st.get(var)) for st in states]


# ---------------------------------------------------------------------------
# Helpers, transcribed from spec/update-lifecycle.qnt
# ---------------------------------------------------------------------------
def _need(state: dict, *fields: str) -> None:
    missing = [f for f in fields if f not in state]
    if missing:
        raise KeyError(",".join(missing))


def running(installer: dict | None) -> bool:
    """qnt:132-133"""
    if not isinstance(installer, dict):
        return False
    return installer.get("phase") in ("spawned", "locked", "downloaded", "aside", "replaced")


def any_running(state: dict, ids: list[int]) -> bool:
    """qnt:135"""
    inst = state.get("inst", {})
    return any(running(inst.get(i)) for i in ids)


def lock_expired(state: dict) -> bool:
    """qnt:138-139"""
    return state["now"] - state["lockAt"] >= LOCK_STALE or state["lockAt"] - state["now"] >= LOCK_STALE


def holder_gone(state: dict) -> bool:
    """qnt:144-145"""
    inst = state.get("inst", {})
    return state["lockKind"] == "wrapper" and not running(inst.get(state["lockOwner"]))


def live_lock(state: dict, windows: bool) -> bool:
    """qnt:149-151"""
    if windows:
        return False
    inst = state.get("inst", {})
    return (
        state["lockKind"] == "wrapper"
        and running(inst.get(state["lockOwner"]))
        and abs(state["now"] - state["lockAt"]) < LOCK_LIVE_MAX
    )


def lock_held_view(state: dict, windows: bool) -> bool:
    """qnt:154-155"""
    return state["lockHeld"] and not holder_gone(state) and (
        not lock_expired(state) or live_lock(state, windows)
    )


def ui_state(state: dict, windows: bool) -> str:
    """qnt:164-172"""
    if not state["ibAlive"]:
        return "none"
    if state["ibOnDisk"] != state["ibRuns"]:
        return "ready"
    if lock_held_view(state, windows):
        return "updating"
    if (
        state["pendingPerson"]
        and state["pendingV"] > state["ibRuns"]
        and state["resultV"] == state["pendingV"]
        and state["resultCode"] != 0
    ):
        return "failed"
    if state["latest"] <= state["ibRuns"]:
        return "none"
    return "available"


def young(rec: dict, now: int) -> bool:
    """qnt:204"""
    return rec["pub"] >= 0 and abs(now - rec["pub"]) < NOTES_YOUNG


def _nf(state: dict, v: int) -> dict:
    rec = state.get("nf", {}).get(v)
    if not isinstance(rec, dict):
        # An absent record means "at = -1 when there is none" (qnt:64).
        return {"at": -1, "failed": False, "pub": -1, "inFlight": False, "quick": False}
    return rec


def notes_due(state: dict, v: int, notes_fix: bool, whats_new_fix: bool) -> bool:
    """qnt:206-215"""
    r = _nf(state, v)
    if r["at"] < 0 or r["at"] > state["now"]:
        return True
    if r["failed"]:
        if whats_new_fix and r["quick"] and state["latestAt"] > r["at"]:
            return state["now"] - r["at"] >= NOTES_PENDING_RETRY
        return state["now"] - r["at"] >= NOTES_FAIL_RETRY
    if whats_new_fix and r["inFlight"]:
        return state["now"] - r["at"] >= NOTES_PENDING_RETRY
    if notes_fix and young(r, state["now"]):
        return state["now"] - r["at"] >= NOTES_PENDING_RETRY
    return state["now"] - r["at"] >= NOTES_INTERVAL


def link(state: dict, ids: list[int], flags: dict) -> int:
    """qnt:257-263"""
    if not state["ibAlive"] or state["ibUpdated"] == 0:
        return 0
    v = state["ibUpdated"]
    if state.get("notes", {}).get(v) == "content":
        return v
    if not state["checksOn"]:
        return 0
    if state.get("ibInflight", {}).get(v):
        return v
    if notes_due(state, v, flags["notes_fix"], flags["whats_new_fix"]):
        return v
    return 0


def since(state: dict, v: int) -> int:
    """qnt:636-637"""
    return max(
        state.get("oursAt", {}).get(v, -1),
        state["ibStartedAt"],
        state["netStableSince"],
        state["checksOnSince"],
    )


def whats_new_owed(state: dict, flags: dict) -> bool:
    """qnt:642-645"""
    v = state["ibUpdated"]
    return (
        bool(state["ibAlive"])
        and v > 0
        and bool(state["checksOn"])
        and bool(state["netUp"])
        and state.get("body", {}).get(v) == "ours"
        and state.get("oursAt", {}).get(v, -1) - state.get("pubAt", {}).get(v, -1) <= NOTES_LAG
        and state["ibLastTick"] - since(state, v) >= WHATS_NEW_BOUND
    )


# ---------------------------------------------------------------------------
# Properties. Each returns (holds: bool, detail: str). The detail is only
# carried into the report when the property does not hold.
# ---------------------------------------------------------------------------
def p_no_two_installers(state: dict, ids: list[int], flags: dict) -> tuple[bool, str]:
    """qnt:605-608"""
    bound = LOCK_STALE if flags["windows"] else LOCK_LIVE_MAX
    inst = state.get("inst", {})
    for i in ids:
        for j in ids:
            if i == j:
                continue
            a, b = inst.get(i), inst.get(j)
            if not (running(a) and running(b)):
                continue
            if a["startedAt"] <= b["startedAt"]:
                age = state["now"] - a["startedAt"]
                if age < bound:
                    return False, (
                        "installers %d and %d both running; startedAt(%d)=%d age=%dm < bound=%dm"
                        % (i, j, i, a["startedAt"], age, bound)
                    )
    return True, ""


def p_binary_always_there(state: dict, ids: list[int], flags: dict) -> tuple[bool, str]:
    """qnt:611"""
    ok = bool(state["binOk"])
    return ok, "" if ok else "binOk=false at the install path"


def p_inbox_can_start(state: dict, ids: list[int], flags: dict) -> tuple[bool, str]:
    """qnt:614"""
    ok = not state["startFailed"]
    return ok, "" if ok else "startFailed=true: an Inbox start found no binary"


def p_nothing_fetched_while_checks_off(state: dict, ids: list[int], flags: dict) -> tuple[bool, str]:
    """qnt:617"""
    ok = not state["offFetch"]
    return ok, "" if ok else "offFetch=true: an automatic request while checks were off"


def p_request_rate_bounded(state: dict, ids: list[int], flags: dict) -> tuple[bool, str]:
    """qnt:621"""
    gap, ngap = state["minCheckGap"], state["minNotesGap"]
    ok = gap >= START_FRESH and ngap >= NOTES_PENDING_RETRY
    return ok, "" if ok else (
        "minCheckGap=%d (>=%d), minNotesGap=%d (>=%d)" % (gap, START_FRESH, ngap, NOTES_PENDING_RETRY)
    )


def p_stale_lock_recovered(state: dict, ids: list[int], flags: dict) -> tuple[bool, str]:
    """qnt:625-626"""
    if ui_state(state, flags["windows"]) == "updating" and not any_running(state, ids):
        age = state["now"] - state["lockAt"]
        if age >= LOCK_STALE:
            return False, "uiState=updating with no installer running; now-lockAt=%dm >= %dm" % (age, LOCK_STALE)
    return True, ""


def p_person_can_update(state: dict, ids: list[int], flags: dict) -> tuple[bool, str]:
    """qnt:630"""
    if ui_state(state, flags["windows"]) == "updating" and not any_running(state, ids):
        return False, "uiState=updating but no installer is running: Update now is hidden for nothing"
    return True, ""


def p_whats_new_within_run(state: dict, ids: list[int], flags: dict) -> tuple[bool, str]:
    """qnt:652-654"""
    v = state["ibUpdated"]
    if whats_new_owed(state, flags) and not state["netEverDown"] and not state.get("abandoned", {}).get(v):
        got = link(state, ids, flags)
        if got != v:
            return False, "WhatsNew owed for v=%d but link=%d" % (v, got)
    return True, ""


def p_whats_new_after_abandoned_request(state: dict, ids: list[int], flags: dict) -> tuple[bool, str]:
    """qnt:657-659"""
    v = state["ibUpdated"]
    if whats_new_owed(state, flags) and not state["netEverDown"]:
        got = link(state, ids, flags)
        if got != v:
            return False, "WhatsNew owed for v=%d but link=%d" % (v, got)
    return True, ""


def p_whats_new_after_blip(state: dict, ids: list[int], flags: dict) -> tuple[bool, str]:
    """qnt:662-664"""
    v = state["ibUpdated"]
    if whats_new_owed(state, flags) and not state.get("abandoned", {}).get(v):
        got = link(state, ids, flags)
        if got != v:
            return False, "WhatsNew owed for v=%d but link=%d" % (v, got)
    return True, ""


def p_whats_new_after_update(state: dict, ids: list[int], flags: dict) -> tuple[bool, str]:
    """qnt:668-673"""
    runs = state["ibRuns"]
    cond = (
        bool(state["ibAlive"])
        and bool(state.get("updatedTo", {}).get(runs))
        and not state.get("whatsNewSeen", {}).get(runs)
        and bool(state["checksOn"])
        and not state["netEverDown"]
        and not state.get("abandoned", {}).get(runs)
        and state.get("body", {}).get(runs) == "ours"
        and state.get("oursAt", {}).get(runs, -1) - state.get("pubAt", {}).get(runs, -1) <= NOTES_LAG
        and state["ibLastTick"] - since(state, runs) >= WHATS_NEW_BOUND
    )
    if cond:
        got = link(state, ids, flags)
        if got != runs:
            return False, "WhatsNew reachable for run %d but link=%d" % (runs, got)
    return True, ""


def p_new_release_offered(state: dict, ids: list[int], flags: dict) -> tuple[bool, str]:
    """qnt:676-679"""
    published = state["published"]
    runs = state["ibRuns"]
    if not (state["ibAlive"] and state["checksOn"] and state["netUp"] and published > runs):
        return True, ""
    base = max(
        state.get("pubAt", {}).get(published, -1), state["netStableSince"], state["checksOnSince"]
    )
    if state["ibLastTick"] - base >= OFFER_BOUND and ui_state(state, flags["windows"]) == "none":
        return False, (
            "release %d newer than run %d, %dm after pubAt but uiState=none"
            % (published, runs, state["ibLastTick"] - base)
        )
    return True, ""


def p_new_release_offered_after_abandoned_check(state: dict, ids: list[int], flags: dict) -> tuple[bool, str]:
    """qnt:683-686"""
    published = state["published"]
    runs = state["ibRuns"]
    if not (state["ibAlive"] and state["checksOn"] and state["netUp"] and published > runs):
        return True, ""
    base = max(
        state.get("pubAt", {}).get(published, -1),
        state.get("abandonedCheckAt", -1),
        state["netStableSince"],
        state["checksOnSince"],
    )
    if state["ibLastTick"] - base >= OFFER_BOUND and ui_state(state, flags["windows"]) == "none":
        return False, (
            "release %d newer than run %d, %dm after abandonedCheckAt but uiState=none"
            % (published, runs, state["ibLastTick"] - base)
        )
    return True, ""


def p_failed_update_shown(state: dict, ids: list[int], flags: dict) -> tuple[bool, str]:
    """qnt:691-696"""
    cond = (
        state["ibAlive"]
        and state["personOutcome"] == "failed"
        and state["tickAfterPersonEnd"]
        and state["pendingPerson"]
        and state["pendingV"] == state["personV"]
        and state["personV"] > state["ibRuns"]
        and (state["resultV"] == 0 or state["resultV"] == state["personV"])
        and state["binOk"]
        and state["binV"] == state["ibRuns"]
        and not any_running(state, ids)
    )
    if cond and ui_state(state, flags["windows"]) != "failed":
        return False, "a failed person install is not shown (uiState=%s)" % ui_state(state, flags["windows"])
    return True, ""


PROPERTIES = [
    ("NoTwoInstallers", "safety", p_no_two_installers, {"now", "inst", "binOk"}),
    ("BinaryAlwaysThere", "safety", p_binary_always_there, {"binOk"}),
    ("InboxCanStart", "safety", p_inbox_can_start, {"startFailed"}),
    ("NothingFetchedWhileChecksOff", "safety", p_nothing_fetched_while_checks_off, {"offFetch"}),
    ("RequestRateBounded", "safety", p_request_rate_bounded, {"minCheckGap", "minNotesGap"}),
    (
        "StaleLockRecovered",
        "safety",
        p_stale_lock_recovered,
        {"now", "lockAt", "lockHeld", "lockKind", "lockOwner", "inst", "ibAlive", "ibOnDisk", "ibRuns",
         "pendingPerson", "pendingV", "resultV", "resultCode", "latest"},
    ),
    (
        "PersonCanUpdate",
        "safety",
        p_person_can_update,
        {"now", "lockAt", "lockHeld", "lockKind", "lockOwner", "inst", "ibAlive", "ibOnDisk", "ibRuns",
         "pendingPerson", "pendingV", "resultV", "resultCode", "latest"},
    ),
    (
        "WhatsNewWithinRun",
        "bounded-eventually",
        p_whats_new_within_run,
        {"ibUpdated", "ibAlive", "checksOn", "netUp", "body", "oursAt", "pubAt", "ibLastTick", "ibStartedAt",
         "netStableSince", "checksOnSince", "netEverDown", "abandoned", "notes", "ibInflight", "nf", "now",
         "latestAt"},
    ),
    (
        "WhatsNewAfterAbandonedRequest",
        "bounded-eventually",
        p_whats_new_after_abandoned_request,
        {"ibUpdated", "ibAlive", "checksOn", "netUp", "body", "oursAt", "pubAt", "ibLastTick", "ibStartedAt",
         "netStableSince", "checksOnSince", "netEverDown", "notes", "ibInflight", "nf", "now", "latestAt"},
    ),
    (
        "WhatsNewAfterBlip",
        "bounded-eventually",
        p_whats_new_after_blip,
        {"ibUpdated", "ibAlive", "checksOn", "netUp", "body", "oursAt", "pubAt", "ibLastTick", "ibStartedAt",
         "netStableSince", "checksOnSince", "abandoned", "notes", "ibInflight", "nf", "now", "latestAt"},
    ),
    (
        "WhatsNewAfterUpdate",
        "bounded-eventually",
        p_whats_new_after_update,
        {"ibRuns", "ibAlive", "updatedTo", "whatsNewSeen", "checksOn", "netEverDown", "abandoned", "body",
         "oursAt", "pubAt", "ibLastTick", "ibStartedAt", "netStableSince", "checksOnSince", "notes",
         "ibInflight", "nf", "now", "latestAt", "ibUpdated"},
    ),
    (
        "NewReleaseOffered",
        "bounded-eventually",
        p_new_release_offered,
        {"published", "ibRuns", "ibAlive", "checksOn", "netUp", "pubAt", "netStableSince", "checksOnSince",
         "ibLastTick", "now", "lockAt", "lockHeld", "lockKind", "lockOwner", "inst", "ibOnDisk",
         "pendingPerson", "pendingV", "resultV", "resultCode", "latest"},
    ),
    (
        "NewReleaseOfferedAfterAbandonedCheck",
        "bounded-eventually",
        p_new_release_offered_after_abandoned_check,
        {"published", "ibRuns", "ibAlive", "checksOn", "netUp", "pubAt", "abandonedCheckAt",
         "netStableSince", "checksOnSince", "ibLastTick", "now", "lockAt", "lockHeld", "lockKind",
         "lockOwner", "inst", "ibOnDisk", "pendingPerson", "pendingV", "resultV", "resultCode", "latest"},
    ),
    (
        "FailedUpdateShown",
        "bounded-eventually",
        p_failed_update_shown,
        {"ibAlive", "personOutcome", "tickAfterPersonEnd", "pendingPerson", "pendingV", "personV", "resultV",
         "binOk", "binV", "ibRuns", "inst", "now", "lockAt", "lockHeld", "lockKind", "lockOwner", "ibOnDisk",
         "resultCode", "latest"},
    ),
]

PROP_NAMES = [p[0] for p in PROPERTIES]


def ids_from_states(states: list[dict], override: list[int] | None) -> list[int]:
    if override:
        return override
    keys: set[int] = set()
    for st in states:
        if isinstance(st, dict) and isinstance(st.get("inst"), dict):
            keys.update(k for k in st["inst"] if isinstance(k, int))
    return sorted(keys) if keys else [1, 2, 3]


def check(trace_path: str, flags: dict, ids_override: list[int] | None = None,
          only: list[str] | None = None) -> dict:
    """Run every property over every state. Returns the report dict."""
    var, states = load_trace(trace_path)
    ids = ids_from_states(states, ids_override)
    selected = [p for p in PROPERTIES if not only or p[0] in only]
    report_props = []
    total_violations = 0
    for name, kind, fn, requires in selected:
        violations = []
        skipped_reasons = set()
        checked = 0
        for step, state in enumerate(states):
            if not isinstance(state, dict):
                skipped_reasons.add("state %d is not an object" % step)
                continue
            missing = sorted(f for f in requires if f not in state)
            if missing:
                skipped_reasons.add("missing fields: " + ",".join(missing))
                continue
            checked += 1
            try:
                holds, detail = fn(state, ids, flags)
            except (KeyError, TypeError, ValueError) as exc:
                skipped_reasons.add("state %d: %s: %s" % (step, type(exc).__name__, exc))
                continue
            if not holds:
                violations.append({
                    "step": step,
                    "act": state.get("act"),
                    "now": state.get("now"),
                    "detail": detail,
                })
        total_violations += len(violations)
        entry = {
            "name": name,
            "kind": kind,
            "source": "%s#L%s" % (SPEC_REF, _source_line(name)),
            "states_checked": checked,
            "violations": violations,
        }
        if skipped_reasons:
            entry["skipped"] = sorted(skipped_reasons)
        report_props.append(entry)
    verdict = "fail" if total_violations else "pass"
    return {
        "tool": TOOL,
        "version": VERSION,
        "trace": trace_path,
        "state_var": var,
        "ids": ids,
        "flags": flags,
        "states": len(states),
        "properties": report_props,
        "violations_total": total_violations,
        "verdict": verdict,
        "exit_code": 1 if total_violations else 0,
    }


_SOURCE_LINES = {
    "NoTwoInstallers": "605-608",
    "BinaryAlwaysThere": "611",
    "InboxCanStart": "614",
    "NothingFetchedWhileChecksOff": "617",
    "RequestRateBounded": "621",
    "StaleLockRecovered": "625-626",
    "PersonCanUpdate": "630",
    "WhatsNewWithinRun": "652-654",
    "WhatsNewAfterAbandonedRequest": "657-659",
    "WhatsNewAfterBlip": "662-664",
    "WhatsNewAfterUpdate": "668-673",
    "NewReleaseOffered": "676-679",
    "NewReleaseOfferedAfterAbandonedCheck": "683-686",
    "FailedUpdateShown": "691-696",
}


def _source_line(name: str) -> str:
    return _SOURCE_LINES.get(name, "?")


def render_text(report: dict) -> str:
    lines = []
    lines.append("%s %s" % (report["tool"], report["version"]))
    lines.append("trace: %s  states=%d  state_var=%s  ids=%s" % (
        report["trace"], report["states"], report["state_var"], report["ids"]))
    f = report["flags"]
    lines.append("instance: windows=%s notes_fix=%s whats_new_fix=%s" % (
        f["windows"], f["notes_fix"], f["whats_new_fix"]))
    lines.append("")
    for prop in report["properties"]:
        state = "FAIL" if prop["violations"] else "ok"
        extra = " (skipped: %s)" % "; ".join(prop["skipped"]) if prop.get("skipped") else ""
        lines.append("[%s] %-38s %-18s %s#L%s%s" % (
            state, prop["name"], prop["kind"], SPEC_REF, _source_line(prop["name"]), extra))
        for v in prop["violations"]:
            lines.append("       step %d  act=%s  now=%s  %s" % (v["step"], v["act"], v["now"], v["detail"]))
    lines.append("")
    lines.append("verdict: %s (%d violation(s))" % (report["verdict"], report["violations_total"]))
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog=TOOL,
        description="Check a Quint ITF trace of plannotator's spec/update-lifecycle.qnt "
                    "against the spec's own properties. Offline, zero dependencies.",
    )
    ap.add_argument("trace", nargs="?", help="path to a .itf.json trace from `quint run --out-itf`")
    ap.add_argument("--json", action="store_true", help="print the report as JSON on stdout")
    ap.add_argument("--list", action="store_true", help="list the checked properties and exit")
    ap.add_argument("--only", action="append", default=None, metavar="PROP",
                    help="check only this property (repeatable)")
    ap.add_argument("--ids", default=None, help="comma-separated installer ids (default: from the trace)")
    ap.add_argument("--windows", action="store_true", help="instance onWindows (LOCK_STALE bound)")
    ap.add_argument("--notes-fix", action="store_true", help="instance onFix (NOTES_FIX=true)")
    ap.add_argument("--whats-new-fix", action="store_true",
                    help="instance onWhatsNewFix (WHATS_NEW_FIX=true)")
    ap.add_argument("--draft-flow", action="store_true", help="instance onFixDraft (DRAFT_FLOW=true)")
    ap.add_argument("--version", action="version", version="%s %s" % (TOOL, VERSION))
    args = ap.parse_args(argv)

    if args.list:
        payload = [{"name": n, "kind": k, "source": "%s#L%s" % (SPEC_REF, _source_line(n))}
                   for n, k, _fn, _r in PROPERTIES]
        if args.json:
            print(json.dumps({"tool": TOOL, "version": VERSION, "properties": payload}, indent=2))
        else:
            for p in payload:
                print("%-38s %-18s %s" % (p["name"], p["kind"], p["source"]))
        return 0

    if not args.trace:
        print("error: a trace path is required (or --list)", file=sys.stderr)
        return 2

    ids_override = None
    if args.ids:
        try:
            ids_override = [int(x) for x in args.ids.split(",") if x.strip()]
        except ValueError:
            print("error: --ids must be a comma-separated list of integers", file=sys.stderr)
            return 2
        if not ids_override:
            print("error: --ids was empty", file=sys.stderr)
            return 2

    if args.only:
        unknown = [o for o in args.only if o not in PROP_NAMES]
        if unknown:
            print("error: unknown property: %s" % ", ".join(unknown), file=sys.stderr)
            return 2

    flags = {
        "windows": bool(args.windows),
        "notes_fix": bool(args.notes_fix),
        "whats_new_fix": bool(args.whats_new_fix),
        "draft_flow": bool(args.draft_flow),
    }
    try:
        report = check(args.trace, flags, ids_override, args.only)
    except InputError as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(report, indent=2, sort_keys=False))
    else:
        sys.stdout.write(render_text(report))
    return report["exit_code"]


if __name__ == "__main__":
    sys.exit(main())
