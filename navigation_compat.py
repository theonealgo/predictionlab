"""Compatibility for legacy navigation URLs; no results or grading work."""
from urllib.parse import urlencode


def canonical_result_link(path, args, method="GET"):
    target_path = path.rstrip("/")
    if method not in ("GET", "HEAD") or target_path not in ("/mlb-results", "/ncaaw-results", "/ncaab-results"):
        return None
    if args.get("view") or str(args.get("market", "")).lower() not in ("spread", "totals", "moneyline"):
        return None
    pairs = [(key, value) for key, value in args.items(multi=True) if key != "view"]
    pairs.append(("view", "chart"))
    return target_path + "?" + urlencode(pairs)


def install(app):
    from flask import redirect, request

    @app.before_request
    def legacy_result_navigation():
        target = canonical_result_link(request.path, request.args, request.method)
        if target:
            return redirect(target, code=301)
