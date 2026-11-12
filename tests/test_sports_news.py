from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import unittest
from sports_news import final_facts, build_article, save_articles, published_posts, validate_article

HERE = Path(__file__).resolve().parent
NOW = datetime(2026, 11, 11, 23, 30, tzinfo=timezone.utc)


def fixture():
    return {"events": [{"id": "123", "date": "2026-11-11T01:00:00Z",
        "status": {"type": {"completed": True, "name": "STATUS_FINAL", "detail": "Final"}},
        "competitions": [{"date": "2026-11-11T01:00:00Z", "competitors": [
            {"homeAway": "home", "team": {"displayName": "Home Team"}, "score": "3"},
            {"homeAway": "away", "team": {"displayName": "Away Team"}, "score": "0"}]}]}]}


class NewsTests(unittest.TestCase):
    def article(self):
        return build_article("NHL", final_facts("NHL", fixture(), "2026-11-10", NOW), NOW)

    def test_only_verified_finals_and_zero_scores(self):
        facts = final_facts("NHL", fixture(), "2026-11-10", NOW)
        self.assertEqual(facts[0]["away_score"], 0)
        altered = fixture()
        altered["events"][0]["status"]["type"]["completed"] = False
        self.assertEqual(final_facts("NHL", altered, "2026-11-10", NOW), [])
        self.assertEqual(final_facts("NHL", fixture(), "2026-11-11", NOW), [])

    def test_plain_original_news_has_no_model_or_gambling_sections(self):
        post = self.article()
        self.assertTrue(validate_article(post))
        self.assertIn("3–0", " ".join(post["body"]))
        self.assertEqual(post["sections"]["markets"], {})
        self.assertEqual(post["sections"]["models"], [])
        self.assertEqual(post["image"], "/static/pl-logo.svg")
        self.assertFalse(post["social_drafts"]["ready_for_delivery"])

    def test_drafts_local_review_and_idempotence(self):
        with tempfile.TemporaryDirectory(dir=HERE) as tmp:
            path = Path(tmp)/"posts.json"
            post = self.article()
            save_articles(path, [post])
            self.assertEqual(published_posts(path), [])
            save_articles(path, [post], publish_local=True)
            self.assertEqual(len(published_posts(path)), 1)
            save_articles(path, [post], publish_local=True)
            self.assertEqual(len(json.loads(path.read_text())["posts"]), 1)

    def test_daily_cap_and_old_posts_are_preserved(self):
        with tempfile.TemporaryDirectory(dir=HERE) as tmp:
            path = Path(tmp)/"posts.json"
            old = {"slug": "older", "date": "2026-11-09", "title": "Existing article", "status": "published"}
            path.write_text(json.dumps({"posts": [old]}))
            for n in range(5):
                post = self.article()
                post["slug"] += str(n)
                post["canonical_path"] = f"/blog/nhl/{post['slug']}"
                save_articles(path, [post])
            values = json.loads(path.read_text())["posts"]
            self.assertEqual(len(values), 4)
            self.assertEqual(values[0], old)

    def test_tampered_facts_gambling_and_external_source_rejected(self):
        for field in ("facts", "body", "link"):
            post = self.article()
            if field == "facts": post["provenance"]["facts"][0]["home_score"] = 99
            elif field == "body": post["body"].append("A betting recommendation.")
            else: post["news_items"][0]["url"] = "javascript:alert(1)"
            with self.assertRaises(ValueError):
                validate_article(post)


if __name__ == "__main__":
    unittest.main()
