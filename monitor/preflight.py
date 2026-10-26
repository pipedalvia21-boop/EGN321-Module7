"""Pre-start checks: refuse to start, with a plain-language fix, instead of crash-looping.

    python supervisor.py --check     # run only these checks and print PASS / FAIL

Added after the 8.1 handoff dry run: a busy port, a missing data file or a bad setting used to
produce either a raw Python traceback or a silent crash loop 30 s later.
"""
import importlib
import socket
import sqlite3
import sys
import time
from dataclasses import dataclass
from pathlib import Path

MIN_PYTHON = (3, 10)
PACKAGES = (("flask", "Flask"), ("waitress", "waitress"), ("psutil", "psutil"))


@dataclass
class Check:
    name: str
    ok: bool
    detail: str
    fix: str = ""


def port_free(host, port) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        if sys.platform != "win32":
            # same as waitress: closed connections in TIME_WAIT must not count as "in use"
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind((host, port))
            return True
        except OSError:
            return False


def wait_for_port(host, port, seconds) -> bool:
    """A supervisor restarted by the OS may need a moment for the previous dashboard to exit."""
    deadline = time.time() + seconds
    while True:
        if port_free(host, port):
            return True
        if time.time() >= deadline:
            return False
        time.sleep(0.5)


def _writable(folder: Path) -> tuple[bool, str]:
    try:
        folder.mkdir(parents=True, exist_ok=True)
        probe = folder / ".write_test"
        probe.write_text("ok")
        probe.unlink()
        return True, str(folder)
    except OSError as exc:
        return False, f"{folder}: {exc.strerror or exc}"


def run_checks(settings, port_wait_s=0.0) -> list[Check]:
    checks = []
    v = sys.version_info
    checks.append(Check("Python version", v >= MIN_PYTHON, f"{v.major}.{v.minor}.{v.micro} ({sys.executable})",
                        f"install Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]} or newer from https://www.python.org"))

    missing = []
    for module, pip_name in PACKAGES:
        try:
            importlib.import_module(module)
        except ImportError:
            missing.append(pip_name)
    in_venv = sys.prefix != getattr(sys, "base_prefix", sys.prefix)
    checks.append(Check("Required packages", not missing,
                        "flask, waitress, psutil installed" if not missing else "missing: " + ", ".join(missing),
                        "activate the virtual environment, then run: pip install -r requirements.txt"
                        + ("" if in_venv else "  (no virtual environment is active)")))

    m6 = Path(settings.module6_db)
    ok, detail = False, f"not found: {m6}"
    if m6.exists():
        try:
            con = sqlite3.connect(f"file:{m6}?mode=ro", uri=True)
            n = con.execute("SELECT COUNT(*) FROM sensor_readings").fetchone()[0]
            con.close()
            ok, detail = n > 0, f"{m6.name}: {n} rows"
        except sqlite3.Error as exc:
            detail = f"{m6.name} cannot be read: {exc}"
    checks.append(Check("Input data (Module 6 history)", ok, detail,
                        "this file ships with the repository: run  git checkout -- data/module6_sensor_history.db  "
                        "or clone the repository again"))

    for label, folder in (("Database folder writable", Path(settings.db_path).parent),
                          ("Log folder writable", Path(settings.log_dir))):
        ok, detail = _writable(folder)
        checks.append(Check(label, ok, detail, "move the project to a folder you can write to "
                                               "(not a read-only or synced/protected folder)"))

    db_path = Path(settings.db_path)
    if db_path.exists():
        try:
            con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
            res = con.execute("PRAGMA quick_check").fetchone()[0]
            con.close()
            checks.append(Check("Monitor database", res == "ok", f"{db_path.name}: quick_check {res}",
                                "the database is damaged: stop the system, keep a copy for diagnosis, then run "
                                "python init_db.py --reset"))
        except sqlite3.Error as exc:
            checks.append(Check("Monitor database", False, f"{db_path.name} cannot be opened: {exc}",
                                "stop the system, keep a copy, then run python init_db.py --reset"))
    else:
        checks.append(Check("Monitor database", True, f"{db_path.name} will be created on first start"))

    checks.append(_source_check(settings))

    free = wait_for_port(settings.web_host, settings.web_port, port_wait_s)
    checks.append(Check(f"Dashboard port {settings.web_port} free", free,
                        f"http://{settings.web_host}:{settings.web_port}" if free
                        else f"another program is already using port {settings.web_port}",
                        "is the monitor already running in another window? Stop it with Ctrl+C. Otherwise start on "
                        "another port:  python supervisor.py --port 5050   (on macOS, port 5000 is often taken by "
                        "AirPlay Receiver)"))
    return checks


def _source_check(settings) -> Check:
    name, sensor = settings.source_dataset, settings.source_sensor
    label = "Replay source dataset"
    paths = [p for p in (Path(settings.db_path), Path(settings.module6_db)) if p.exists()]
    if not paths:
        return Check(label, False, "cannot be checked while the input data file is missing",
                     "fix the 'Input data' problem first")
    for path in paths:
        try:
            con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
            n = con.execute("SELECT COUNT(*) FROM sensor_readings WHERE dataset = ? AND sensor_name = ?",
                            (name, sensor)).fetchone()[0]
            con.close()
        except sqlite3.Error:
            continue
        if n:
            return Check(label, True, f"{name}/{sensor}: {n} readings")
    if name == "practice_malformed":
        fix = "build it first:  python -m scripts.make_malformed_feed"
    elif name == "practice":
        fix = "the input data file is incomplete: run  git checkout -- data/module6_sensor_history.db"
    else:
        fix = "use --dataset practice (the default) or --dataset practice_malformed"
    return Check(label, False, f"dataset '{name}' has no {sensor} readings", fix)


def report(checks) -> str:
    lines = []
    for c in checks:
        lines.append(f"  [{'PASS' if c.ok else 'FAIL'}] {c.name}: {c.detail}")
        if not c.ok and c.fix:
            lines.append(f"         -> fix: {c.fix}")
    return "\n".join(lines)
