"""One app, per-sport copies of helper modules, templates and static files.

The sport folders (mlb/, nfl/, ...) each changed shared helpers in their own
way. Each folded sport keeps its exact copy under
``sport_variants/<sport>/<same relative path>``. The file at the normal path
is the default copy (used by every sport without its own copy).

Which copy runs:
  1. a ``sport`` / ``sport_u`` / ``league`` argument on the call, else
  2. the sport in the request URL (/ncaaf-picks, /sport/NCAAF/..., ?sport=), else
  3. the default copy.
"""
from __future__ import annotations

import functools
import importlib.util
import inspect
import os
import re
import sys
import threading
import types
from importlib.machinery import SourceFileLoader

ROOT = os.path.dirname(os.path.abspath(__file__))
VARIANT_DIR = os.path.join(ROOT, "sport_variants")
SPORTS = ("mlb", "nhl", "nba", "nfl", "ncaaf", "ncaab", "ncaaw", "wnba", "cfl", "soccer", "tennis", "ufc", "golf")
_SPORT_ALT = "|".join(SPORTS)
_PATH_RE = re.compile(rf"^/(?:sport/|api/picks/|blog/)?({_SPORT_ALT})(?=[-/_.]|$)", re.I)
_SPORT_ARG_NAMES = ("sport", "sport_u", "sport_key", "sport_l", "league", "slug")
_local = threading.local()

# module name -> {sport: variant file}
MODULES: dict[str, dict[str, str]] = {}
# template name -> set of sports with their own copy
TEMPLATES: dict[str, set[str]] = {}
# static path (under /static/) -> set of sports with their own copy
STATIC: dict[str, set[str]] = {}


def _scan() -> None:
    if not os.path.isdir(VARIANT_DIR):
        return
    for sport in SPORTS:
        sroot = os.path.join(VARIANT_DIR, sport)
        if not os.path.isdir(sroot):
            continue
        for dirpath, dirnames, filenames in os.walk(sroot):
            dirnames[:] = [d for d in dirnames if d != "__pycache__"]
            for fname in filenames:
                full = os.path.join(dirpath, fname)
                rel = os.path.relpath(full, sroot).replace(os.sep, "/")
                if rel.startswith("templates/"):
                    TEMPLATES.setdefault(rel[len("templates/"):], set()).add(sport)
                elif rel.startswith("static/"):
                    STATIC.setdefault(rel[len("static/"):], set()).add(sport)
                elif rel.endswith(".py"):
                    mod = rel[:-3].replace("/", ".")
                    if mod.endswith(".__init__"):
                        continue
                    MODULES.setdefault(mod, {})[sport] = full


def _sport_from_value(value) -> str | None:
    if value is None:
        return None
    s = str(value).strip().lower()
    return s if s in SPORTS else None


def current_sport() -> str | None:
    stack = getattr(_local, "stack", None)
    if stack:
        return stack[-1]
    try:
        from flask import has_request_context, request

        if has_request_context():
            m = _PATH_RE.match(request.path or "")
            if m:
                return m.group(1).lower()
            return _sport_from_value(request.args.get("sport"))
    except Exception:
        pass
    return None


class sport_scope:
    """``with sport_scope("ncaaf"):`` runs that sport's copies inside the block."""

    def __init__(self, sport):
        self.sport = _sport_from_value(sport)

    def __enter__(self):
        if self.sport:
            _local.stack = getattr(_local, "stack", []) + [self.sport]
        return self

    def __exit__(self, *exc):
        if self.sport:
            _local.stack = _local.stack[:-1]
        return False


def _explicit_sport(sig, arg_names, args, kwargs) -> str | None:
    """Sport named by the call itself: a sport argument, or a card/game dict's 'sport'."""
    if sig is None:
        return None
    try:
        bound = sig.bind_partial(*args, **kwargs)
    except TypeError:
        return None
    for n in arg_names:
        if n in bound.arguments:
            s = _sport_from_value(bound.arguments[n])
            if s:
                return s
    for value in bound.arguments.values():
        if isinstance(value, dict) and "sport" in value:
            s = _sport_from_value(value.get("sport"))
            if s:
                return s
            break
    return None


def per_sport(**variant_names):
    """Run a sport's own copy of a function for that sport; everyone else runs this one.

        @per_sport(ncaaf="_fn__ncaaf")
        def _fn(...): ...
    """

    def deco(fn):
        try:
            sig = inspect.signature(fn)
            arg_names = [n for n in _SPORT_ARG_NAMES if n in sig.parameters]
        except (TypeError, ValueError):
            sig, arg_names = None, []
        glb = fn.__globals__

        @functools.wraps(fn)
        def dispatch(*args, **kwargs):
            sport = _explicit_sport(sig, arg_names, args, kwargs) or current_sport()
            name = variant_names.get(sport) if sport else None
            target = glb.get(name, fn) if name else fn
            with sport_scope(sport):
                return target(*args, **kwargs)

        dispatch.__wrapped_default__ = fn
        return dispatch

    return deco


class _VariantModule(types.ModuleType):
    """Stands in sys.modules for a helper that has per-sport copies."""

    def __init__(self, name: str, real_path: str, variants: dict[str, str]):
        super().__init__(name)
        object.__setattr__(self, "_pv_real_path", real_path)
        object.__setattr__(self, "_pv_variants", variants)
        object.__setattr__(self, "_pv_loaded", {})
        object.__setattr__(self, "_pv_wrappers", {})
        object.__setattr__(self, "_pv_saved", {})
        object.__setattr__(self, "_pv_lock", threading.RLock())
        object.__setattr__(self, "__file__", real_path)
        pkg = name.rpartition(".")[0]
        object.__setattr__(self, "__package__", pkg)

    def _pv_load(self, key: str):
        loaded = object.__getattribute__(self, "_pv_loaded")
        mod = loaded.get(key)
        if mod is not None:
            return mod
        with object.__getattribute__(self, "_pv_lock"):
            mod = loaded.get(key)
            if mod is not None:
                return mod
            name = object.__getattribute__(self, "__name__")
            real = object.__getattribute__(self, "_pv_real_path")
            src = real if key == "default" else object.__getattribute__(self, "_pv_variants")[key]
            # __file__ stays the normal path so file-relative paths (.cache/, data/) still resolve.
            loader = SourceFileLoader(name, src)
            spec = importlib.util.spec_from_file_location(name, real, loader=loader)
            mod = importlib.util.module_from_spec(spec)
            mod.__package__ = name.rpartition(".")[0]
            loaded[key] = mod
            try:
                if key == "default":
                    spec.loader.exec_module(mod)
                else:
                    with sport_scope(key):
                        spec.loader.exec_module(mod)
            except BaseException:
                loaded.pop(key, None)
                raise
            return mod

    def _pv_for(self, sport: str | None):
        variants = object.__getattribute__(self, "_pv_variants")
        if sport and sport in variants:
            return self._pv_load(sport)
        return self._pv_load("default")

    def _pv_find(self, attr: str, sport: str | None):
        """attr from the sport's copy, else the default, else any copy that has it."""
        for key in ([sport] if sport else []) + ["default"]:
            mod = self._pv_for(key) if key != "default" else self._pv_load("default")
            if hasattr(mod, attr):
                return getattr(mod, attr)
        for key in object.__getattribute__(self, "_pv_variants"):
            mod = self._pv_load(key)
            if hasattr(mod, attr):
                return getattr(mod, attr)
        raise AttributeError(f"module {object.__getattribute__(self, '__name__')!r} has no attribute {attr!r}")

    def __getattr__(self, attr: str):
        if attr.startswith("__") and attr.endswith("__"):
            return getattr(self._pv_load("default"), attr)
        val = self._pv_find(attr, current_sport())
        if isinstance(val, types.FunctionType):
            return self._pv_wrapper(attr, val)
        return val

    def __setattr__(self, attr, value):
        if attr.startswith("__") and attr.endswith("__"):
            object.__setattr__(self, attr, value)
            return
        self._pv_load("default")
        saved = object.__getattribute__(self, "_pv_saved")
        restoring = object.__getattribute__(self, "_pv_wrappers").get(attr) is value
        for key, mod in list(object.__getattribute__(self, "_pv_loaded").items()):
            if restoring:
                # ``from mod import f`` handed out the dispatcher; putting it back
                # into a copy would make the dispatcher call itself.
                if (key, attr) in saved:
                    setattr(mod, attr, saved.pop((key, attr)))
                continue
            if (key, attr) not in saved and attr in mod.__dict__:
                saved[(key, attr)] = mod.__dict__[attr]
            setattr(mod, attr, value)

    def __dir__(self):
        return dir(self._pv_load("default"))

    def _pv_wrapper(self, attr: str, sample):
        wrappers = object.__getattribute__(self, "_pv_wrappers")
        w = wrappers.get(attr)
        if w is not None:
            return w
        try:
            sig = inspect.signature(sample)
            arg_names = [n for n in _SPORT_ARG_NAMES if n in sig.parameters]
        except (TypeError, ValueError):
            sig, arg_names = None, []
        proxy = self

        @functools.wraps(sample)
        def dispatch(*args, **kwargs):
            sport = _explicit_sport(sig, arg_names, args, kwargs) or current_sport()
            fn = proxy._pv_find(attr, sport)
            with sport_scope(sport):
                return fn(*args, **kwargs)

        wrappers[attr] = dispatch
        return dispatch


# Folders that live code puts on sys.path and imports from by bare name
# (``import tennis_page`` with iso_hub/ on the path).
_FLAT_DIRS = ("iso_hub",)
# bare module name -> stand-in; served by _FlatFinder even after a loader pops sys.modules
FLAT: dict[str, "_VariantModule"] = {}


class _FlatFinder:
    """Serves the stand-in for a bare iso_hub name ahead of the normal path search."""

    def find_spec(self, fullname, path=None, target=None):
        proxy = FLAT.get(fullname)
        if proxy is None:
            return None
        return importlib.util.spec_from_loader(fullname, self)

    def create_module(self, spec):
        return FLAT[spec.name]

    def exec_module(self, module):
        return None


def source_for(rel: str, sport: str) -> str:
    """The sport's copy of ``rel`` (path relative to the app root), else the normal file."""
    own = os.path.join(VARIANT_DIR, sport, *rel.split("/"))
    return own if os.path.isfile(own) else os.path.join(ROOT, *rel.split("/"))


def _install_flat() -> None:
    for name, variants in MODULES.items():
        parent, _, child = name.rpartition(".")
        if parent not in _FLAT_DIRS or child in FLAT:
            continue
        real = os.path.join(ROOT, parent, child + ".py")
        if not os.path.isfile(real) or os.path.isfile(os.path.join(ROOT, child + ".py")):
            continue
        proxy = _VariantModule(child, real, variants)
        object.__setattr__(proxy, "__package__", "")
        existing = sys.modules.get(child)
        if existing is not None and not isinstance(existing, _VariantModule):
            object.__getattribute__(proxy, "_pv_loaded")["default"] = existing
        FLAT[child] = proxy
        sys.modules[child] = proxy
    if FLAT and not any(isinstance(f, _FlatFinder) for f in sys.meta_path):
        sys.meta_path.insert(0, _FlatFinder())


def install_modules() -> None:
    """Put a per-sport stand-in in sys.modules for every helper with copies."""
    if getattr(sys, "_pl_sport_variant_installed", False):
        return
    _scan()
    _install_flat()
    for name, variants in MODULES.items():
        real = os.path.join(ROOT, *name.split(".")) + ".py"
        if not os.path.isfile(real):
            continue
        existing = sys.modules.get(name)
        if isinstance(existing, _VariantModule):
            continue
        proxy = _VariantModule(name, real, variants)
        if existing is not None:
            object.__getattribute__(proxy, "_pv_loaded")["default"] = existing
        sys.modules[name] = proxy
        parent, _, child = name.rpartition(".")
        if parent:
            pkg = sys.modules.get(parent) or importlib.import_module(parent)
            setattr(pkg, child, proxy)
    sys._pl_sport_variant_installed = True


def install_app(app) -> None:
    """Per-sport templates and static files for one Flask app."""
    if getattr(app, "_pl_sport_variant_app", False):
        return
    app._pl_sport_variant_app = True
    if not MODULES and not TEMPLATES and not STATIC:
        _scan()

    if TEMPLATES:
        from jinja2 import ChoiceLoader, FileSystemLoader, PrefixLoader

        env = app.jinja_env
        env.loader = ChoiceLoader(
            [PrefixLoader({"__sport_variant__": FileSystemLoader(VARIANT_DIR)}), env.loader]
        )
        orig_get_template = env.get_template

        def get_template(name, parent=None, globals=None):
            if isinstance(name, str):
                sport = current_sport()
                if sport and sport in TEMPLATES.get(name, ()):
                    name = f"__sport_variant__/{sport}/templates/{name}"
            return orig_get_template(name, parent, globals)

        env.get_template = get_template

    if STATIC:
        from flask import request, send_from_directory

        @app.route("/sport-static/<sport>/<path:asset>")
        def _pl_sport_static(sport, asset):
            sport = (sport or "").lower()
            if sport not in STATIC.get(asset, ()):
                from flask import abort

                abort(404)
            return send_from_directory(os.path.join(VARIANT_DIR, sport, "static"), asset)

        pattern = re.compile(
            r"/static/(" + "|".join(re.escape(p) for p in sorted(STATIC, key=len, reverse=True)) + r")(?=[\"'?#\s])"
        )

        @app.after_request
        def _pl_sport_static_urls(response):
            try:
                sport = current_sport()
                if not sport or response.status_code != 200 or response.direct_passthrough:
                    return response
                if "html" not in (response.mimetype or ""):
                    return response
                html = response.get_data(as_text=True)

                def repl(m):
                    asset = m.group(1)
                    if sport in STATIC.get(asset, ()):
                        return f"/sport-static/{sport}/{asset}"
                    return m.group(0)

                out = pattern.sub(repl, html)
                if out != html:
                    response.set_data(out)
            except Exception:
                pass
            return response
