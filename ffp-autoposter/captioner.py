#!/usr/bin/env python3
"""Caption + hashtag engine for Final Frame Prints."""
import argparse
import base64
import json
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

HERE = Path(__file__).parent
CONFIG_PATH = HERE / "config.json"
LIBRARY_PATH = HERE / "hashtag_library.json"
POST_LOG = HERE / "post_log.jsonl"
GRAPH = "https://graph.facebook.com/v21.0"
GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta/models"

MAX_NEW_LOOKUPS_PER_DAY = 4
HASHTAG_COUNT = 15


def load_config():
    if not CONFIG_PATH.exists():
        sys.exit("config.json not found")
    return json.loads(CONFIG_PATH.read_text())


def load_library():
    if LIBRARY_PATH.exists():
        return json.loads(LIBRARY_PATH.read_text())
    return {"tags": {}, "lookups": []}


def save_library(lib):
    LIBRARY_PATH.write_text(json.dumps(lib, indent=2))


def lookups_used_this_week(lib):
    cutoff = datetime.now(timezone.utc) - timedelta(days=7)
    return [t for t in lib.get("lookups", [])
            if datetime.fromisoformat(t) > cutoff]


def gemini(cfg, parts, use_search=False, temperature=0.9):
    key = cfg["gemini"]["api_key"]
    model = cfg["gemini"].get("model", "gemini-flash-latest")
    body = {
        "contents": [{"parts": parts}],
        "generationConfig": {"temperature": temperature},
    }
    if use_search:
        body["tools"] = [{"google_search": {}}]

    r = requests.post(
        f"{GEMINI_BASE}/{model}:generateContent",
        headers={"x-goog-api-key": key, "Content-Type": "application/json"},
        json=body, timeout=120)
    r.raise_for_status()
    data = r.json()
    try:
        chunks = data["candidates"][0]["content"]["parts"]
        return "".join(c.get("text", "") for c in chunks).strip()
    except (KeyError, IndexError):
        raise RuntimeError(f"Unexpected Gemini response: {json.dumps(data)[:400]}")


def fetch_image_part(url):
    r = requests.get(url, timeout=60)
    r.raise_for_status()
    mime = r.headers.get("Content-Type", "image/jpeg").split(";")[0]
    return {"inline_data": {"mime_type": mime,
                            "data": base64.b64encode(r.content).decode()}}


def research_trending_hashtags(cfg):
    today = datetime.now(timezone.utc).strftime("%d %B %Y")
    prompt = f"""Today is {today}. Research what is CURRENTLY working on Instagram
right now for a UK business selling framed movie-quote posters and cinematic wall
art (Scarface, Creed, Snowfall, gym/hustle motivation, mancave and office decor).

Search for current, recent information - not general knowledge. Look for:
- hashtags actively used on high-performing wall art / film poster / home decor posts in the last few weeks
- any seasonal or trending angle right now that this niche could ride

Return ONLY a JSON array of 25 lowercase hashtag strings WITHOUT the # symbol,
ordered most promising first. Favour a mix: a few broad high-volume tags, mostly
mid-size niche tags where a small account can actually rank, and a few very
specific long-tail tags. No explanation, JSON array only."""

    try:
        raw = gemini(cfg, [{"text": prompt}], use_search=True, temperature=0.4)
    except Exception as e:
        print(f"[warn] trend research failed, falling back: {e}", file=sys.stderr)
        return []

    m = re.search(r"\[.*\]", raw, re.S)
    if not m:
        return []
    try:
        tags = json.loads(m.group(0))
    except json.JSONDecodeError:
        return []
    return [str(t).lstrip("#").strip().lower() for t in tags if str(t).strip()]


def validate_hashtags(cfg, candidates, lib):
    token = cfg["meta"]["system_user_token"]
    ig_id = cfg["meta"]["ig_business_account_id"]
    used = lookups_used_this_week(lib)
    budget = min(MAX_NEW_LOOKUPS_PER_DAY, max(0, 30 - len(used)))

    for tag in candidates:
        if budget <= 0:
            break
        if tag in lib["tags"]:
            continue
        try:
            r = requests.get(f"{GRAPH}/ig_hashtag_search", params={
                "user_id": ig_id, "q": tag, "access_token": token}, timeout=30)
            if r.status_code != 200:
                lib["tags"][tag] = {"valid": False, "checked": _now()}
                continue
            hid = r.json().get("data", [{}])[0].get("id")
            if not hid:
                lib["tags"][tag] = {"valid": False, "checked": _now()}
                continue

            media = requests.get(f"{GRAPH}/{hid}/top_media", params={
                "user_id": ig_id, "fields": "id,like_count,comments_count",
                "limit": 25, "access_token": token}, timeout=30).json()
            items = media.get("data", [])
            avg_likes = (sum(i.get("like_count", 0) for i in items) / len(items)
                         if items else 0)

            lib["tags"][tag] = {
                "valid": True,
                "hashtag_id": hid,
                "top_media_sampled": len(items),
                "avg_top_likes": round(avg_likes),
                "checked": _now(),
            }
            lib["lookups"].append(_now())
            budget -= 1
        except Exception as e:
            print(f"[warn] hashtag check failed for {tag}: {e}", file=sys.stderr)

    lib["lookups"] = lookups_used_this_week(lib)
    save_library(lib)
    return lib


def _now():
    return datetime.now(timezone.utc).isoformat()


def own_performance(cfg, limit=25):
    token = cfg["meta"]["system_user_token"]
    ig_id = cfg["meta"]["ig_business_account_id"]
    try:
        r = requests.get(f"{GRAPH}/{ig_id}/media", params={
            "fields": "id,caption,like_count,comments_count,timestamp",
            "limit": limit, "access_token": token}, timeout=60)
        r.raise_for_status()
        posts = r.json().get("data", [])
    except Exception as e:
        print(f"[warn] could not read own insights: {e}", file=sys.stderr)
        return []

    scored = []
    for p in posts:
        tags = re.findall(r"#(\w+)", p.get("caption") or "")
        scored.append({
            "engagement": p.get("like_count", 0) + 2 * p.get("comments_count", 0),
            "hashtags": [t.lower() for t in tags],
        })
    scored.sort(key=lambda x: x["engagement"], reverse=True)
    return scored


def winning_tags(perf, top_n=5):
    counts = {}
    for post in perf[:top_n]:
        for t in post["hashtags"]:
            counts[t] = counts.get(t, 0) + 1
    return [t for t, _ in sorted(counts.items(), key=lambda kv: -kv[1])]


def decide_cta_mode():
    n = 0
    if POST_LOG.exists():
        n = sum(1 for line in POST_LOG.read_text().splitlines() if line.strip())
    return "shop" if n % 2 == 0 else "engagement"


def write_caption(cfg, image_urls, hashtags, product=None, brief_block=""):
    shop = cfg.get("brand", {}).get("shop_url", "")
    mode = decide_cta_mode()

    cta_brief = (
        f"End with a clear call to action driving people to the shop: {shop}. "
        "Make wanting to own it feel urgent, but don't sound like an advert."
        if mode == "shop" else
        "End with a question or prompt that makes people comment, save or tag "
        "someone. Do NOT push the shop link this time - this post is for reach."
    )

    parts = []
    for url in image_urls[:3]:
        try:
            parts.append(fetch_image_part(url))
        except Exception as e:
            print(f"[warn] could not attach {url}: {e}", file=sys.stderr)

    product_line = f"\nThe product featured is: {product}." if product else ""

    parts.append({"text": f"""You are writing the Instagram caption for Final Frame Prints,
a brand selling framed cinematic movie-quote posters - Scarface, Creed, Snowfall,
that kind of energy. Gritty, aspirational, hustle-and-ambition territory. Buyers
are mostly men 18-40 kitting out a gym, office, mancave or first flat.{product_line}
{brief_block}
Look at the attached poster image(s) and write the caption.

Rules:
- Apply the research brief above - use the proven hook styles and structures,
  avoid everything on the AVOID list.
- Match the ENERGY of the poster itself. Cinematic, punchy, a bit of swagger.
  Short lines. Big first line - it has to stop the scroll in the first 3 words.
- Emotionally triggering: ambition, respect, owning your space, no-excuses grit.
- Never cheesy, never corporate, no "elevate your space" cliches.
- 40-70 words max. Line breaks between thoughts.
- {cta_brief}
- Do NOT include any hashtags - they get appended separately.
- Output ONLY the caption text, nothing else."""})

    caption = gemini(cfg, parts, temperature=1.0)
    tag_block = " ".join(f"#{t}" for t in hashtags)
    return f"{caption}\n\n{tag_block}", mode


def build(cfg, image_urls, product=None):
    lib = load_library()

    trending = research_trending_hashtags(cfg)
    lib = validate_hashtags(cfg, trending, lib)

    perf = own_performance(cfg)
    proven = winning_tags(perf)

    ordered, seen = [], set()
    for t in proven + trending:
        if t in seen:
            continue
        seen.add(t)
        info = lib["tags"].get(t)
        if info and not info.get("valid", True):
            continue
        ordered.append(t)

    hashtags = ordered[:HASHTAG_COUNT]

    brief, brief_block = {}, ""
    try:
        import swipefile
        brief = swipefile.build_brief(cfg)
        brief_block = swipefile.as_prompt_block(brief)
    except Exception as e:
        print(f"[warn] swipe brief unavailable: {e}", file=sys.stderr)

    caption, mode = write_caption(cfg, image_urls, hashtags, product, brief_block)
    return {"caption": caption, "hashtags": hashtags, "cta_mode": mode,
            "trend_candidates": len(trending),
            "lookups_used_this_week": len(lib.get("lookups", [])),
            "brief_captions_sampled": brief.get("captions_sampled", 0),
            "brief_posts_analysed": brief.get("posts_analysed", 0)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--image-urls", required=True)
    ap.add_argument("--product", default=None)
    args = ap.parse_args()

    cfg = load_config()
    urls = [u.strip() for u in args.image_urls.split(",") if u.strip()]
    print(json.dumps(build(cfg, urls, args.product), indent=2))


if __name__ == "__main__":
    main()
