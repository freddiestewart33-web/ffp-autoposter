#!/usr/bin/env python3
"""Daily orchestrator — the whole pipeline in one run.

Content comes from one of two places, in this order:

  1. THE QUEUE (`content-queue/`) — Freddie's own hand-made carousel sets.
     Always preferred. Posted sets move to `content-queue/posted/` so they
     can never repeat.
  2. CATALOGUE ROTATION — if the queue is empty, fall back to the live
     Shopify product images, picking whatever has gone longest unposted.

Either way the caption, hashtags, price, link and Instagram Shop product tag
are all derived from the REAL product record, not guessed from the image.

Usage:
  python3 run_daily.py                     # full auto
  python3 run_daily.py --dry-run           # everything except publish
  python3 run_daily.py --handle creed-poster   # force a product
  python3 run_daily.py --no-queue          # ignore the queue, use catalogue
"""
import argparse
import json
import sys
import time
from pathlib import Path

import catalogue
import captioner
import poster
import queue as content_queue

HERE = Path(__file__).parent
POST_LOG = HERE / "post_log.jsonl"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--handle", default=None,
                    help="Force a specific product by its Shopify handle")
    ap.add_argument("--max-images", type=int, default=3)
    ap.add_argument("--platforms", default="instagram,facebook,pinterest,tiktok")
    ap.add_argument("--no-tag", action="store_true",
                    help="Skip Instagram Shop product tagging")
    ap.add_argument("--no-queue", action="store_true",
                    help="Ignore content-queue, use catalogue images")
    args = ap.parse_args()

    cfg = captioner.load_config()

    products = catalogue.fetch_catalogue(cfg)
    if not products:
        sys.exit("No products found in catalogue — is the shop URL correct?")
    print(f"[info] {len(products)} products in live catalogue")

    # ---- 1. Decide what we're posting -------------------------------------
    queued = None if (args.no_queue or args.handle) else content_queue.next_set()
    queue_paths = []

    if queued:
        set_name, queue_paths = queued
        handle = content_queue.product_handle(set_name)
        product = next((p for p in products if p["handle"] == handle), None)
        if not product:
            print(f"[warn] queue set '{set_name}' maps to handle '{handle}' "
                  f"which isn't in the catalogue — falling back to rotation",
                  file=sys.stderr)
            queued = None
            queue_paths = []
        else:
            image_urls = content_queue.raw_urls(queue_paths)
            source = f"queue:{set_name}"
            print(f"[info] QUEUE set '{set_name}' -> {product['title']}")

    if not queued:
        if args.handle:
            product = next((p for p in products
                            if p["handle"] == args.handle), None)
            if not product:
                sys.exit(f"No product with handle '{args.handle}'")
        else:
            product = catalogue.next_product(products)
        image_urls = product["images"][:args.max_images]
        source = "catalogue"
        print("[info] queue empty — falling back to catalogue rotation")

    print(f"[info] posting: {product['title']} ({len(image_urls)} images)")
    print(f"[info] product page: {product['url']}")
    for u in image_urls:
        print(f"[info]   slide: {u}")

    # ---- 2. Caption -------------------------------------------------------
    product_brief = catalogue.as_product_brief(product)
    result = captioner.build(cfg, image_urls, product=product_brief)
    caption = result["caption"]

    print("\n--- CAPTION ---")
    print(caption)
    print("--- END ---\n")
    print(f"[info] CTA mode: {result.get('cta_mode')}, "
          f"{len(result.get('hashtags', []))} hashtags, "
          f"{result.get('brief_posts_analysed', 0)} posts analysed")

    # ---- 3. Instagram Shop product tag ------------------------------------
    product_tags = None
    if not args.no_tag:
        try:
            import shoptags
            pid = shoptags.find_product_id(cfg, product["title"])
            if pid:
                product_tags = shoptags.tags_payload(pid)
                print(f"[info] tagging Instagram Shop product {pid}")
            else:
                print(f"[warn] NO PRODUCT TAG: '{product['title']}' was not "
                      f"found in the Instagram catalogue. The post will still "
                      f"go out, just untagged. Check the product is approved "
                      f"and live in Commerce Manager.", file=sys.stderr)
        except Exception as e:  # noqa: BLE001
            print(f"[warn] product tagging unavailable: {e}", file=sys.stderr)

    # ---- 4. Publish -------------------------------------------------------
    results = []
    for name in [p.strip() for p in args.platforms.split(",")]:
        fn = poster.PLATFORMS.get(name)
        if not fn:
            continue
        try:
            if name == "instagram":
                results.append(fn(cfg, image_urls, caption, args.dry_run,
                                  product_tags=product_tags))
            else:
                results.append(fn(cfg, image_urls, caption, args.dry_run))
        except Exception as e:  # noqa: BLE001
            results.append((name, "error", str(e)))

    for platform, status, detail in results:
        print(f"{platform:10s} {status:12s} {detail}")

    # ---- 5. Log and retire the queue set ----------------------------------
    posted_ok = any(s.startswith("posted") for _, s, _ in results)
    if posted_ok and not args.dry_run:
        if queue_paths:
            moved = content_queue.mark_posted(queue_paths)
            print(f"[info] retired {len(moved)} queue file(s) to posted/")

        with POST_LOG.open("a") as f:
            f.write(json.dumps({
                "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "source": source,
                "product_handle": product["handle"],
                "product_title": product["title"],
                "product_url": product["url"],
                "product_tagged": bool(product_tags),
                "images": image_urls,
                "caption": caption,
                "hashtags": result.get("hashtags", []),
                "cta_mode": result.get("cta_mode"),
                "results": results,
            }) + "\n")
        print("[info] logged")
    elif args.dry_run:
        print("[info] dry run — nothing logged, published, or retired")
    else:
        print("[warn] nothing published, so not logged "
              "(queue set stays for next run)")
        sys.exit(1)


if __name__ == "__main__":
    main()
