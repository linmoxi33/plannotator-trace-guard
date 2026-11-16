#!/usr/bin/env python3
"""Build the example ITF traces and run plannotator-trace-guard over them,
writing examples/out/*.json from the real invocation output.

Run from the repository root:  python3 tools/make_examples.py
"""
import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
IN = os.path.join(ROOT, "examples", "in")
OUT = os.path.join(ROOT, "examples", "out")
TOOL = os.path.join(ROOT, "plannotator_trace_guard.py")


def state(**over):
    """A minimal but complete updateLifecycle state (all fields the properties read)."""
    s = {
        "act": "init", "now": 100,
        "inst": {i: {"phase": "unused", "v": 1, "person": False, "startedAt": 0} for i in (1, 2, 3)},
        "binOk": True, "binV": 1,
        "startFailed": False, "offFetch": False,
        "minCheckGap": 10, "minNotesGap": 15,
        "lockHeld": False, "lockKind": "none", "lockAt": -99999, "lockOwner": 0,
        "ibAlive": True, "ibRuns": 1, "ibOnDisk": 1, "ibStartedAt": 0, "ibLastTick": 100,
        "ibUpdated": 0, "ibInflight": {}, "notes": {},
        "latest": 1, "latestAt": 90, "published": 1, "pubAt": {}, "body": {}, "oursAt": {},
        "nf": {}, "updatedTo": {}, "whatsNewSeen": {}, "abandoned": {}, "abandonedCheckAt": -1,
        "pendingPerson": False, "pendingV": 0, "personV": 0, "personOutcome": "none",
        "resultV": 0, "resultCode": 0,
        "checksOn": True, "autoOn": True, "netUp": True, "netEverDown": False,
        "netStableSince": 0, "checksOnSince": 0, "tickAfterPersonEnd": False,
    }
    s.update(over)
    return s


def itf(module, states):
    """ITF: top-level `vars`, and each state is a map var -> value (plus #meta)."""
    wrapped = [{"#meta": {"index": i}, "s": st} for i, st in enumerate(states)]
    return {"#meta": {"format": "ITF", "source": "quint", "module": module}, "vars": ["s"], "states": wrapped}


def write(name, payload):
    path = os.path.join(IN, name)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2)
        fh.write("\n")
    return path


def main():
    os.makedirs(IN, exist_ok=True)
    os.makedirs(OUT, exist_ok=True)

    # 1. A clean trace: no property fires.
    clean = itf("onMain", [
        state(act="init", now=0),
        state(act="doCliUpdateNow", now=1),
        state(act="tick", now=100),
    ])
    # 2. The 0.28.37 known bug isolated (spec/README.md): the Inbox runs on v2
    #    while the release still carries GitHub's body, the written notes land
    #    later, and the "What's new" link never appears in that run.
    missing_link = itf("onMain", [
        state(act="doPublish", now=0, published=2, latest=2, pubAt={2: 30}, body={2: "generated"}, latestAt=30),
        state(act="doNotesWritten", now=50, published=2, latest=2, latestAt=30,
              pubAt={2: 30}, body={2: "ours"}, oursAt={2: 50},
              ibUpdated=2, ibRuns=1, ibLastTick=50, notes={2: "empty"},
              nf={2: {"at": 50, "failed": False, "pub": -1, "inFlight": False, "quick": False}}),
        state(act="tick", now=100, published=2, latest=2, latestAt=30,
              pubAt={2: 30}, body={2: "ours"}, oursAt={2: 50},
              ibUpdated=2, ibRuns=1, ibLastTick=100, notes={2: "empty"},
              nf={2: {"at": 50, "failed": False, "pub": -1, "inFlight": False, "quick": False}}),
    ])
    # 3. Two installers overlap by 100 minutes. Allowed once the first has run
    #    past the lock's limit: a day on POSIX, the hour on Windows.
    overlap = itf("onMain", [
        state(act="launch", now=100,
              inst={1: {"phase": "locked", "v": 2, "person": True, "startedAt": 0},
                    2: {"phase": "spawned", "v": 3, "person": False, "startedAt": 5},
                    3: {"phase": "unused", "v": 1, "person": False, "startedAt": 0}},
              lockHeld=True, lockKind="wrapper", lockAt=0, lockOwner=1,
              pendingPerson=True, pendingV=2, resultV=0),
    ])

    write("clean.itf.json", clean)
    write("whats-new-link-missing.itf.json", missing_link)
    write("lock-overlap.itf.json", overlap)

    runs = [
        (["examples/in/clean.itf.json"], "clean.json"),
        (["examples/in/whats-new-link-missing.itf.json"], "whats-new-link-missing.json"),
        (["examples/in/lock-overlap.itf.json"], "lock-overlap-posix.json"),
        (["--windows", "examples/in/lock-overlap.itf.json"], "lock-overlap-windows.json"),
    ]
    for argv, out_name in runs:
        proc = subprocess.run([sys.executable, TOOL, "--json"] + argv,
                              cwd=ROOT, capture_output=True, text=True)
        if proc.stdout.strip() == "" or proc.returncode not in (0, 1):
            sys.stderr.write("unexpected failure for %r: rc=%s\n%s\n" % (argv, proc.returncode, proc.stderr))
            return 1
        report = json.loads(proc.stdout)
        report["observed_exit_code"] = proc.returncode
        with open(os.path.join(OUT, out_name), "w", encoding="utf-8") as fh:
            json.dump(report, fh, indent=2, sort_keys=False)
            fh.write("\n")
        print("%-34s exit=%d verdict=%s violations=%d" % (
            out_name, proc.returncode, report["verdict"], report["violations_total"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
