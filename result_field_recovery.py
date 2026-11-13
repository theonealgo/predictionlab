"""Response-only recovery of cached published result fields."""
import re
import sqlite3


def install(app):
    from flask import request

    @app.after_request
    def restore_result_fields(response):
        match = re.fullmatch(r"/(ncaab)-results/?", request.path)
        if not match or response.status_code != 200 or response.mimetype != "text/html":
            return response
        from auth_system import is_premium_user
        if not is_premium_user() or response.headers.get("Content-Encoding"):
            return response
        import NHL77FINAL as site
        from web_api import _cards
        from market_card_refresh import refresh as market_refresh
        from published_rating_fields import refresh as rating_refresh
        html = response.get_data(as_text=True)
        cards = _cards(html)
        try:
            payload = market_refresh({"cards": cards}, match[1].upper(), site.DATABASE,
                                     allow_final=True, fetch_missing=False)
            payload = rating_refresh(payload, match[1].upper(), site.DATABASE)
            for before, after in zip(cards, payload["cards"]):
                if before["html"] != after["html"]:
                    html = html.replace(before["html"], after["html"], 1)
            response.set_data(html)
        except (sqlite3.Error, ValueError, TypeError):
            app.logger.warning("Saved result field recovery unavailable")
        return response
