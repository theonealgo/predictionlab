"""SQLite helpers — no global connection cache; callers manage lifecycle."""
from __future__ import annotations

import os
import shutil
import sqlite3
import stat
import tempfile
from pathlib import Path
from typing import Any, Iterable, Mapping

# Original path → resolved writable path (process-local).
_RESOLVED_WRITABLE: dict[str, Path] = {}


def _sandbox_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _cache_candidates() -> list[Path]:
    env = (os.environ.get("SPORTS_SANDBOX_WRITABLE_DB_DIR") or "").strip()
    out: list[Path] = []
    if env:
        out.append(Path(env).expanduser())
    out.append(_sandbox_root() / ".cache" / "writable_dbs")
    out.append(Path(tempfile.gettempdir()) / "sports_sandbox_writable_dbs")
    return out


def _try_chmod_user_write(path: Path) -> None:
    try:
        mode = stat.S_IMODE(path.stat().st_mode)
        os.chmod(path, mode | stat.S_IWUSR)
    except OSError:
        pass


def _probe_sqlite_writable(path: Path) -> bool:
    """True if we can actually mutate the SQLite file at path.

    Note: BEGIN IMMEDIATE can succeed on a chmod-readonly DB; INSERT/DDL cannot.
    """
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        _try_chmod_user_write(path.parent)
        if path.exists():
            _try_chmod_user_write(path)
        conn = sqlite3.connect(str(path), timeout=5)
        try:
            conn.execute("CREATE TABLE IF NOT EXISTS __writable_probe (i INTEGER)")
            conn.execute("DROP TABLE IF EXISTS __writable_probe")
            conn.commit()
        finally:
            conn.close()
        return True
    except Exception:
        return False


def ensure_writable_sqlite(db_path: Path | str, *, cache_dir: Path | None = None) -> Path:
    """Return a SQLite path that this process can write.

    If the original file/dir is read-only (chmod, sandbox, mount), copy into a
    writable cache and use that. Prefer chmod u+w on the original when possible.
    """
    path = Path(db_path)
    key = str(path)
    cached = _RESOLVED_WRITABLE.get(key)
    if cached is not None and _probe_sqlite_writable(cached):
        return cached

    if _probe_sqlite_writable(path):
        _RESOLVED_WRITABLE[key] = path
        return path

    candidates = [cache_dir] if cache_dir is not None else _cache_candidates()
    # Stable name: sport folder + filename when nested under .../<sport>/database/<file>
    stamp = f"{path.parent.parent.name}_{path.name}" if path.parent.name == "database" else path.name
    last_err: Exception | None = None
    for base in candidates:
        if base is None:
            continue
        try:
            base = Path(base)
            base.mkdir(parents=True, exist_ok=True)
            dest = base / stamp
            if path.exists():
                need_copy = (
                    not dest.exists()
                    or dest.stat().st_size == 0
                    or path.stat().st_mtime > dest.stat().st_mtime
                    or not _probe_sqlite_writable(dest)
                )
                if need_copy:
                    if dest.exists():
                        try:
                            os.chmod(dest, 0o644)
                        except OSError:
                            pass
                        try:
                            dest.unlink()
                        except OSError:
                            pass
                    shutil.copy2(path, dest)
                # copy2 preserves read-only mode bits — force owner write on the copy.
                try:
                    os.chmod(dest, 0o644)
                except OSError:
                    _try_chmod_user_write(dest)
            if _probe_sqlite_writable(dest):
                if dest.resolve() != path.resolve():
                    print(
                        f"[db] writable copy → {dest} (source not writable: {path})",
                        flush=True,
                    )
                _RESOLVED_WRITABLE[key] = dest
                return dest
        except Exception as e:
            last_err = e
            continue

    raise PermissionError(
        f"SQLite DB not writable: {path}"
        + (f" ({last_err})" if last_err else "")
    )


def connect(db_path: Path | str, *, row_factory: bool = True) -> sqlite3.Connection:
    path = ensure_writable_sqlite(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=30)
    if row_factory:
        conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_from_schema(db_path: Path | str, schema_sql: str) -> Path:
    path = ensure_writable_sqlite(db_path)
    with connect(path) as conn:
        conn.executescript(schema_sql)
        conn.commit()
    return path


def upsert_row(
    conn: sqlite3.Connection,
    table: str,
    row: Mapping[str, Any],
    conflict_cols: Iterable[str],
) -> None:
    cols = list(row.keys())
    placeholders = ", ".join("?" for _ in cols)
    col_sql = ", ".join(cols)
    conflict = ", ".join(conflict_cols)
    conflict_set = set(conflict_cols)
    updates = ", ".join(f"{c}=excluded.{c}" for c in cols if c not in conflict_set)
    sql = (
        f"INSERT INTO {table} ({col_sql}) VALUES ({placeholders}) "
        f"ON CONFLICT({conflict}) DO UPDATE SET {updates}"
    )
    conn.execute(sql, [row[c] for c in cols])


def prepare_hub_sqlite_dbs() -> list[Path]:
    """Chmod/copy known hub + isolation SQLite files so request handlers can write."""
    home = Path.home()
    root = _sandbox_root()
    paths = [
        home / "Documents/Personal/cfl/database/cfl_sandbox.db",
        home / "Documents/Personal/ufc/database/ufc_sandbox.db",
        home / "Documents/Personal/fantasy/database/fantasy_sandbox.db",
        root / "cfl/database/cfl.sqlite",
        root / "golf/database/golf.sqlite",
        root / "ufc/database/ufc.sqlite",
        root / "tennis/database/tennis.sqlite",
        root / "mlb/database/mlb.sqlite",
        root / "soccer/database/soccer.sqlite",
        root / "wnba/database/wnba.sqlite",
    ]
    ready: list[Path] = []
    for p in paths:
        try:
            if p.exists() or p.parent.is_dir():
                ready.append(ensure_writable_sqlite(p))
        except Exception as e:
            print(f"[db] prepare skip {p}: {e}", flush=True)
    return ready
