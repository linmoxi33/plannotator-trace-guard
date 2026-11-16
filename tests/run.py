#!/usr/bin/env python3
"""Test suite for plannotator-trace-guard. Zero third-party dependencies.

    python3 tests/run.py

Behavioural tests (subprocess: exit codes, --json on stdout) are marked
[behaviour]; unit tests import the module directly.
"""
import importlib.util
import json
import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import plannotator_trace_guard as tg  # noqa: E402

_spec = importlib.util.spec_from_file_location("mkex", os.path.join(ROOT, "tools", "make_examples.py"))
mkex = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mkex)

TOOL = os.path.join(ROOT, "plannotator_trace_guard.py")
RESULTS = []
TMP = tempfile.mkdtemp(prefix="traceguard-tests-")


def test(fn):
    RESULTS.append(fn)
    return fn


def write_trace(name, states, var="s"):
    path = os.path.join(TMP, name)
    payload = {"#meta": {"format": "ITF"}, "vars": [var],
               "states": [{"#meta": {"index": i}, var: st} for i, st in enumerate(states)]}
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh)
    return path


def run(args):
    return subprocess.run([sys.executable, TOOL] + args, cwd=ROOT, capture_output=True, text=True)


def violated(report, name):
    for p in report["properties"]:
        if p["name"] == name:
            return p["violations"]
    raise AssertionError("property %s not in report" % name)


def check_states(states, ids=None, **flags):
    path = write_trace("t-%d.itf.json" % len(RESULTS), states)
    f = {"windows": False, "notes_fix": False, "whats_new_fix": False, "draft_flow": False}
    f.update(flags)
    return tg.check(path, f, ids)


# --- decoder ---------------------------------------------------------------
@test
def test_decode_bigint():
    assert tg.decode({"#bigint": "42"}) == 42


@test
def test_decode_map_and_int_keys():
    got = tg.decode({"#map": [[{"#bigint": "1"}, {"#bigint": "7"}], [{"#bigint": "2"}, 3]]})
    assert got == {1: 7, 2: 3}, got
    assert tg.decode({"2": {"at": 1}}) == {2: {"at": 1}}


@test
def test_decode_set_and_tuple():
    assert tg.decode({"#set": [1, 2]}) == [1, 2]
    assert tg.decode({"#tup": [1, {"#bigint": "2"}]}) == [1, 2]


# --- loader ----------------------------------------------------------------
@test
def test_loader_rejects_non_trace():
    path = os.path.join(TMP, "bad.json")
    open(path, "w").write('{"hello": 1}')
    try:
        tg.load_trace(path)
    except tg.InputError as exc:
        assert "states" in str(exc)
        return
    raise AssertionError("expected InputError")


@test
def test_loader_accepts_namespaced_state_var():
    path = write_trace("ns.itf.json", [mkex.state()], var="updateLifecycle::s")
    var, states = tg.load_trace(path)
    assert var == "updateLifecycle::s" and len(states) == 1


# --- behavioural: exit codes and --json ------------------------------------
@test
def test_behaviour_clean_trace_exits_0_and_json_parses():
    path = write_trace("clean-behaviour.itf.json", [mkex.state(now=0), mkex.state(now=100)])
    proc = run(["--json", path])
    assert proc.returncode == 0, proc.stderr
    report = json.loads(proc.stdout)
    assert report["verdict"] == "pass" and report["violations_total"] == 0
    assert report["exit_code"] == 0


@test
def test_behaviour_violation_exits_1():
    path = write_trace("violation-behaviour.itf.json", [mkex.state(binOk=False)])
    proc = run([path])
    assert proc.returncode == 1, (proc.returncode, proc.stdout, proc.stderr)
    assert "FAIL" in proc.stdout and "BinaryAlwaysThere" in proc.stdout


@test
def test_behaviour_missing_file_exits_2():
    proc = run([os.path.join(TMP, "does-not-exist.itf.json")])
    assert proc.returncode == 2 and proc.stdout.strip() == ""
    assert "error" in proc.stderr


@test
def test_behaviour_unknown_only_exits_2():
    path = write_trace("only.itf.json", [mkex.state()])
    proc = run(["--only", "NoSuchProperty", path])
    assert proc.returncode == 2 and "unknown property" in proc.stderr


@test
def test_behaviour_bad_ids_exits_2():
    path = write_trace("ids.itf.json", [mkex.state()])
    proc = run(["--ids", "one,two", path])
    assert proc.returncode == 2 and "--ids" in proc.stderr


@test
def test_behaviour_list_properties():
    proc = run(["--list", "--json"])
    assert proc.returncode == 0
    payload = json.loads(proc.stdout)
    assert len(payload["properties"]) == 14
    names = [p["name"] for p in payload["properties"]]
    assert "NoTwoInstallers" in names and "WhatsNewWithinRun" in names


@test
def test_behaviour_only_filters_and_windows_flag_changes_verdict():
    states = [mkex.state(act="launch", now=100,
                         inst={1: {"phase": "locked", "v": 2, "person": True, "startedAt": 0},
                               2: {"phase": "spawned", "v": 3, "person": False, "startedAt": 5},
                               3: {"phase": "unused", "v": 1, "person": False, "startedAt": 0}})]
    path = write_trace("overlap.itf.json", states)
    posix = run(["--json", "--only", "NoTwoInstallers", path])
    windows = run(["--json", "--only", "NoTwoInstallers", "--windows", path])
    assert posix.returncode == 1 and windows.returncode == 0
    assert json.loads(posix.stdout)["violations_total"] == 1


# --- per-property unit tests ----------------------------------------------
@test
def test_no_two_installers_posix_bound():
    states = [mkex.state(now=100, inst={1: {"phase": "locked", "v": 2, "person": True, "startedAt": 0},
                                        2: {"phase": "replaced", "v": 3, "person": False, "startedAt": 5},
                                        3: {"phase": "unused", "v": 1, "person": False, "startedAt": 0}})]
    rep = check_states(states)
    assert violated(rep, "NoTwoInstallers"), "overlap of 100m must violate the POSIX day bound"


@test
def test_no_two_installers_allowed_past_the_bound():
    states = [mkex.state(now=2000, inst={1: {"phase": "locked", "v": 2, "person": True, "startedAt": 0},
                                         2: {"phase": "downloaded", "v": 3, "person": False, "startedAt": 5},
                                         3: {"phase": "unused", "v": 1, "person": False, "startedAt": 0}})]
    rep = check_states(states)
    assert not violated(rep, "NoTwoInstallers"), "past LOCK_LIVE_MAX the second install is allowed"


@test
def test_binary_always_there_and_inbox_can_start():
    assert violated(check_states([mkex.state(binOk=False)]), "BinaryAlwaysThere")
    assert violated(check_states([mkex.state(startFailed=True)]), "InboxCanStart")


@test
def test_nothing_fetched_while_checks_off_and_rate_bounded():
    assert violated(check_states([mkex.state(offFetch=True)]), "NothingFetchedWhileChecksOff")
    rep = check_states([mkex.state(minCheckGap=0, minNotesGap=1)])
    v = violated(rep, "RequestRateBounded")
    assert "minCheckGap=0" in v[0]["detail"] and "minNotesGap=1" in v[0]["detail"]


@test
def test_stale_lock_recovered_and_person_can_update():
    # A lock named for an installer outside IDS: liveLock holds the view while
    # anyRunning (IDS) is false. Predicate unit test, not a reachability claim.
    states = [mkex.state(now=600, lockHeld=True, lockKind="wrapper", lockAt=0, lockOwner=4,
                         inst={1: {"phase": "unused", "v": 1, "person": False, "startedAt": 0},
                               2: {"phase": "unused", "v": 1, "person": False, "startedAt": 0},
                               3: {"phase": "unused", "v": 1, "person": False, "startedAt": 0},
                               4: {"phase": "locked", "v": 2, "person": True, "startedAt": 0}})]
    rep = check_states(states, ids=[1, 2, 3])
    assert violated(rep, "StaleLockRecovered"), rep["properties"][5]
    assert violated(rep, "PersonCanUpdate")


@test
def test_whats_new_within_run_witness():
    st = mkex.state(now=100, ibUpdated=2, ibRuns=1, ibLastTick=100, latest=2, published=2, latestAt=30,
                    pubAt={2: 30}, body={2: "ours"}, oursAt={2: 50}, notes={2: "empty"},
                    nf={2: {"at": 50, "failed": False, "pub": -1, "inFlight": False, "quick": False}})
    rep = check_states([st])
    v = violated(rep, "WhatsNewWithinRun")
    assert v[0]["detail"] == "WhatsNew owed for v=2 but link=0"


@test
def test_new_release_offered_witness():
    st = mkex.state(now=100, ibLastTick=100, ibRuns=1, latest=1, published=2, pubAt={2: 0})
    rep = check_states([st])
    v = violated(rep, "NewReleaseOffered")
    assert "uiState=none" in v[0]["detail"]


@test
def test_failed_update_shown_witness():
    st = mkex.state(now=100, ibRuns=1, latest=1, personOutcome="failed", tickAfterPersonEnd=True,
                    pendingPerson=True, pendingV=2, personV=2, resultV=0, binOk=True, binV=1)
    rep = check_states([st])
    assert violated(rep, "FailedUpdateShown")


@test
def test_whats_new_after_update_witness():
    st = mkex.state(now=100, ibRuns=2, ibUpdated=2, ibLastTick=100, latest=2, published=2, latestAt=30,
                    pubAt={2: 30}, body={2: "ours"}, oursAt={2: 50}, notes={2: "empty"},
                    updatedTo={2: True}, whatsNewSeen={2: False},
                    nf={2: {"at": 50, "failed": False, "pub": -1, "inFlight": False, "quick": False}})
    rep = check_states([st])
    assert violated(rep, "WhatsNewAfterUpdate")


@test
def test_missing_field_is_reported_not_crashed():
    st = mkex.state()
    del st["binOk"]
    rep = check_states([st])
    entry = [p for p in rep["properties"] if p["name"] == "BinaryAlwaysThere"][0]
    assert entry["skipped"] and "binOk" in entry["skipped"][0]
    assert entry["violations"] == []


@test
def test_report_shape_is_stable():
    rep = check_states([mkex.state()])
    assert rep["tool"] == "plannotator-trace-guard"
    assert rep["exit_code"] == 0 and rep["verdict"] == "pass"
    assert len(rep["properties"]) == 14
    for p in rep["properties"]:
        assert set(p) >= {"name", "kind", "source", "states_checked", "violations"}
        assert p["source"].startswith("https://github.com/backnotprop/plannotator/")


@test
def test_fidelity_script_agrees_and_catches_drift():
    """Mechanism test for tools/check_fidelity.py on a generated spec file."""
    import inspect
    spec = importlib.util.spec_from_file_location(
        "cf", os.path.join(ROOT, "tools", "check_fidelity.py"))
    cf = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cf)

    def build(lock_live_max=None):
        lines = []
        for c in cf.CONSTANTS:
            value = getattr(tg, c)
            if c == "LOCK_LIVE_MAX" and lock_live_max is not None:
                value = lock_live_max
            lines.append("  pure val %s = %d" % (c, value))
        lines.append("  pure val WINDOWS = false")
        lines.append("  pure val NOTES_FIX = false")
        for name, _kind, fn, _req in tg.PROPERTIES:
            refs = " ".join(sorted(cf.upper_refs(inspect.getsource(fn)))) or "true"
            lines.append("  /// fixture body")
            lines.append("  val %s = %s" % (name, refs))
        lines.append("}")
        return "\n".join(lines) + "\n"

    good = os.path.join(TMP, "spec-good.qnt")
    bad = os.path.join(TMP, "spec-bad.qnt")
    open(good, "w").write(build())
    open(bad, "w").write(build(lock_live_max=tg.LOCK_LIVE_MAX + 1))
    script = os.path.join(ROOT, "tools", "check_fidelity.py")
    p_good = subprocess.run([sys.executable, script, good], cwd=ROOT, capture_output=True, text=True)
    p_bad = subprocess.run([sys.executable, script, bad], cwd=ROOT, capture_output=True, text=True)
    assert p_good.returncode == 0, (p_good.stdout, p_good.stderr)
    assert "14 property bodies agree" in p_good.stdout
    assert p_bad.returncode == 1 and "LOCK_LIVE_MAX" in p_bad.stdout


@test
def test_examples_out_match_a_fresh_run():
    """Every examples/out JSON was produced by the tool on examples/in."""
    import glob
    out_dir = os.path.join(ROOT, "examples", "out")
    names = sorted(os.path.basename(p) for p in glob.glob(os.path.join(out_dir, "*.json")))
    assert names, "examples/out is empty"
    mapping = {
        "clean.json": (["--json", "examples/in/clean.itf.json"], 0),
        "whats-new-link-missing.json": (["--json", "examples/in/whats-new-link-missing.itf.json"], 1),
        "lock-overlap-posix.json": (["--json", "examples/in/lock-overlap.itf.json"], 1),
        "lock-overlap-windows.json": (["--json", "--windows", "examples/in/lock-overlap.itf.json"], 0),
    }
    for name in names:
        assert name in mapping, "unexpected example output: %s" % name
        argv, expected_rc = mapping[name]
        proc = run(argv)
        assert proc.returncode == expected_rc, (name, proc.returncode, proc.stdout)
        fresh = json.loads(proc.stdout)
        stored = json.load(open(os.path.join(out_dir, name)))
        stored.pop("observed_exit_code", None)
        fresh.pop("trace", None)
        stored.pop("trace", None)
        assert fresh == stored, "%s is stale: regenerate with tools/make_examples.py" % name


def main():
    passed = failed = 0
    for fn in RESULTS:
        try:
            fn()
            passed += 1
            print("PASS %s" % fn.__name__)
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print("FAIL %s: %s: %s" % (fn.__name__, type(exc).__name__, exc))
    print("\n%d passed, %d failed" % (passed, failed))
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
