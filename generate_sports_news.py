"""Generate review drafts; --publish-local is only for the working-copy review site."""
import argparse
import os
from pathlib import Path
from sports_news import generate, save_articles


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--date")
    parser.add_argument("--publish-local", action="store_true")
    args = parser.parse_args()
    if args.publish_local and (os.getenv("RENDER", "").lower() in ("true", "1") or Path("/data").is_dir()):
        parser.error("Local review publication is not available in the hosted environment.")
    posts = generate(args.date)
    save_articles(None, posts, publish_local=args.publish_local)
    for post in posts:
        print(post["title"], post["canonical_path"])


if __name__ == "__main__":
    main()
