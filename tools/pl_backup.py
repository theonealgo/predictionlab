"""Local snapshot of the databases and saved pages, taken before every push and test.

    .venv/bin/python tools/pl_backup.py                   # take one now
    .venv/bin/python tools/pl_backup.py --list            # list snapshots
    .venv/bin/python tools/pl_backup.py --restore NAME    # put NAME back (stop 5122 first)
    .venv/bin/python tools/pl_backup.py --restore latest

Snapshots live in backups/pl_cursor_<YYYY-MM-DD_HH-MM-SS>/ (git-ignored); the newest
KEEP are kept. Each holds every non-empty SQLite file of the project (copied with the
SQLite backup API, so a running server's WAL is included) and the saved pages at the
top of .cache (served pages, memos, state files; scratch subfolders are skipped).
"""
from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKUPS = ROOT / "backups"
PREFIX = "pl_cursor_"
KEEP = 10
_DB_SUFFIXES = (".db", ".sqlite", ".sqlite3")
_SKIP_DIRS = {".cache", "backups", ".venv", "venv", ".pushgit", ".git", "pl_cursor", "node_modules",
              ".merge_backup", ".deploy_rehearsal", "__pycache__", "standalone-player-props copy"}


def database_files(root: Path = ROOT) -> list[Path]:
    """Non-empty SQLite files of the project, relative to root."""
    out = []
    stack = [root]
    while stack:
        d = stack.pop()
        try:
            entries = list(d.iterdir())
        except OSError:
            continue
        for p in entries:
            if p.is_dir() and not p.is_symlink():
                if p.name not in _SKIP_DIRS and not p.name.startswith(".fix_backup_"):
                    stack.append(p)
            elif p.suffix in _DB_SUFFIXES and p.is_file() and p.stat().st_size > 0:
                out.append(p.relative_to(root))
    return sorted(out)


def copy_database(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_name(dst.name + ".tmp")
    tmp.unlink(missing_ok=True)
    s = sqlite3.connect(f"file:{src}?mode=ro", uri=True, timeout=30)
    d = sqlite3.connect(tmp)
    try:
        s.backup(d)
    finally:
        d.close()
        s.close()
    tmp.replace(dst)


def snapshots() -> list[Path]:
    if not BACKUPS.is_dir():
        return []
    return sorted((p for p in BACKUPS.iterdir() if p.is_dir() and p.name.startswith(PREFIX)), key=lambda p: p.name)


def take(reason: str = "manual", quiet: bool = False) -> Path:
    stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    dest = BACKUPS / f"{PREFIX}{stamp}"
    n = 2
    while dest.exists():
        dest = BACKUPS / f"{PREFIX}{stamp}_{n}"
        n += 1
    work = dest.with_name(dest.name + ".partial")
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    dbs = []
    for rel in database_files():
        try:
            copy_database(ROOT / rel, work / "db" / rel)
            dbs.append(str(rel))
        except sqlite3.Error as exc:
            print(f"backup: skipped {rel}: {exc}", file=sys.stderr)
    pages = 0
    cache = ROOT / ".cache"
    if cache.is_dir():
        (work / "cache").mkdir()
        for p in cache.iterdir():
            if p.is_file():
                shutil.copy2(p, work / "cache" / p.name)
                pages += 1
    (work / "manifest.json").write_text(json.dumps(
        {"taken": stamp, "reason": reason, "databases": dbs, "cache_files": pages}, indent=2))
    work.rename(dest)
    for old in snapshots()[:-KEEP]:
        shutil.rmtree(old, ignore_errors=True)
    if not quiet:
        size = sum(f.stat().st_size for f in dest.rglob("*") if f.is_file()) / 1e6
        print(f"backup: {dest.relative_to(ROOT)} ({len(dbs)} databases, {pages} saved pages, {size:.0f} MB, {reason})")
    return dest


def _port_open(port: int) -> bool:
    import socket

    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def restore(name: str, force: bool = False) -> int:
    snaps = snapshots()
    src = snaps[-1] if name == "latest" and snaps else BACKUPS / name
    if not src.is_dir():
        print(f"no snapshot {name}; see --list", file=sys.stderr)
        return 1
    if _port_open(5122) and not force:
        print("the local site on 5122 is running; stop it (Ctrl+C in its window), then restore", file=sys.stderr)
        return 1
    take(reason=f"before restoring {src.name}")
    db_dir = src / "db"
    for f in sorted(db_dir.rglob("*")) if db_dir.is_dir() else []:
        if f.is_file():
            target = ROOT / f.relative_to(db_dir)
            copy_database(f, target)
            for side in ("-wal", "-shm"):
                Path(str(target) + side).unlink(missing_ok=True)
    cache_src = src / "cache"
    if cache_src.is_dir():
        cache = ROOT / ".cache"
        cache.mkdir(exist_ok=True)
        for p in cache.iterdir():
            if p.is_file():
                p.unlink()
        for f in cache_src.iterdir():
            shutil.copy2(f, cache / f.name)
    print(f"restored {src.name}. Start the local site again: ./run_local.sh")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--restore", metavar="NAME")
    ap.add_argument("--force", action="store_true", help="restore even while 5122 is running")
    ap.add_argument("--reason", default="manual")
    a = ap.parse_args()
    if a.list:
        for s in snapshots():
            try:
                m = json.loads((s / "manifest.json").read_text())
            except (OSError, ValueError):
                m = {}
            print(f"{s.name}  {m.get('reason', '')}")
        return 0
    if a.restore:
        return restore(a.restore, a.force)
    take(a.reason)
    return 0


if __name__ == "__main__":
    sys.exit(main())
