"""Shared helpers for sport modules (lazy import of NHL77FINAL)."""
from __future__ import annotations


def main():
    import sys
    running = sys.modules.get('__main__')
    if running and hasattr(running, 'get_db_connection') and hasattr(running, 'app'):
        return running
    import NHL77FINAL as m
    return m


def register_shortcut(app, route: str, picks_slug: str, endpoint: str | None = None) -> None:
    from flask import redirect

    ep = endpoint or f'shortcut_{picks_slug.replace("-", "_")}'

    @app.route(route, endpoint=ep)
    def _shortcut():
        return redirect(f'/{picks_slug}', code=301)
