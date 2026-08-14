#!/usr/bin/env python3
"""Live product catalogue + posting queue for Final Frame Prints.

Pulls the real catalogue straight from the Shopify storefront every run, so the
posting rotation always reflects what's actually for sale. Add a product to the
store and it enters the rotation automatically — nothing to maintain by hand.

Rotation logic: whichever product has gone longest without being posted goes
next, read from post_log.jsonl. Deterministic, no repeats until the whole
catalogue has cycled.

Usage:
  python3 catalogue.py              # show the full catalogue
  python3 catalogue.py --next       # show the product due to post next
"""
import json
import re
import sys
from html import unescape
from pathlib import Path

import requests

HERE = Path(__file__).parent
CONFIG_PATH = HERE / "config.json"
POST_LOG = HERE / "post_log.jsonl"

# Shared/utility images we never want in a post (size charts etc.)
IMAGE_EXCLUDE = re.compile(r"size[_\-]?chart|sizechart", re.I)
MIN_IMAGE_PX = 900


def load_config():
    return json.loads(CONFIG_PATH.read_text()) if CONFIG_PATH.exists() else {}


def strip_html(html):
    text = re.sub(r"<[^>]+>", " ", html or "")
    return re.sub(r"\s+", " ", unescape(text)).strip()


def collection_of(title, body):
    for name in ("Power Collection", "Discipline Collection", "Ambition Collection"):
        if name.lower() in (body or "").lower():
            return name
    t = (title or "").lower()
    if "scarface" in t:
        return "Power Collection"
    if "creed" in t:
        return "Discipline Collection"
    if "snowfall" in t:
        return "Ambition Collection"
    return ""


def character_of(title, body):
    b = (body or "").lower()
    for name in ("Tony Montana", "Adonis Creed", "Franklin Saint", "Rocky"):
        if name.lower() in b:
            return name
    t = (title or "").lower()
    return {"scarface": "Tony Montana", "creed": "Adonis Creed",
            "snowfall": "Franklin Saint"}.get(
        next((k for k in ("scarface", "creed", "snowfall") if k in t), ""), "")


def fetch_catalogue(cfg=None):
    cfg = cfg or load_config()
    shop = (cfg.get("brand", {}) or {}).get("shop_url", "https://finalframeprints.com")
    shop = shop.rstrip("/")

    r = requests.get(f"{shop}/products.json", params={"limit": 250}, timeout=60)
    r.raise_for_status()

    products = []
    for p in r.json().get("products", []):
        body = strip_html(p.get("body_html"))
        images = [
            img["src"] for img in p.get("images", [])
            if not IMAGE_EXCLUDE.search(img.get("src", ""))
            and (img.get("width") or 0) >= MIN_IMAGE_PX
        ]
        if not images:
            continue

        variants = p.get("variants", [])
        prices = sorted({v.get("price") for v in variants if v.get("price")})
        was = sorted({v.get("compare_at_price") for v in variants
                      if v.get("compare_at_price")})

        products.append({
            "id": p["id"],
            "title": p["title"].strip(),
            "handle": p["handle"],
            "url": f"{shop}/products/{p['handle']}",
            "description": body,
            "collection": collection_of(p["title"], body),
            "character": character_of(p["title"], body),
            "images": images,
            "price_from": prices[0] if prices else None,
            "compare_at": was[0] if was else None,
            "sizes": [v.get("title") for v in variants],
        })
    return products


def posted_history():
    """Product handles in the order they were last posted (most recent last)."""
    if not POST_LOG.exists():
        return []
    seen = []
    for line in POST_LOG.read_text().splitlines():
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        handle = entry.get("product_handle")
        if handle:
            if handle in seen:
                seen.remove(handle)
            seen.append(handle)
    return seen


def next_product(products=None):
    """Least-recently-posted product. Never-posted items come first."""
    products = products or fetch_catalogue()
    if not products:
        return None
    history = posted_history()

    def rank(p):
        try:
            return history.index(p["handle"])
        except ValueError:
            return -1  # never posted → highest priority
    return sorted(products, key=rank)[0]


def as_product_brief(p):
    """Human-readable context block for the caption writer."""
    if not p:
        return ""
    bits = [f"Product: {p['title']}"]
    if p.get("character"):
        bits.append(f"Character: {p['character']}")
    if p.get("collection"):
        bits.append(f"Collection: {p['collection']}")
    if p.get("price_from"):
        was = f" (was £{p['compare_at']})" if p.get("compare_at") else ""
        bits.append(f"Price: from £{p['price_from']}{was}")
    bits.append(f"Product page: {p['url']}")
    if p.get("description"):
        bits.append(f"Official description: {p['description'][:600]}")
    return "\n".join(bits)


def main():
    products = fetch_catalogue()
    if "--next" in sys.argv:
        p = next_product(products)
        print(json.dumps(p, indent=2))
        return
    print(f"{len(products)} products in catalogue\n")
    history = posted_history()
    for p in products:
        posted = "never posted" if p["handle"] not in history else \
            f"last posted #{len(history) - history.index(p['handle'])} ago"
        print(f"  {p['title']:<50} {len(p['images'])} imgs  £{p['price_from']}  {posted}")


if __name__ == "__main__":
    main()
