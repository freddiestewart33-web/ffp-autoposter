#!/usr/bin/env python3
"""Instagram Shopping product tagging.

Matches the Shopify product being posted to the equivalent item in your
Instagram Shop catalogue, so the post becomes shoppable — viewers tap the image
and go straight to the product.

Flow:
  1. /{ig-user-id}/available_catalogs      → which catalogue is connected
  2. /{ig-user-id}/catalog_product_search  → find the product ID by name
  3. product_tags on the media container   → attach it to the post

Everything degrades gracefully: if the catalogue isn't connected, the product
can't be matched, or the permission is missing, posting continues untagged
rather than failing.

Usage (diagnostic):
  python3 shoptags.py                 # show catalogue + all matchable products
  python3 shoptags.py "Nothing to lose"
"""
import json
import re
import sys
from pathlib import Path

import requests

HERE = Path(__file__).parent
CONFIG_PATH = HERE / "config.json"
CACHE_PATH = HERE / "shop_catalog_cache.json"
GRAPH = "https://graph.facebook.com/v21.0"

# Words that add noise when matching a Shopify title to a catalogue item
QUOTE_CHARS = "\"“”'‘’"
NOISE = re.compile(r"[" + QUOTE_CHARS + r"]|\b(poster|print|framed|wall art|the)\b",
                   re.I)


def load_config():
    return json.loads(CONFIG_PATH.read_text()) if CONFIG_PATH.exists() else {}


def normalise(s):
    return re.sub(r"\s+", " ", NOISE.sub(" ", s or "")).strip().lower()


def get_catalog_id(cfg):
    """The Instagram Shop catalogue connected to this account."""
    token = cfg["meta"]["system_user_token"]
    ig_id = cfg["meta"]["ig_business_account_id"]
    try:
        r = requests.get(f"{GRAPH}/{ig_id}/available_catalogs",
                         params={"access_token": token}, timeout=45)
        if r.status_code != 200:
            print(f"[warn] available_catalogs {r.status_code}: {r.text[:200]}",
                  file=sys.stderr)
            return None
        data = r.json().get("data", [])
        if not data:
            print("[warn] no Instagram Shop catalogue connected to this account",
                  file=sys.stderr)
            return None
        cat = data[0]
        print(f"[info] catalogue: {cat.get('name')} ({cat.get('catalog_id')})",
              file=sys.stderr)
        return cat.get("catalog_id")
    except Exception as e:  # noqa: BLE001
        print(f"[warn] catalogue lookup failed: {e}", file=sys.stderr)
        return None


def search_products(cfg, catalog_id, query=""):
    token = cfg["meta"]["system_user_token"]
    ig_id = cfg["meta"]["ig_business_account_id"]
    try:
        r = requests.get(f"{GRAPH}/{ig_id}/catalog_product_search", params={
            "catalog_id": catalog_id, "q": query,
            "access_token": token}, timeout=45)
        if r.status_code != 200:
            print(f"[warn] product_search {r.status_code}: {r.text[:200]}",
                  file=sys.stderr)
            return []
        return r.json().get("data", [])
    except Exception as e:  # noqa: BLE001
        print(f"[warn] product search failed: {e}", file=sys.stderr)
        return []


def find_product_id(cfg, product_title):
    """Best-effort match from a Shopify title to an IG Shop product ID."""
    catalog_id = get_catalog_id(cfg)
    if not catalog_id:
        return None

    target = normalise(product_title)

    # Try the distinctive part of the title first (the quote in quotation marks)
    quoted = re.findall(r"[" + QUOTE_CHARS + r"]([^" + QUOTE_CHARS + r"]{4,})["
                        + QUOTE_CHARS + r"]", product_title or "")
    queries = [q.strip() for q in quoted] + [product_title, ""]

    seen = {}
    for q in queries:
        for item in search_products(cfg, catalog_id, q):
            pid = item.get("product_id") or item.get("id")
            name = item.get("name") or item.get("title") or ""
            if pid:
                seen[pid] = name

        # exact-ish match on this pass?
        for pid, name in seen.items():
            if normalise(name) == target:
                print(f"[info] exact catalogue match: {name} ({pid})", file=sys.stderr)
                return pid

    if not seen:
        print(f"[warn] no catalogue products found for '{product_title}'",
              file=sys.stderr)
        return None

    # Show what the catalogue actually contains — essential for debugging
    print(f"[info] catalogue returned {len(seen)} candidate(s):", file=sys.stderr)
    for pid, name in list(seen.items())[:15]:
        print(f"         {pid}  {name!r}", file=sys.stderr)

    # Substring match either direction (handles "Scarface Poster — All I Have…")
    for pid, name in seen.items():
        n = normalise(name)
        if not n:
            continue
        if n in target or target in n:
            print(f"[info] substring catalogue match: {name} ({pid})",
                  file=sys.stderr)
            return pid

    # Match on the quoted phrase alone
    for phrase in (normalise(q) for q in quoted):
        if not phrase:
            continue
        for pid, name in seen.items():
            if phrase in normalise(name):
                print(f"[info] quote-phrase match: {name} ({pid})", file=sys.stderr)
                return pid

    # Fall back to best token overlap
    tset = set(target.split())
    best, score = None, 0
    for pid, name in seen.items():
        overlap = len(tset & set(normalise(name).split()))
        if overlap > score:
            best, score = pid, overlap
    if best and score >= 2:
        print(f"[info] fuzzy catalogue match: {seen[best]} ({best}, score {score})",
              file=sys.stderr)
        return best

    print(f"[warn] couldn't confidently match '{product_title}' "
          f"(best overlap {score}); posting untagged", file=sys.stderr)
    return None


def tags_payload(product_id):
    """product_tags value for a media container. Empty if no product."""
    if not product_id:
        return None
    return json.dumps([{"product_id": str(product_id)}])


def main():
    cfg = load_config()
    query = sys.argv[1] if len(sys.argv) > 1 else ""
    catalog_id = get_catalog_id(cfg)
    if not catalog_id:
        sys.exit("No catalogue available — check Instagram Shop is set up and "
                 "the token has instagram_shopping_tag_products + catalog_management.")
    items = search_products(cfg, catalog_id, query)
    print(f"{len(items)} catalogue products"
          f"{f' matching {query!r}' if query else ''}:\n")
    for i in items:
        print(f"  {i.get('product_id') or i.get('id')}  {i.get('name') or i.get('title')}")
    if query:
        print(f"\nBest match → {find_product_id(cfg, query)}")


if __name__ == "__main__":
    main()
