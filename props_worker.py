"""One low-priority publication job; deliberately does not import NHL77FINAL."""
import argparse
from datetime import datetime
import fcntl
import importlib
import os
from pathlib import Path
import signal
import sys
from types import ModuleType, SimpleNamespace


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", required=True)
    parser.add_argument("--cache-dir", required=True)
    parser.add_argument("--league", required=True)
    parser.add_argument("--date", required=True)
    parser.add_argument("--timeout", type=int, default=600)
    args = parser.parse_args()
    from sports_props_hub import LEAGUES
    if args.league not in LEAGUES or not Path(args.database).is_file():
        parser.error("Invalid publication job")
    datetime.strptime(args.date, "%Y-%m-%d")
    cache = Path(args.cache_dir)
    cache.mkdir(parents=True, exist_ok=True)
    with (cache / "publication.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return 75
        os.nice(15)
        signal.alarm(args.timeout)
        os.environ["PL_PROP_WORKER"] = "1"
        package = ModuleType("_pl_props_worker")
        package.__path__ = [str(Path(__file__).parent / "standalone-player-props/backend/app")]
        package.__package__ = package.__name__
        sys.modules[package.__name__] = package
        def load():
            return (importlib.import_module(package.__name__+".engine"),
                    importlib.import_module(package.__name__+".config"))
        site = SimpleNamespace(DATABASE=args.database, _PROPS_DISK_DIR=str(cache),
                               _load_props_modules=load)
        from props_service import build_one
        build_one(site, args.league, args.date)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
