#!/usr/bin/env python3
"""Final Frame Prints auto-poster.

Posts a creative (image at a public URL + caption) to Instagram, Facebook,
Pinterest, and TikTok. Platforms with placeholder credentials are skipped.

Usage:
  python3 poster.py --image-url https://.../poster.jpg --caption "..." \
      [--platforms instagram,facebook] [--dry-run]
"""
import argparse
import json
import sys
import time
from pathlib import Path

import requests

GRAPH = "https://graph.facebook.com/v21.0"
CONFIG_PATH = Path(__file__).parent / "config.json"


def load_config():
    if not CONFIG_PATH.exists():
        sys.exit("config.json not found — copy config.example.json and fill in credentials.")
    return json.loads(CONFIG_PATH.read_text())


def _wait_for_container(container_id, token):
    for _ in range(20):
        s = requests.get(f"{GRAPH}/{container_id}", params={
            "fields": "status_code", "access_token": token}, timeout=30).json()
        if s.get("status_code") == "FINISHED":
            return None
        if s.get("status_code") == "ERROR":
            return f"container failed: {s}"
        time.sleep(3)
    return "timed out waiting for container"


def post_instagram(cfg, image_urls, caption, dry_run=False):
    """Publishes a single image OR a swipeable carousel (2+ images) to Instagram."""
    token = cfg["meta"]["system_user_token"]
    ig_id = cfg["meta"]["ig_business_account_id"]
    if "PASTE" in token or "PASTE" in ig_id:
        return ("instagram", "skipped", "credentials not set")
    if isinstance(image_urls, str):
        image_urls = [image_urls]
    if dry_run:
        return ("instagram", "dry-run", f"would post {len(image_urls)} image(s): {image_urls}")

    if len(image_urls) == 1:
        r = requests.post(f"{GRAPH}/{ig_id}/media", data={
            "image_url": image_urls[0],
            "caption": caption,
            "access_token": token,
        }, timeout=60)
        r.raise_for_status()
        container_id = r.json()["id"]
        err = _wait_for_container(container_id, token)
        if err:
            return ("instagram", "error", err)
        r = requests.post(f"{GRAPH}/{ig_id}/media_publish", data={
            "creation_id": container_id, "access_token": token}, timeout=60)
        r.raise_for_status()
        return ("instagram", "posted", r.json().get("id", ""))

    # Carousel: create each child item (no caption on children), then a parent container
    child_ids = []
    for url in image_urls:
        r = requests.post(f"{GRAPH}/{ig_id}/media", data={
            "image_url": url,
            "is_carousel_item": "true",
            "access_token": token,
        }, timeout=60)
        r.raise_for_status()
        child_id = r.json()["id"]
        err = _wait_for_container(child_id, token)
        if err:
            return ("instagram", "error", f"child {url}: {err}")
        child_ids.append(child_id)

    r = requests.post(f"{GRAPH}/{ig_id}/media", data={
        "media_type": "CAROUSEL",
        "children": ",".join(child_ids),
        "caption": caption,
        "access_token": token,
    }, timeout=60)
    r.raise_for_status()
    carousel_id = r.json()["id"]
    err = _wait_for_container(carousel_id, token)
    if err:
        return ("instagram", "error", err)

    r = requests.post(f"{GRAPH}/{ig_id}/media_publish", data={
        "creation_id": carousel_id, "access_token": token}, timeout=60)
    r.raise_for_status()
    return ("instagram", "posted (carousel)", r.json().get("id", ""))


def post_facebook(cfg, image_urls, caption, dry_run=False):
    """Posts a single photo, or a multi-photo album post, to the Facebook Page feed."""
    token = cfg["meta"]["system_user_token"]
    page_id = cfg["meta"]["page_id"]
    if "PASTE" in token or "PASTE" in page_id:
        return ("facebook", "skipped", "credentials not set")
    if isinstance(image_urls, str):
        image_urls = [image_urls]
    if dry_run:
        return ("facebook", "dry-run", f"would post {len(image_urls)} image(s): {image_urls}")

    # Exchange system-user token for the Page token
    r = requests.get(f"{GRAPH}/{page_id}", params={
        "fields": "access_token", "access_token": token}, timeout=30)
    r.raise_for_status()
    page_token = r.json()["access_token"]

    if len(image_urls) == 1:
        r = requests.post(f"{GRAPH}/{page_id}/photos", data={
            "url": image_urls[0], "message": caption, "access_token": page_token}, timeout=60)
        r.raise_for_status()
        return ("facebook", "posted", r.json().get("post_id", r.json().get("id", "")))

    # Multi-photo: upload each unpublished, then attach them all to one feed post
    media_fbids = []
    for url in image_urls:
        r = requests.post(f"{GRAPH}/{page_id}/photos", data={
            "url": url, "published": "false", "access_token": page_token}, timeout=60)
        r.raise_for_status()
        media_fbids.append(r.json()["id"])

    attached_media = json.dumps([{"media_fbid": mid} for mid in media_fbids])
    r = requests.post(f"{GRAPH}/{page_id}/feed", data={
        "message": caption,
        "attached_media": attached_media,
        "access_token": page_token,
    }, timeout=60)
    r.raise_for_status()
    return ("facebook", "posted (album)", r.json().get("id", ""))


def post_pinterest(cfg, image_urls, caption, dry_run=False):
    token = cfg["pinterest"]["access_token"]
    board = cfg["pinterest"]["board_id"]
    if isinstance(image_urls, str):
        image_urls = [image_urls]
    if "PENDING" in token or not board:
        return ("pinterest", "skipped", "awaiting trial-access approval")
    if dry_run:
        return ("pinterest", "dry-run", f"would pin {image_urls[0]}")

    r = requests.post("https://api.pinterest.com/v5/pins", json={
        "board_id": board,
        "title": caption[:100],
        "description": caption[:500],
        "media_source": {"source_type": "image_url", "url": image_urls[0]},
    }, headers={"Authorization": f"Bearer {token}"}, timeout=60)
    r.raise_for_status()
    return ("pinterest", "posted", r.json().get("id", ""))


def post_tiktok(cfg, image_urls, caption, dry_run=False):
    token = cfg["tiktok"]["access_token"]
    if isinstance(image_urls, str):
        image_urls = [image_urls]
    if "PENDING" in token:
        return ("tiktok", "skipped", "awaiting app audit")
    if dry_run:
        return ("tiktok", "dry-run", f"would post {len(image_urls)} image(s)")

    # Photo post via Content Posting API (PULL_FROM_URL) — supports multiple photos as a slideshow
    r = requests.post(
        "https://open.tiktokapis.com/v2/post/publish/content/init/",
        headers={"Authorization": f"Bearer {token}",
                 "Content-Type": "application/json"},
        json={
            "post_info": {"title": caption[:150], "privacy_level": "SELF_ONLY"},
            "source_info": {"source": "PULL_FROM_URL",
                            "photo_images": image_urls},
            "post_mode": "DIRECT_POST",
            "media_type": "PHOTO",
        }, timeout=60)
    r.raise_for_status()
    return ("tiktok", "posted (private until audit passes)",
            r.json().get("data", {}).get("publish_id", ""))


PLATFORMS = {
    "instagram": post_instagram,
    "facebook": post_facebook,
    "pinterest": post_pinterest,
    "tiktok": post_tiktok,
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--image-url", help="Single image URL")
    ap.add_argument("--image-urls", help="Comma-separated image URLs for a carousel/album/slideshow post")
    ap.add_argument("--caption", required=True)
    ap.add_argument("--platforms", default="instagram,facebook,pinterest,tiktok")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if args.image_urls:
        image_urls = [u.strip() for u in args.image_urls.split(",") if u.strip()]
    elif args.image_url:
        image_urls = [args.image_url]
    else:
        sys.exit("Provide either --image-url or --image-urls")

    cfg = load_config()
    results = []
    for name in [p.strip() for p in args.platforms.split(",")]:
        fn = PLATFORMS.get(name)
        if not fn:
            results.append((name, "error", "unknown platform"))
            continue
        try:
            results.append(fn(cfg, image_urls, args.caption, args.dry_run))
        except requests.HTTPError as e:
            results.append((name, "error", f"{e.response.status_code}: {e.response.text[:300]}"))
        except Exception as e:  # noqa: BLE001
            results.append((name, "error", str(e)))

    log_path = Path(__file__).parent / "post_log.jsonl"
    with log_path.open("a") as f:
        f.write(json.dumps({"ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                            "images": image_urls,
                            "caption": args.caption,
                            "results": results}) + "\n")

    for platform, status, detail in results:
        print(f"{platform:10s} {status:10s} {detail}")


if __name__ == "__main__":
    main()
