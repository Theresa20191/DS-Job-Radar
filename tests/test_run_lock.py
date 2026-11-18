"""F5: single-writer pipeline lock. Kernel lock behaviour (contention, release
on SIGKILL, sharing with a child process) is proved with real processes; every
wait has a deadline. All locks here live under scratch data roots: the
production data folder is only ever passed to require_lock, which never
writes, and conftest fails the session if data/.pipeline.lock is touched."""

import ast
import fcntl
import hashlib
import importlib.util
import json
import os
import shutil
import signal
import subprocess
import sys
import time
from contextlib import contextmanager
from datetime import date
from pathlib import Path

import pytest

from src.career_coach import run_lock
from src.career_coach.operational import ACTIVE_OUTPUT, is_active_output, render_dashboard, write_run
from src.career_coach.run_archive import archive_id_v2, archive_run
from src.career_coach.run_audit import code_identity, write_manifest
from src.career_coach.run_lock import case_insensitive_dir
from tests.test_run_all import _recording
from tests.test_run_audit import MY_ONLY, _daily, _root, run_all

ROOT = Path(__file__).resolve().parent.parent
PRELUDE = (f"import json, os, subprocess, sys, time\nsys.path.insert(0, {str(ROOT)!r})\n"
           "from pathlib import Path\nfrom src.career_coach import run_lock\n")
DEADLINE = 20


def _script(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _wait_for(path: Path, timeout: float = DEADLINE) -> None:
    end = time.monotonic() + timeout
    while not path.exists():
        assert time.monotonic() < end, f"timed out waiting for {path}"
        time.sleep(0.02)


def _probe(data_dir: Path) -> bool:
    """Can a new, independent open of the lock file take the lock right now?"""
    fd = os.open(run_lock.lock_file(data_dir), os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except BlockingIOError:
        return False
    finally:
        os.close(fd)


def _py(code: str, **kw) -> subprocess.Popen:
    return subprocess.Popen([sys.executable, "-c", PRELUDE + code], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, **kw)


@contextmanager
def _holder(data_dir: Path, tmp_path: Path):
    """A separate process that holds data_dir's lock until the block ends."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    ready, stop = tmp_path / "holder.ready", tmp_path / "holder.stop"
    proc = _py(f"lock = run_lock.acquire(Path({str(data_dir)!r}), purpose='test holder')\n"
               f"Path({str(ready)!r}).write_text(str(os.getpid()))\n"
               f"end = time.monotonic() + {DEADLINE}\n"
               f"while time.monotonic() < end and not Path({str(stop)!r}).exists(): time.sleep(0.02)\n")
    try:
        _wait_for(ready)
        yield int(ready.read_text())
    finally:
        stop.write_text("stop")
        proc.wait(timeout=DEADLINE)


def _tree(*paths: Path) -> dict:
    """Every file below paths with its hash, the lock file excepted."""
    return {str(f): hashlib.sha256(f.read_bytes()).hexdigest() for p in paths if p.exists()
            for f in sorted(p.rglob("*")) if f.is_file() and f.name != run_lock.LOCK_NAME}


# ===================================================================== registry

SCRIPTS = {   # every scripts/*.py: how it relates to the pipeline lock
    "run_all.py": ("acquires", "the daily pipeline; passes its lock to a scraper child when one is installed"),
    "run_demo.py": ("acquires", "copies the synthetic demo raw files into data/raw under the lock, then runs run_all"),
    "record_live_checks.py": ("inherits", "appends the live-check log; inherits a parent's lock, acquires standalone"),
    "career_coach_shadow.py": ("acquires", "reads raw and the live log, writes the shared archive root"),
    "career_coach_dashboard.py": ("acquires", "renders dashboard files; refuses the active output folder"),
    "coach_log.py": ("none", "appends the application log (through application_log), which run_all never reads"),
    "log_application.py": ("none", "legacy application log, never read by run_all"),
    "outcome_report.py": ("none", "reads archives and the application log; writes data/output/learning only"),
    "generate_decision_cards.py": ("none", "legacy decision cards in data/output"),
    "generate_report.py": ("none", "legacy report data/output/daily_job_radar.md"),
    "run_pipeline.py": ("none", "legacy pipeline outputs in data/processed and data/output (via exporters)"),
    "scoring_audit.py": ("none", "legacy scoring audit in data/output"),
}
LOCKING_SCRIPTS = {name for name, (kind, _) in SCRIPTS.items() if kind != "none"}
GUARDED_NAMES = {"archive_run", "write_run", "render_dashboard", "write_manifest"}
# Path segments of data the operational pipeline reads: no unlocked script may write a path built from them.
PROTECTED_TOKENS = {"raw", "archive", "runs", "live_checks.jsonl", "live_check_evidence", ACTIVE_OUTPUT,
                    "career_coach_shadow", "ARCHIVE_ROOT", run_lock.LOCK_NAME}
UNGUARDED_WRITERS = {   # every function with a direct write but no require_lock call, and why that is safe
    "scripts/generate_decision_cards.py:main": "legacy decision cards under data/output; never read by run_all",
    "scripts/generate_report.py:main": "legacy report data/output/daily_job_radar.md; never read by run_all",
    "scripts/outcome_report.py:main": "learning report under data/output/learning; never read by run_all",
    "scripts/scoring_audit.py:main": "legacy scoring audit under data/output; never read by run_all",
    "src/career_coach/application_log.py:append_event": "the application log; run_all never reads or writes it",
    "src/strategy/application_log.py:append_event": "the legacy application log; never read by run_all",
    "src/reporting/exporters.py:export_processed": "legacy data/processed output; never read by run_all",
    "src/reporting/exporters.py:export_scored": "legacy data/output parquet; never read by run_all",
    "src/reporting/exporters.py:export_top_jobs": "legacy data/output top-jobs files; never read by run_all",
    "src/career_coach/run_lock.py:_write_diagnostics": "the lock file's own diagnostics, written while holding it",
    "src/career_coach/run_lock.py:acquire": "creates the lock folder, before the lock exists",
}
ONLY_CALLED_FROM_GUARDED = set()
WRITE_METHODS = {"write_text", "write_bytes", "mkdir", "unlink", "rmdir", "touch", "symlink_to", "chmod",
                 "to_csv", "to_parquet"}
TARGET_METHODS = {"replace", "rename"}                     # Path.replace(target): one positional argument
OS_WRITES = {("os", n) for n in ("replace", "rename", "remove", "unlink", "makedirs", "mkdir", "rmdir", "write", "pwrite",
             "ftruncate", "chmod", "symlink", "link")} | \
            {("shutil", n) for n in ("copy", "copy2", "copyfile", "copytree", "move", "rmtree")}


def _calls(tree) -> list[ast.Call]:
    return [n for n in ast.walk(tree) if isinstance(n, ast.Call)]


def _call_name(call: ast.Call) -> str:
    return call.func.attr if isinstance(call.func, ast.Attribute) else getattr(call.func, "id", "")


def _code_strings(tree) -> set[str]:
    docstrings = {id(n.body[0].value) for n in ast.walk(tree)
                  if isinstance(n, (ast.Module, ast.FunctionDef, ast.ClassDef)) and n.body
                  and isinstance(n.body[0], ast.Expr) and isinstance(n.body[0].value, ast.Constant)}
    return {n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)
            and id(n) not in docstrings}


def _write_targets(call: ast.Call) -> list[ast.AST] | None:
    """The path expressions a call writes to, or None if it does not write."""
    f = call.func
    if isinstance(f, ast.Attribute):
        if isinstance(f.value, ast.Name) and (f.value.id, f.attr) in OS_WRITES:
            return list(call.args)
        if f.attr in ("to_csv", "to_parquet"):
            return list(call.args[:1])
        if f.attr in WRITE_METHODS:
            return [f.value]
        if f.attr in TARGET_METHODS and len(call.args) == 1 and not call.keywords:
            return [f.value, call.args[0]]
    if isinstance(f, ast.Name) and f.id == "open":
        mode = call.args[1] if len(call.args) > 1 else next((k.value for k in call.keywords if k.arg == "mode"), None)
        if isinstance(mode, ast.Constant) and any(ch in str(mode.value) for ch in "wax+"):
            return list(call.args[:1])
    return None


def _writing_functions():
    """(location, function node) for every function in scripts/ and src/ with a direct write."""
    for path in sorted([*(ROOT / "scripts").glob("*.py"), *(ROOT / "src").rglob("*.py")]):
        tree = ast.parse(path.read_text())
        for fn in (n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)):
            if any(_write_targets(c) is not None for c in _calls(fn)):
                yield f"{path.relative_to(ROOT)}:{fn.name}", fn


def _guarded(fn) -> bool:
    return any(_call_name(c) == "require_lock" for c in _calls(fn))


def _path_tokens(expr: ast.AST, fn) -> set[str]:
    """String constants a path expression is built from, following simple local assignments."""
    assigned: dict[str, list[ast.AST]] = {}
    for node in ast.walk(fn):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    assigned.setdefault(target.id, []).append(node.value)
    tokens, seen, todo = set(), set(), [expr]
    while todo:
        node = todo.pop()
        for sub in ast.walk(node):
            if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
                tokens |= {sub.value, *sub.value.replace("\\", "/").split("/")}
            elif isinstance(sub, ast.Name):
                tokens.add(sub.id)
                if sub.id not in seen:
                    seen.add(sub.id)
                    todo += assigned.get(sub.id, [])
    return tokens


def test_the_write_scanner_finds_writes_and_ignores_string_methods():
    fn = ast.parse("def f(p, df, s):\n    Path(p).write_text('x')\n    open(p, 'a')\n    shutil.copy2(p, q)\n"
                   "    os.replace(a, b)\n    df.to_csv(p)\n    Path(p).replace(q)\n    s.replace('a', 'b')\n"
                   "    df.rename(columns={})\n    open(p)\n").body[0]
    found = [ast.unparse(c.func) for c in _calls(fn) if _write_targets(c) is not None]
    assert found == ["Path(p).write_text", "open", "shutil.copy2", "os.replace", "df.to_csv", "Path(p).replace"]


def test_every_script_is_classified():
    assert {p.name for p in (ROOT / "scripts").glob("*.py")} == set(SCRIPTS), "classify every script in SCRIPTS"
    assert all(kind in ("acquires", "inherits", "none") and reason for kind, reason in SCRIPTS.values())


@pytest.mark.parametrize("name", sorted(LOCKING_SCRIPTS))
def test_locking_scripts_take_the_lock(name):
    calls = _calls(ast.parse((ROOT / "scripts" / name).read_text()))
    takes = [c for c in calls if _call_name(c) in ("run_locked", "acquire")]
    assert takes, f"{name} never takes the pipeline lock"
    inherit = any(k.arg == "inherit_ok" and getattr(k.value, "value", False) for c in takes for k in c.keywords)
    assert inherit == (SCRIPTS[name][0] == "inherits")


@pytest.mark.parametrize("name", sorted(set(SCRIPTS) - LOCKING_SCRIPTS))
def test_unlocked_scripts_neither_use_the_lock_nor_reach_a_protected_writer(name):
    tree = ast.parse((ROOT / "scripts" / name).read_text())
    imported = {a.name for n in ast.walk(tree) if isinstance(n, (ast.Import, ast.ImportFrom)) for a in n.names}
    modules = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
    assert "run_lock" not in imported and "src.career_coach.run_lock" not in modules | imported, \
        f"{name} uses the lock: classify it as acquiring"
    assert not imported & GUARDED_NAMES, f"{name} imports a protected writer"
    assert not {s for s in _code_strings(tree) if any(script in s for script in LOCKING_SCRIPTS)}, \
        f"{name} names a locking script (it must not launch one)"


@pytest.mark.parametrize("name", sorted(set(SCRIPTS) - LOCKING_SCRIPTS))
def test_unlocked_scripts_write_no_path_the_pipeline_reads(name):
    tree = ast.parse((ROOT / "scripts" / name).read_text())
    for fn in (n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)):
        for call in _calls(fn):
            for target in _write_targets(call) or []:
                hit = _path_tokens(target, fn) & PROTECTED_TOKENS
                assert not hit, f"{name}:{fn.name} writes a path built from {sorted(hit)}: {ast.unparse(call)}"


def test_every_direct_write_is_guarded_or_explicitly_justified():
    unguarded = {where for where, fn in _writing_functions() if not _guarded(fn)}
    assert unguarded == set(UNGUARDED_WRITERS), (f"new unguarded writers: {sorted(unguarded - set(UNGUARDED_WRITERS))}; "
                                                 f"stale entries: {sorted(set(UNGUARDED_WRITERS) - unguarded)}")
    assert all(UNGUARDED_WRITERS.values())


def test_unguarded_helpers_are_only_called_from_guarded_functions():
    for path in [*(ROOT / "scripts").glob("*.py"), *(ROOT / "src").rglob("*.py")]:
        tree = ast.parse(path.read_text())
        for fn in (n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)):
            called = {_call_name(c) for c in _calls(fn)} & ONLY_CALLED_FROM_GUARDED
            if called:
                assert _guarded(fn), f"{path.name}:{fn.name} calls {sorted(called)} without checking the lock"


def test_nothing_ever_unlocks_explicitly():
    """An inherited descriptor shares its lock with the parent: LOCK_UN in a
    child would release the parent's lock. Releasing means closing."""
    for path in [*(ROOT / "src").rglob("*.py"), *(ROOT / "scripts").glob("*.py")]:
        tree = ast.parse(path.read_text())
        assert not [n for n in ast.walk(tree) if (isinstance(n, ast.Attribute) and n.attr == "LOCK_UN")
                    or (isinstance(n, ast.Name) and n.id == "LOCK_UN")], path


# ================================================================ low-level guards

def _guarded_calls(protected: Path):
    rec = _script("record_live_checks")
    out = protected / "output" / "x"
    run = _daily(protected.parent, protected.parent / "root_unused", out="unused")
    run.out_dir = out
    return {
        "archive_run": lambda: archive_run(protected / "nowhere", b"", b"", root=protected / "archive" / "runs"),
        "write_run": lambda: write_run(out, [], {}),
        "render_dashboard": lambda: render_dashboard(out),
        "write_manifest": lambda: write_manifest(out, {}),
        "append": lambda: rec.append([], protected / "live_checks.jsonl"),
        "stage_coach": lambda: run_all.stage_coach(run),
    }


@pytest.mark.parametrize("function", ["archive_run", "write_run", "render_dashboard", "write_manifest",
                                      "append", "stage_coach"])
def test_a_protected_write_without_the_lock_is_refused_before_any_change(isolated_lock_root, function):
    before = _tree(isolated_lock_root)
    with pytest.raises(run_lock.LockNotHeld, match="does not hold the pipeline lock"):
        _guarded_calls(isolated_lock_root)[function]()
    assert _tree(isolated_lock_root) == before and not any(isolated_lock_root.iterdir())


def test_protected_writes_succeed_while_the_lock_is_held(isolated_lock_root):
    rec = _script("record_live_checks")
    with run_lock.held(isolated_lock_root, "test"):
        write_run(isolated_lock_root / "output" / "x", [], {})
        rows = rec.build_records([{"source": "mycareersfuture", "source_job_id": "s1", "checked_on": "2026-09-25",
                                   "method": "test", "page_reviewable": "YES", "email_in_jd": "NO",
                                   "posting_status": "OPEN"}], {"s1": "job_x"})
        assert rec.append(rows, isolated_lock_root / "live_checks.jsonl") == (1, 0)
    assert (isolated_lock_root / "output" / "x" / "decisions.json").exists()


def test_the_production_data_folder_is_always_protected(isolated_lock_root):
    """Holding a scratch root's lock never allows a write to production data.
    require_lock only checks; nothing is written."""
    with run_lock.held(isolated_lock_root, "test"):
        for target in ("live_checks.jsonl", "raw", "archive/runs", f"output/{ACTIVE_OUTPUT}"):
            with pytest.raises(run_lock.LockNotHeld):
                run_lock.require_lock(run_lock.PRODUCTION_DATA / target)
        run_lock.require_lock(isolated_lock_root / "output" / "x")         # its own root is fine


def test_a_spoofed_environment_variable_satisfies_neither_inheritance_nor_the_guard(isolated_lock_root, tmp_path,
                                                                                    monkeypatch):
    other = os.open(tmp_path / "unrelated", os.O_RDWR | os.O_CREAT, 0o644)
    try:
        monkeypatch.setenv(run_lock.ENV_FD, str(other))
        with pytest.raises(run_lock.LockNotHeld):
            run_lock.require_lock(isolated_lock_root / "live_checks.jsonl")
        with pytest.raises(run_lock.LockInvalid, match="is not"):
            run_lock.acquire_or_inherit(isolated_lock_root)
        assert run_lock._HELD == {}
    finally:
        os.close(other)


def test_the_guard_reverifies_the_descriptor_on_every_call(isolated_lock_root):
    lock = run_lock.acquire(isolated_lock_root, "test")
    run_lock.require_lock(isolated_lock_root / "x")
    os.close(lock.fd)                                                    # the descriptor is gone
    try:
        with pytest.raises(run_lock.LockNotHeld, match="no longer refers"):
            run_lock.require_lock(isolated_lock_root / "x")
    finally:
        run_lock._HELD.clear()


# ===================================================================== run_all

def test_a_competing_run_all_exits_5_without_running_any_stage(tmp_path, capsys):
    root = _root(tmp_path, MY_ONLY)
    run = _daily(tmp_path, root)
    before = _tree(root, run.out_dir, run.archive_root)
    calls = []
    with _holder(root / "data", tmp_path) as holder_pid:
        assert run_all.execute(run, _recording(calls)) == 5
    out = capsys.readouterr().out
    assert calls == [] and _tree(root, run.out_dir, run.archive_root) == before
    assert "DAILY RUN REFUSED (exit 5)" in out and f"pid {holder_pid}" in out and "nothing was changed" in out
    assert run_all.EXIT_CODES["lock"] == 5


@pytest.mark.parametrize("ending", ["success", "stage failure", "KeyboardInterrupt"])
def test_run_all_releases_the_lock_however_it_ends(tmp_path, ending):
    root = _root(tmp_path, MY_ONLY)
    run = _daily(tmp_path, root)
    if ending == "success":
        assert run_all.execute(run) == 0
    elif ending == "stage failure":
        assert run_all.execute(run, _recording([], failing="pipeline")) == run_all.EXIT_CODES["pipeline"]
    else:
        with pytest.raises(KeyboardInterrupt):
            run_all.execute(run, _recording([], failing="coach", error=KeyboardInterrupt()))
    assert run.lock is None and run_lock._HELD == {} and _probe(root / "data")


def test_run_all_passes_its_lock_to_the_scraper_child(tmp_path, monkeypatch):
    """The real stage_scrape path, with a stub scraper that inherits the lock,
    rewrites the raw files and proves the guard accepts the inherited lock."""
    root = _root(tmp_path, MY_ONLY)
    monkeypatch.setenv(run_lock.ENV_DATA_ROOT, str(root / "data"))
    report = tmp_path / "child.json"
    (root / "scripts").mkdir()
    (root / "scripts" / "run_scraper.py").write_text(PRELUDE + (
        "lock = run_lock.acquire_or_inherit(purpose='stub scraper')\n"
        "raw = Path(__file__).resolve().parent.parent / 'data' / 'raw'\n"
        "run_lock.require_lock(raw)\n"
        "for s in ('mycareersfuture', 'hiredly'):\n"
        "    p = raw / f'{s}_latest.jsonl'; p.write_bytes(p.read_bytes())\n"
        f"Path({str(report)!r}).write_text(json.dumps({{'inherited': lock.inherited, "
        f"'env_left': run_lock.ENV_FD in os.environ}}))\n"))
    run = _daily(tmp_path, root, skip_scrape=False)
    run.lock = run_lock.acquire(root / "data", "test run_all")
    started = time.monotonic()
    run_all.stage_scrape(run)                                            # would raise if the child had failed
    assert time.monotonic() - started < DEADLINE
    assert json.loads(report.read_text()) == {"inherited": True, "env_left": False} and run.info["scrape"] == "OK"
    assert not _probe(root / "data")                                     # the child's exit kept the parent's lock
    run_lock.release(run.lock)
    assert _probe(root / "data")


# ===================================================== standalone writers refuse

def _never(*a, **k):
    raise AssertionError("ran despite the lock being held")


def _refusal_cases(tmp_path, monkeypatch):
    csv_path = tmp_path / "obs.csv"
    csv_path.write_text("source,source_job_id,checked_on,method,page_reviewable,email_in_jd,posting_status\n"
                        "mycareersfuture,s1,2026-09-25,test,YES,NO,OPEN\n")
    live = tmp_path / "live.jsonl"
    live.write_text('{"existing": "evidence"}\n')
    recorder = _script("record_live_checks")
    shadow, dashboard = _script("career_coach_shadow"), _script("career_coach_dashboard")
    monkeypatch.setattr(recorder, "job_ids_by_source_id", _never)
    monkeypatch.setattr(shadow, "coach_run", _never)
    monkeypatch.setattr(dashboard, "render_dashboard", _never)
    return {
        "record_live_checks": (recorder, ["record_live_checks.py", str(csv_path), "--out", str(live)]),
        "career_coach_shadow": (shadow, ["career_coach_shadow.py", "--out", str(tmp_path / "shadow_out"),
                                         "--archive-root", str(tmp_path / "shadow_archive")]),
        "career_coach_dashboard": (dashboard, ["career_coach_dashboard.py", str(tmp_path / "some_run")]),
    }


@pytest.mark.parametrize("name", ["record_live_checks", "career_coach_shadow", "career_coach_dashboard"])
def test_every_standalone_writer_refuses_before_its_first_side_effect(name, tmp_path, monkeypatch, capsys,
                                                                      isolated_lock_root):
    module, argv = _refusal_cases(tmp_path, monkeypatch)[name]
    monkeypatch.setattr("sys.argv", argv)
    before = _tree(tmp_path, isolated_lock_root)
    with _holder(isolated_lock_root, tmp_path / "h") as holder_pid:
        with pytest.raises(SystemExit) as exit_info:
            module.main()
    err = capsys.readouterr().err
    assert exit_info.value.code == 5 and f"{name}: REFUSED (exit 5)" in err and f"pid {holder_pid}" in err
    after = {k: v for k, v in _tree(tmp_path, isolated_lock_root).items() if "/h/holder." not in k}
    assert after == before and run_lock._HELD == {}


# ===================================================== recorder vs pipeline overlap

OBS_CSV = ("source,source_job_id,checked_on,method,page_reviewable,email_in_jd,posting_status\n"
           "mycareersfuture,s1,2026-09-25,test,YES,NO,OPEN\n")


def test_the_recorder_cannot_append_while_run_all_runs(tmp_path, monkeypatch):
    root = _root(tmp_path, MY_ONLY)
    data = root / "data"
    monkeypatch.setenv(run_lock.ENV_DATA_ROOT, str(data))
    live = data / "live_checks.jsonl"
    live.write_text("")
    (tmp_path / "obs.csv").write_text(OBS_CSV)
    ready, stop = tmp_path / "run.ready", tmp_path / "run.stop"
    pipeline = _py(
        "import importlib.util\nfrom datetime import date\n"
        f"spec = importlib.util.spec_from_file_location('run_all', {str(ROOT / 'scripts' / 'run_all.py')!r})\n"
        "m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)\n"
        f"run = m.DailyRun(root=Path({str(root)!r}), as_of=date(2026, 9, 25), out_dir=Path({str(tmp_path / 'out')!r}), "
        f"archive_root=Path({str(tmp_path / 'archive')!r}), live_path=Path({str(live)!r}), skip_scrape=True, "
        "code={'commit': 'c' * 40, 'tree_clean': True, 'tree_fingerprint': None})\n"
        "def block(run):\n"
        f"    Path({str(ready)!r}).write_text('x'); end = time.monotonic() + {DEADLINE}\n"
        f"    while time.monotonic() < end and not Path({str(stop)!r}).exists(): time.sleep(0.02)\n"
        "    raise RuntimeError('stopped by the test')\n"
        "sys.exit(m.execute(run, [('scrape', block)]))\n")
    recorder = _script("record_live_checks")
    try:
        _wait_for(ready)
        monkeypatch.setattr("sys.argv", ["record_live_checks.py", str(tmp_path / "obs.csv"), "--out", str(live)])
        monkeypatch.setattr(recorder, "job_ids_by_source_id", lambda sources, as_of: {"s1": "job_x"})
        with pytest.raises(SystemExit) as exit_info:
            recorder.main()
        assert exit_info.value.code == 5 and live.read_bytes() == b""
    finally:
        stop.write_text("stop")
        assert pipeline.wait(timeout=DEADLINE) == run_all.EXIT_CODES["scrape"]


def test_run_all_cannot_start_while_the_recorder_appends(tmp_path, monkeypatch, capsys):
    root = _root(tmp_path, MY_ONLY)
    data = root / "data"
    monkeypatch.setenv(run_lock.ENV_DATA_ROOT, str(data))
    live = data / "live_checks.jsonl"
    live.write_text("")
    (tmp_path / "obs.csv").write_text(OBS_CSV)
    ready, stop = tmp_path / "rec.ready", tmp_path / "rec.stop"
    recorder = _py(
        "import importlib.util\n"
        f"spec = importlib.util.spec_from_file_location('rec', {str(ROOT / 'scripts' / 'record_live_checks.py')!r})\n"
        "m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)\n"
        "real_append = m.append\n"
        "def slow_append(records, path):\n"
        f"    Path({str(ready)!r}).write_text('x'); end = time.monotonic() + {DEADLINE}\n"
        f"    while time.monotonic() < end and not Path({str(stop)!r}).exists(): time.sleep(0.02)\n"
        "    return real_append(records, path)\n"
        "m.append = slow_append\n"
        "m.job_ids_by_source_id = lambda sources, as_of: {'s1': 'job_x'}\n"
        f"sys.argv = ['record_live_checks.py', {str(tmp_path / 'obs.csv')!r}, '--out', {str(live)!r}]\n"
        "m.main()\n")
    run = _daily(tmp_path, root, live=live)
    calls = []
    try:
        _wait_for(ready)
        assert run_all.execute(run, _recording(calls)) == 5 and calls == []
        assert live.read_bytes() == b""
    finally:
        stop.write_text("stop")
        assert recorder.wait(timeout=DEADLINE) == 0
    assert len(live.read_text().splitlines()) == 1                       # the recorder finished its append afterwards
    assert "DAILY RUN REFUSED (exit 5)" in capsys.readouterr().out


# ===================================================== inherited descriptors

def _child(data_dir: Path, **kw) -> str:
    code = (f"try:\n    lock = run_lock.acquire_or_inherit(Path({str(data_dir)!r}))\n    print('OK', lock.inherited)\n"
            "    run_lock.release(lock)\n"                               # as run_locked does when a script ends
            "except run_lock.LockInvalid as e:\n    print('INVALID', e)\n")
    proc = _py(code, **kw)
    out, err = proc.communicate(timeout=DEADLINE)
    assert proc.returncode == 0, err
    return out.strip()


def test_a_valid_inherited_descriptor_is_adopted(tmp_path):
    lock = run_lock.acquire(tmp_path, "test parent")
    try:
        assert _child(tmp_path, **run_lock.child_process_args(lock)) == "OK True"
    finally:
        run_lock.release(lock)


@pytest.mark.parametrize("case", ["closed", "garbage", "unrelated file", "not holding"])
def test_invalid_inherited_descriptors_are_rejected(tmp_path, case):
    env = dict(os.environ)
    if case == "closed":
        out = _child(tmp_path, env={**env, run_lock.ENV_FD: "987"})
        assert out.startswith("INVALID") and "not an open descriptor" in out
        return
    if case == "garbage":
        out = _child(tmp_path, env={**env, run_lock.ENV_FD: "abc"})
        assert out.startswith("INVALID") and "not an open descriptor" in out
        return
    if case == "unrelated file":
        fd = os.open(tmp_path / "unrelated", os.O_RDWR | os.O_CREAT, 0o644)
        try:
            out = _child(tmp_path, pass_fds=(fd,), env={**env, run_lock.ENV_FD: str(fd)})
        finally:
            os.close(fd)
        assert out.startswith("INVALID") and "is not" in out
        return
    with _holder(tmp_path, tmp_path / "h"):                          # the lock file itself, but not holding it
        fd = os.open(run_lock.lock_file(tmp_path), os.O_RDWR)
        try:
            out = _child(tmp_path, pass_fds=(fd,), env={**env, run_lock.ENV_FD: str(fd)})
        finally:
            os.close(fd)
    assert out.startswith("INVALID") and "does not hold the lock" in out


def test_a_child_exiting_does_not_release_the_parents_lock(tmp_path):
    lock = run_lock.acquire(tmp_path, "test parent")
    try:
        assert _child(tmp_path, **run_lock.child_process_args(lock)) == "OK True"
        assert not _probe(tmp_path)
    finally:
        run_lock.release(lock)
    assert _probe(tmp_path)


def test_sigkill_of_the_parent_keeps_the_lock_while_the_inheriting_child_lives(tmp_path):
    pids, child_ready = tmp_path / "pids.json", tmp_path / "child.ready"
    child = (PRELUDE + f"lock = run_lock.acquire_or_inherit(Path({str(tmp_path)!r}))\n"
             f"Path({str(child_ready)!r}).write_text('x')\ntime.sleep(3)\n")
    parent = _py(f"lock = run_lock.acquire(Path({str(tmp_path)!r}), purpose='test parent')\n"
                 f"c = subprocess.Popen([sys.executable, '-c', {child!r}], **run_lock.child_process_args(lock))\n"
                 f"Path({str(pids)!r}).write_text(json.dumps({{'parent': os.getpid(), 'child': c.pid}}))\n"
                 "time.sleep(60)\n")
    _wait_for(child_ready)
    _wait_for(pids)
    os.kill(parent.pid, signal.SIGKILL)
    parent.wait(timeout=DEADLINE)
    killed = time.monotonic()
    assert not _probe(tmp_path)                                          # the orphaned child still holds it
    while not _probe(tmp_path):
        assert time.monotonic() - killed < DEADLINE, "the lock was never released"
        time.sleep(0.05)
    assert time.monotonic() - killed > 1.0                               # released only when the child exited


# ===================================================== diagnostics and re-entrancy

def test_stale_or_malformed_diagnostics_never_block_a_legitimate_acquisition(tmp_path):
    run_lock.lock_file(tmp_path).write_text("pid 1 forever {not json")
    lock = run_lock.acquire(tmp_path, "test")
    try:
        info = json.loads(run_lock.lock_file(tmp_path).read_text())
        assert info["pid"] == os.getpid() and info["purpose"] == "test"
    finally:
        run_lock.release(lock)


def test_a_holder_with_unreadable_diagnostics_is_still_refused(tmp_path):
    with _holder(tmp_path, tmp_path / "h"):
        run_lock.lock_file(tmp_path).write_text("garbage")              # content never decides anything
        with pytest.raises(run_lock.LockHeld, match="holder diagnostics unreadable"):
            run_lock.acquire(tmp_path, "test")
    assert run_lock._HELD == {}


def test_the_lock_is_reentrant_within_one_process(tmp_path):
    first = run_lock.acquire(tmp_path, "outer")
    second = run_lock.acquire(tmp_path, "inner")
    assert first is second
    run_lock.release(second)
    assert not _probe(tmp_path)
    run_lock.release(first)
    assert _probe(tmp_path) and run_lock._HELD == {}


# ===================================================== dashboard target protection

@pytest.mark.parametrize("name", [ACTIVE_OUTPUT, f".{ACTIVE_OUTPUT}.staging", f".{ACTIVE_OUTPUT}.previous"])
def test_the_dashboard_script_refuses_the_active_output_folder(name, isolated_lock_root, monkeypatch, capsys):
    target = isolated_lock_root / "output" / name
    target.mkdir(parents=True)
    dashboard = _script("career_coach_dashboard")
    monkeypatch.setattr(dashboard, "render_dashboard", _never)
    monkeypatch.setattr("sys.argv", ["career_coach_dashboard.py", str(target)])
    with pytest.raises(SystemExit) as exit_info:
        dashboard.main()
    assert exit_info.value.code == 2 and "active operational output folder" in capsys.readouterr().err
    assert list(target.iterdir()) == [] and not run_lock.lock_file(isolated_lock_root).exists()


def test_the_dashboard_script_refuses_the_production_active_folder_without_touching_it(monkeypatch):
    """Read-only against production: the refusal comes before any read, write or lock."""
    target = run_lock.PRODUCTION_DATA / "output" / ACTIVE_OUTPUT
    before = _tree(target)
    dashboard = _script("career_coach_dashboard")
    monkeypatch.setattr(dashboard, "render_dashboard", _never)
    monkeypatch.setattr("sys.argv", ["career_coach_dashboard.py", str(target)])
    with pytest.raises(SystemExit) as exit_info:
        dashboard.main()
    assert exit_info.value.code == 2 and _tree(target) == before


def test_the_dashboard_script_renders_any_other_folder_under_the_lock(tmp_path, monkeypatch, isolated_lock_root):
    run = _daily(tmp_path, _root(tmp_path, MY_ONLY))
    assert run_all.execute(run) == 0
    other = tmp_path / "copy"
    other.mkdir()
    for name in ("decisions.json", "run_meta.json"):
        (other / name).write_bytes((run.out_dir / name).read_bytes())
    seen = []
    dashboard = _script("career_coach_dashboard")
    real = dashboard.render_dashboard

    def spy(run_dir, **kw):
        seen.append(not _probe(isolated_lock_root))                      # held while rendering
        return real(run_dir, **kw)
    monkeypatch.setattr(dashboard, "render_dashboard", spy)
    monkeypatch.setattr("sys.argv", ["career_coach_dashboard.py", str(other), "--archive-root", str(run.archive_root),
                                     "--live-checks", str(run.live_path)])
    dashboard.main()
    assert seen == [True] and (other / "dashboard.md").exists() and _probe(isolated_lock_root)


# ===================================================== finding 1: the lock never changes the code identity

def _git(repo: Path, *args, check=True) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=check)


def test_the_production_lock_file_is_gitignored():
    """Read-only: asks git about the path; nothing is created."""
    assert _git(ROOT, "check-ignore", "-q", "data/.pipeline.lock", check=False).returncode == 0


def _scratch_repo(tmp_path: Path, gitignore: str) -> Path:
    repo = tmp_path / "repo"
    (repo / "data").mkdir(parents=True)
    (repo / ".gitignore").write_text(gitignore)
    (repo / "data" / ".gitkeep").write_text("")
    (repo / "code.py").write_text("x = 1\n")
    _git(repo, "init", "-q")
    _git(repo, "add", "-A")
    _git(repo, "-c", "user.name=t", "-c", "user.email=t@example.test", "commit", "-qm", "scratch")
    return repo


@pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")
def test_holding_the_lock_never_changes_the_code_identity_or_archive_id(tmp_path):
    repo = _scratch_repo(tmp_path, (ROOT / ".gitignore").read_text())         # the repository's own ignore rules
    assert _git(repo, "check-ignore", "-q", "data/.pipeline.lock", check=False).returncode == 0
    clean = code_identity(repo)
    assert clean["tree_clean"] and clean["tree_fingerprint"] is None
    ids = []
    for purpose in ("first run", "second run with other diagnostics"):
        lock = run_lock.acquire(repo / "data", purpose)
        assert json.loads(run_lock.lock_file(repo / "data").read_text())["purpose"] == purpose
        held_identity = code_identity(repo)
        run_lock.release(lock)
        assert held_identity == clean == code_identity(repo)                   # held, and after release
        ids.append(archive_id_v2("2026-10-10", "OPERATIONAL", {"raw_corpus": "r"}, held_identity))
    assert run_lock.lock_file(repo / "data").exists() and len(set(ids)) == 1


@pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")
def test_without_the_ignore_rule_the_lock_file_would_dirty_the_identity(tmp_path):
    """Sensitivity check for the test above: the rule is what keeps the identity clean."""
    rules = "\n".join(l for l in (ROOT / ".gitignore").read_text().splitlines() if ".pipeline.lock" not in l)
    repo = _scratch_repo(tmp_path, rules + "\n")
    lock = run_lock.acquire(repo / "data", "test")
    try:
        assert code_identity(repo)["tree_clean"] is False
    finally:
        run_lock.release(lock)


# ===================================================== finding 2: case variants and symlinks

CASE_INSENSITIVE = case_insensitive_dir(run_lock.PRODUCTION_DATA.parent)
needs_case_insensitive = pytest.mark.skipif(not CASE_INSENSITIVE, reason="the data folder's filesystem is case-sensitive")


def _variant(path: Path, style: str) -> Path:
    """path with the repository and data components re-cased (same folder on a case-insensitive filesystem)."""
    base = run_lock.PRODUCTION_DATA.parent
    rel = path.relative_to(base.parent)
    parts = [p.upper() if style == "upper" else "".join(c.upper() if i % 2 else c for i, c in enumerate(p))
             for p in rel.parts]
    return base.parent.joinpath(*parts)


@needs_case_insensitive
@pytest.mark.parametrize("style", ["upper", "mixed"])
@pytest.mark.parametrize("target", ["live_checks.jsonl", "raw/a_file_that_does_not_exist.jsonl", "archive/runs",
                                    "output/career_coach_operational/dashboard.md", "live_check_evidence/new_dir/x.csv"])
def test_case_variants_of_production_paths_still_need_the_lock(style, target):
    """Pure checks against production paths: require_lock never writes."""
    variant = _variant(run_lock.PRODUCTION_DATA / target, style)
    assert variant != run_lock.PRODUCTION_DATA / target
    with pytest.raises(run_lock.LockNotHeld):
        run_lock.require_lock(variant)


@needs_case_insensitive
@pytest.mark.parametrize("style", ["upper", "mixed"])
@pytest.mark.parametrize("name", [ACTIVE_OUTPUT, ACTIVE_OUTPUT.upper(), f".{ACTIVE_OUTPUT}.staging",
                                  f".{ACTIVE_OUTPUT.upper()}.previous", f".{ACTIVE_OUTPUT}.failed-x-y"])
def test_case_variants_of_the_active_output_folder_are_recognised(style, name):
    """Existing (the active folder) and nonexistent (dot-siblings) targets; read-only."""
    assert is_active_output(_variant(run_lock.PRODUCTION_DATA / "output", style) / name)


def test_symlinks_into_protected_data_are_followed(tmp_path):
    (tmp_path / "to_data").symlink_to(run_lock.PRODUCTION_DATA)
    (tmp_path / "to_output").symlink_to(run_lock.PRODUCTION_DATA / "output")
    (tmp_path / "to_active").symlink_to(run_lock.PRODUCTION_DATA / "output" / ACTIVE_OUTPUT)
    with pytest.raises(run_lock.LockNotHeld):
        run_lock.require_lock(tmp_path / "to_data" / "live_checks.jsonl")
    assert is_active_output(tmp_path / "to_output" / ACTIVE_OUTPUT)
    assert is_active_output(tmp_path / "to_output" / f".{ACTIVE_OUTPUT}.staging")
    assert is_active_output(tmp_path / "to_active")


def test_paths_outside_protected_data_stay_permitted(tmp_path):
    outside = tmp_path / "elsewhere" / "output"
    (outside / ACTIVE_OUTPUT).mkdir(parents=True)
    for target in (outside / ACTIVE_OUTPUT / "x.json", outside / "not_yet" / "deeper" / "x", tmp_path / "LIVE_CHECKS.jsonl"):
        run_lock.require_lock(target)                                    # no exception: not protected
    assert not is_active_output(outside / ACTIVE_OUTPUT)
    assert not is_active_output(outside / f".{ACTIVE_OUTPUT}.staging")
    assert not is_active_output(run_lock.PRODUCTION_DATA / "output" / "career_coach_shadow")


def test_the_case_probe_matches_the_filesystem(tmp_path):
    (tmp_path / "Probe").write_text("x")
    expected = (tmp_path / "pROBE").exists()
    assert case_insensitive_dir(tmp_path) is expected


def test_name_matching_folds_case_only_on_a_case_insensitive_filesystem(isolated_lock_root, monkeypatch):
    import src.career_coach.operational as operational
    output = isolated_lock_root / "output"
    output.mkdir()
    for insensitive, upper_matches in ((False, False), (True, True)):
        monkeypatch.setattr(operational, "case_insensitive_dir", lambda d, v=insensitive: v)
        assert is_active_output(output / ACTIVE_OUTPUT)
        assert is_active_output(output / ACTIVE_OUTPUT.upper()) is upper_matches
        assert is_active_output(output / f".{ACTIVE_OUTPUT.upper()}.staging") is upper_matches


# ===================================================== finding 3: lock domains and the data-root override

@pytest.fixture
def fake_production(tmp_path, monkeypatch):
    """A scratch tree standing in for production data, so domain rules can be
    exercised with real acquisitions without touching the real data folder."""
    prod = tmp_path / "repo" / "data"
    (prod / "raw").mkdir(parents=True)
    (prod / "output").mkdir()
    (prod / "live_checks.jsonl").write_text("")
    monkeypatch.setattr(run_lock, "PRODUCTION_DATA", prod)
    return prod


def _lock_files(tree: Path) -> list[Path]:
    return sorted(tree.rglob(run_lock.LOCK_NAME))


@pytest.mark.parametrize("override", ["equal", "raw inside", "containing", "case variant of equal"])
def test_an_overlapping_override_is_refused_before_any_lock_exists(fake_production, monkeypatch, capsys, override):
    target = {"equal": fake_production, "raw inside": fake_production / "raw",
              "containing": fake_production.parent, "case variant of equal": fake_production.parent / "DATA"}[override]
    if override == "case variant of equal" and not case_insensitive_dir(fake_production.parent):
        pytest.skip("case-sensitive filesystem")
    monkeypatch.setenv(run_lock.ENV_DATA_ROOT, str(target))
    with pytest.raises(run_lock.LockSetupError, match="overlaps the production data folder"):
        run_lock.data_root()
    with pytest.raises(run_lock.LockSetupError):
        run_lock.acquire()
    recorder = _script("record_live_checks")
    monkeypatch.setattr(recorder, "_main", _never)
    monkeypatch.setattr("sys.argv", ["record_live_checks.py", "obs.csv"])
    with pytest.raises(SystemExit) as exit_info:
        recorder.main()
    assert exit_info.value.code == run_lock.LOCK_SETUP_EXIT
    assert "pipeline lock setup failed" in capsys.readouterr().err
    assert _lock_files(fake_production.parent) == [] and run_lock._HELD == {}


def test_a_lock_on_another_folder_never_authorises_a_production_write(fake_production, tmp_path):
    """The reproduced bypass: holding some other lock (here a separate scratch
    domain, as an override would) must not open production data."""
    with run_lock.held(tmp_path / "scratch", "unrelated"):
        for target in (fake_production / "raw" / "x.jsonl", fake_production / "live_checks.jsonl"):
            with pytest.raises(run_lock.LockNotHeld):
                run_lock.require_lock(target)
    for explicit in (fake_production / "raw", fake_production.parent):
        with pytest.raises(run_lock.LockSetupError):
            run_lock.acquire(explicit)
    assert _lock_files(fake_production.parent) == []


def test_production_writes_need_the_production_lock_while_another_process_holds_it(fake_production, tmp_path,
                                                                                    monkeypatch):
    """A second process cannot get an alternate lock for production data through
    the override, so a protected write is refused while the pipeline holds it."""
    with _holder(fake_production, tmp_path / "h"):
        monkeypatch.setenv(run_lock.ENV_DATA_ROOT, str(fake_production / "raw"))
        with pytest.raises(run_lock.LockSetupError):
            run_lock.acquire()
        with pytest.raises(run_lock.LockNotHeld):
            run_lock.require_lock(fake_production / "raw" / "mycareersfuture_latest.jsonl")
        with pytest.raises(run_lock.LockHeld):
            run_lock.acquire(fake_production)
    assert _lock_files(fake_production.parent) == [fake_production / run_lock.LOCK_NAME]


def test_a_separate_scratch_override_is_accepted_and_production_stays_protected(fake_production, tmp_path,
                                                                                 monkeypatch):
    monkeypatch.setenv(run_lock.ENV_DATA_ROOT, str(tmp_path / "scratch"))
    assert run_lock.data_root() == tmp_path / "scratch"
    with run_lock.held(purpose="scratch"):
        run_lock.require_lock(tmp_path / "scratch" / "live_checks.jsonl")
        with pytest.raises(run_lock.LockNotHeld):
            run_lock.require_lock(fake_production / "live_checks.jsonl")
    with run_lock.held(fake_production, "production"):
        run_lock.require_lock(fake_production / "raw" / "x")
        with pytest.raises(run_lock.LockNotHeld):                       # the scratch domain needs its own lock
            run_lock.require_lock(tmp_path / "scratch" / "x")


@pytest.mark.parametrize("target", ["data", "data/raw", ".", "DATA"])
def test_overrides_aimed_at_the_real_production_tree_are_refused(monkeypatch, target):
    """Pure checks against the real tree: data_root() only inspects paths."""
    path = run_lock.PRODUCTION_DATA.parent / target
    if target == "DATA" and not CASE_INSENSITIVE:
        pytest.skip("case-sensitive filesystem")
    monkeypatch.setenv(run_lock.ENV_DATA_ROOT, str(path))
    with pytest.raises(run_lock.LockSetupError):
        run_lock.data_root()
    assert run_lock.protected_roots() == [run_lock.PRODUCTION_DATA]      # production stays protected regardless


# ===================================================== finding 4: setup failures and handle hygiene

def _open_fds() -> int:
    return len(os.listdir("/dev/fd"))


def test_a_diagnostics_write_failure_is_a_setup_error_that_releases_and_closes(tmp_path, monkeypatch):
    before = _open_fds()

    def failing(*a, **k):
        raise OSError(28, "No space left on device")
    with monkeypatch.context() as m:
        m.setattr(run_lock.os, "pwrite", failing)
        with pytest.raises(run_lock.LockSetupError, match="cannot write the lock diagnostics"):
            run_lock.acquire(tmp_path, "test")
    assert run_lock._HELD == {} and _open_fds() == before and _probe(tmp_path)


@pytest.mark.parametrize("problem", ["lock folder is a file", "lock file is a directory"])
def test_an_unusable_lock_file_is_a_setup_error(tmp_path, problem):
    before = _open_fds()
    if problem == "lock folder is a file":
        (tmp_path / "data").write_text("not a folder")
        expected = "cannot create the lock folder"
    else:
        (tmp_path / "data" / run_lock.LOCK_NAME).mkdir(parents=True)
        expected = "cannot open the lock file"
    with pytest.raises(run_lock.LockSetupError, match=expected):
        run_lock.acquire(tmp_path / "data", "test")
    assert run_lock._HELD == {} and _open_fds() == before


def test_run_all_reports_a_lock_setup_failure_without_running_any_stage(tmp_path, capsys):
    root = _root(tmp_path, MY_ONLY)
    (root / "data" / run_lock.LOCK_NAME).mkdir()                        # the lock file cannot be opened
    calls = []
    assert run_all.execute(_daily(tmp_path, root), _recording(calls)) == run_all.EXIT_CODES["lock_setup"] == 1
    out = capsys.readouterr().out
    assert calls == [] and "DAILY RUN FAILED before any stage (exit 1): pipeline lock setup failed" in out
    assert "DAILY RUN REFUSED" not in out and run_all.EXIT_CODES["lock"] == 5


def test_a_script_reports_a_lock_setup_failure_with_exit_1(isolated_lock_root, monkeypatch, capsys):
    (isolated_lock_root / run_lock.LOCK_NAME).mkdir()
    recorder = _script("record_live_checks")
    monkeypatch.setattr(recorder, "_main", _never)
    monkeypatch.setattr("sys.argv", ["record_live_checks.py", "obs.csv"])
    with pytest.raises(SystemExit) as exit_info:
        recorder.main()
    assert exit_info.value.code == 1 and "FAILED before doing anything (exit 1)" in capsys.readouterr().err


def _child_fd_state(data_dir: Path, fd_env: str | None, pass_fds=(), probe_fd: int | None = None) -> str:
    """Runs acquire_or_inherit in a child and reports whether the offered
    descriptor is still open there afterwards."""
    code = (f"try:\n    run_lock.acquire_or_inherit(Path({str(data_dir)!r}))\n    print('OK')\n"
            "except run_lock.LockInvalid:\n    print('INVALID')\n"
            f"fd = {probe_fd!r}\n"
            "if fd is not None:\n"
            "    try:\n        os.fstat(fd); print('open')\n    except OSError:\n        print('closed')\n")
    env = dict(os.environ)
    if fd_env is not None:
        env[run_lock.ENV_FD] = fd_env
    proc = _py(code, env=env, pass_fds=pass_fds)
    out, err = proc.communicate(timeout=DEADLINE)
    assert proc.returncode == 0, err
    return " ".join(out.split())


def test_rejected_inherited_handles_are_closed_only_when_they_are_this_lock_file(tmp_path):
    with _holder(tmp_path, tmp_path / "h"):
        lock_fd = os.open(run_lock.lock_file(tmp_path), os.O_RDWR)        # the lock file, not holding the lock
        other = os.open(tmp_path / "unrelated", os.O_RDWR | os.O_CREAT, 0o644)
        try:
            assert _child_fd_state(tmp_path, str(lock_fd), (lock_fd,), lock_fd) == "INVALID closed"
            assert _child_fd_state(tmp_path, str(other), (other,), other) == "INVALID open"    # not ours: left alone
            assert not _probe(tmp_path)                                  # the holder's lock is unaffected
        finally:
            os.close(lock_fd)
            os.close(other)


def test_a_second_inherited_descriptor_for_a_held_lock_is_refused_and_left_open(tmp_path, monkeypatch):
    lock = run_lock.acquire(tmp_path, "test")
    extra = os.open(run_lock.lock_file(tmp_path), os.O_RDWR)
    try:
        monkeypatch.setenv(run_lock.ENV_FD, str(extra))
        with pytest.raises(run_lock.LockInvalid, match="already holds"):
            run_lock.inherit(tmp_path)
        os.fstat(extra)                                                  # still open: not closed by inherit
        assert run_lock._HELD[lock.key] is lock and not _probe(tmp_path)
    finally:
        os.close(extra)
        run_lock.release(lock)


# ===================================================== dangling symlinks and link chains

def _records():
    rec = _script("record_live_checks")
    return rec, rec.build_records([{"source": "mycareersfuture", "source_job_id": "s1", "checked_on": "2026-09-25",
                                    "method": "test", "page_reviewable": "YES", "email_in_jd": "NO",
                                    "posting_status": "OPEN"}], {"s1": "job_x"})


def test_a_dangling_file_symlink_into_protected_data_is_refused_before_any_file_exists(fake_production, tmp_path):
    target = fake_production / "live_checks_new.jsonl"
    link = tmp_path / "dangling_file"
    link.symlink_to(target)
    assert not link.exists() and link.is_symlink()
    with pytest.raises(run_lock.LockNotHeld):
        run_lock.require_lock(link)
    rec, records = _records()
    with pytest.raises(run_lock.LockNotHeld):
        rec.append(records, link)                                        # a real writer, refused before writing
    assert not target.exists() and not link.exists()


def test_a_dangling_directory_symlink_into_protected_data_is_identified(fake_production, tmp_path):
    link = tmp_path / "dangling_dir"
    link.symlink_to(fake_production / "live_check_evidence" / "2026-10-12")
    with pytest.raises(run_lock.LockNotHeld):
        run_lock.require_lock(link / "observations.csv")
    staging = tmp_path / "to_staging"
    staging.symlink_to(fake_production / "output" / f".{ACTIVE_OUTPUT}.staging")      # does not exist
    assert is_active_output(staging)
    assert not (fake_production / "live_check_evidence").exists()


@pytest.mark.parametrize("final", ["missing file", "existing file", "missing directory"])
def test_a_symlink_chain_ending_inside_protected_data_is_refused(fake_production, tmp_path, final):
    end = {"missing file": fake_production / "raw" / "new.jsonl", "existing file": fake_production / "live_checks.jsonl",
           "missing directory": fake_production / "new_dir"}[final]
    (tmp_path / "c").symlink_to(end)
    (tmp_path / "b").symlink_to(tmp_path / "c")
    (tmp_path / "a").symlink_to(tmp_path / "b")
    target = tmp_path / "a" / "x.json" if final == "missing directory" else tmp_path / "a"
    with pytest.raises(run_lock.LockNotHeld):
        run_lock.require_lock(target)


def test_a_dangling_symlink_pointing_outside_protected_data_stays_permitted(fake_production, tmp_path):
    (tmp_path / "elsewhere").mkdir()
    link = tmp_path / "outside_link"
    link.symlink_to(tmp_path / "elsewhere" / "new.jsonl")
    run_lock.require_lock(link)
    rec, records = _records()
    assert rec.append(records, link) == (1, 0) and (tmp_path / "elsewhere" / "new.jsonl").exists()


def test_writes_through_symlinks_follow_the_lock_of_their_real_destination(fake_production, tmp_path, monkeypatch):
    scratch = tmp_path / "scratch"
    monkeypatch.setenv(run_lock.ENV_DATA_ROOT, str(scratch))
    scratch.mkdir()
    to_scratch, to_production = tmp_path / "to_scratch", tmp_path / "to_production"
    to_scratch.symlink_to(scratch / "live_checks.jsonl")                 # both dangling
    to_production.symlink_to(fake_production / "live_checks_new.jsonl")
    rec, records = _records()
    with pytest.raises(run_lock.LockNotHeld):
        rec.append(records, to_scratch)                                  # the scratch domain needs its own lock
    with run_lock.held(purpose="scratch"):
        assert rec.append(records, to_scratch) == (1, 0) and (scratch / "live_checks.jsonl").exists()
        with pytest.raises(run_lock.LockNotHeld):
            rec.append(records, to_production)                           # the scratch lock never opens production
    with run_lock.held(fake_production, "production"):
        assert rec.append(records, to_production) == (1, 0)
    assert (fake_production / "live_checks_new.jsonl").exists()
