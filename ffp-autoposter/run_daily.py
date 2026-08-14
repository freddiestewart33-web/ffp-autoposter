#!/usr/bin/env python3
"""Daily orchestrator — the whole pipeline in one run.

  1. Pull the live Shopify catalogue
  2. Pick the product that's gone longest without being posted
  3. Write a caption from the REAL product data (title, character, collection,
     official description, price, product URL) — no guessing from the image
  4. Post its mockup images as a carousel to every connected platform
  5. Log what happened, including which product, so rotation works next time

Usage:
  python3 run_daily.py                     # full auto: pick, write, post
  python3 run_daily.py --dry-run           # do everything except publish
  python3 run_daily.py --handle creed-poster   # force a specific product
  python3 run_daily.py --max-images 3      # cap carousel size (default 3)
"""
import argparse
import json
import sys
import time
from pathlib import Path

import catalogue
import captioner
import poster

HERE = Path(__file__).parent
POST_LOG = HERE / "post_log.jsonl"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--handle", default=None,
                    help="Force a specific product by its Shopify handle")
    ap.add_argument("--max-images", type=int, default=3)
    ap.add_argument("--platforms", default="instagram,facebook,pinterest,tiktok")
    args = ap.parse_args()

    cfg = captioner.load_config()

    # 1-2. Catalogue and selection ------------------------------------------
    products = catalogue.fetch_catalogue(cfg)
    if not products:
        sys.exit("No products found in catalogue — is the shop URL correct?")
    print(f"[info] {len(products)} products in live catalogue")

    if args.handle:
        product = next((p for p in products if p["handle"] == args.handle), None)
        if not product:
            sys.exit(f"No product with handle '{args.handle}'")
    else:
        product = catalogue.next_product(products)

    image_urls = product["images"][:args.max_images]
    print(f"[info] posting: {product['title']} ({len(image_urls)} images)")
    print(f"[info] product page: {product['url']}")

    # 3. Caption ------------------------------------------------------------
    product_brief = catalogue.as_product_brief(product)
    result = captioner.build(cfg, image_urls, product=product_brief)
    caption = result["caption"]

    print("\n--- CAPTION ---")
    print(caption)
    print("--- END ---\n")
    print(f"[info] CTA mode: {result.get('cta_mode')}, "
          f"{len(result.get('hashtags', []))} hashtags, "
          f"{result.get('brief_posts_analysed', 0)} posts analysed")

    # 4. Publish ------------------------------------------------------------
    results = []
    for name in [p.strip() for p in args.platforms.split(",")]:
        fn = poster.PLATFORMS.get(name)
        if not fn:
            continue
        try:
            results.append(fn(cfg, image_urls, caption, args.dry_run))
        except Exception as e:  # noqa: BLE001
            results.append((name, "error", str(e)))

    for platform, status, detail in results:
        print(f"{platform:10s} {status:12s} {detail}")

    # 5. Log ----------------------------------------------------------------
    posted_ok = any(s.startswith("posted") for _, s, _ in results)
    if posted_ok and not args.dry_run:
        with POST_LOG.open("a") as f:
            f.write(json.dumps({
                "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "product_handle": product["handle"],
                "product_title": product["title"],
                "product_url": product["url"],
                "images": image_urls,
                "caption": caption,
                "hashtags": result.get("hashtags", []),
                "cta_mode": result.get("cta_mode"),
                "results": results,
            }) + "\n")
        print("[info] logged — this product moves to the back of the rotation")
    elif args.dry_run:
        print("[info] dry run, nothing logged or published")
    else:
        print("[warn] nothing published, so not logged "
              "(product stays next in rotation)")
        sys.exit(1)


if __name__ == "__main__":
    main()
