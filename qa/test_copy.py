"""Start a test server on its own copy of the project; the local 5122 site is never touched.

    .venv/bin/python qa/test_copy.py start [--port 5131] [--commit SHA]
    .venv/bin/python qa/test_copy.py stop  [--port 5131]
    .venv/bin/python qa/test_copy.py list

start: takes a backup (tools/pl_backup.py), checks the commit (HEAD by default) out into a
temporary folder, lays the uncommitted changes of the working tree on top (unless --commit
is given), copies every database and .env into it, and runs gunicorn there with
PL_AUTOREFRESH=0. stop: kills that server and deletes the copy. Port 5122 is refused.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import pl_backup  # noqa: E402

GIT = ["/usr/bin/git", f"--git-dir={ROOT / '.pushgit'}"]
VENV_PY = ROOT / ".venv" / "bin" / "python"
STATE = Path(tempfile.gettempdir()) / "pl_test_copies.json"
LOCAL_SITE_PORT = 5122


def _load() -> dict:
    try:
        return json.loads(STATE.read_text())
    except (OSError, ValueError):
        return {}


def _save(state: dict) -> None:
    STATE.write_text(json.dumps(state, indent=2))


def _port_busy(port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def _git(*args: str, cwd: Path = ROOT) -> str:
    return subprocess.check_output([*GIT, f"--work-tree={cwd}", *args], cwd=cwd, text=True)


def start(port: int, commit: str | None, env_extra: list[str]) -> int:
    if port == LOCAL_SITE_PORT:
        print("refused: 5122 is the local site", file=sys.stderr)
        return 1
    if _port_busy(port):
        print(f"refused: port {port} is already in use", file=sys.stderr)
        return 1
    pl_backup.take(reason=f"before test copy on {port}")
    work = Path(tempfile.mkdtemp(prefix=f"pl_test_{port}_"))
    if work.resolve() == ROOT or ROOT in work.resolve().parents:
        print("refused: the copy must be outside the project folder", file=sys.stderr)
        return 1
    shutil.rmtree(work)
    sha = commit or "HEAD"
    subprocess.check_call([*GIT, "worktree", "add", "--detach", "-f", str(work), sha],
                          cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if not commit:
        changed = _git("diff", "--name-only", "HEAD").split("\n")
        changed += _git("ls-files", "--others", "--exclude-standard").split("\n")
        for rel in filter(None, changed):
            src, dst = ROOT / rel, work / rel
            if src.is_file():
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
            elif not src.exists() and dst.exists():
                dst.unlink()
    for rel in pl_backup.database_files():
        pl_backup.copy_database(ROOT / rel, work / rel)
    for name in (".env",):
        if (ROOT / name).exists():
            shutil.copy2(ROOT / name, work / name)

    env = dict(os.environ, PORT=str(port), PL_AUTOREFRESH="0", PYTHONUNBUFFERED="1")
    env.pop("RENDER", None)
    for kv in env_extra:
        k, _, v = kv.partition("=")
        env[k] = v
    log_path = work / "test_server.log"
    proc = subprocess.Popen([str(VENV_PY), "-m", "gunicorn", "-c", "gunicorn.conf.py", "NHL77FINAL:app"],
                            cwd=work, env=env, stdout=open(log_path, "w"), stderr=subprocess.STDOUT,
                            start_new_session=True)
    state = _load()
    state[str(port)] = {"pid": proc.pid, "dir": str(work), "commit": sha, "started": time.time()}
    _save(state)
    for _ in range(90):
        if proc.poll() is not None:
            print(f"test server exited; log: {log_path}", file=sys.stderr)
            stop(port)
            return 1
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz", timeout=3)
            print(f"test copy up: http://127.0.0.1:{port}  (folder {work}, log {log_path})")
            print(f"stop it with: .venv/bin/python qa/test_copy.py stop --port {port}")
            return 0
        except Exception:
            time.sleep(2)
    print(f"still loading after 3 minutes; log: {log_path}")
    return 0


def stop(port: int) -> int:
    state = _load()
    info = state.pop(str(port), None)
    if not info:
        print(f"no test copy recorded on {port}")
        return 1
    try:
        os.killpg(info["pid"], signal.SIGTERM)
        time.sleep(3)
        os.killpg(info["pid"], signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass
    subprocess.call([*GIT, "worktree", "remove", "--force", info["dir"]], cwd=ROOT,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    shutil.rmtree(info["dir"], ignore_errors=True)
    subprocess.call([*GIT, "worktree", "prune"], cwd=ROOT)
    _save(state)
    print(f"stopped test copy on {port}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("action", choices=("start", "stop", "list"))
    ap.add_argument("--port", type=int, default=5131)
    ap.add_argument("--commit", help="test this commit as-is (no working-tree changes)")
    ap.add_argument("--env", action="append", default=[], help="extra KEY=VALUE for the server")
    a = ap.parse_args()
    if a.action == "start":
        return start(a.port, a.commit, a.env)
    if a.action == "stop":
        return stop(a.port)
    print(json.dumps(_load(), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
