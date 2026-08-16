#!/usr/bin/env python3
"""Caption + hashtag engine for Final Frame Prints.

Three layers feed the hashtag set:
  1. LIVE TREND RESEARCH - Gemini with Google Search grounding looks up what's
     currently working in the wall-art / movie-print niche. Runs daily, so it
     reflects today rather than stale training data.
  2. VOLUME VALIDATION - Instagram's /ig_hashtag_search confirms a tag is real
     and queryable. HARD LIMIT: 30 unique hashtags per rolling 7 days per
     account, so we check at most `max_new_lookups_per_day` new ones and cache
     every result forever in hashtag_library.json.
  3. OWN PERFORMANCE - pulls insights from your recent posts so tags/styles that
     actually earned reach and saves get reused.

The caption itself is written by Gemini looking at the real poster image,
matching its energy, alternating CTA between shop-link and engagement.

Usage:
  python3 captioner.py --image-urls "url1,url2" [--product "Scarface print"]
  # prints JSON: {"caption": "...", "hashtags": [...], "cta_mode": "..."}
"""
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

# Stay comfortably under Instagram's 30-unique-per-7-days ceiling.
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


# --------------------------------------------------------------------------
# Gemini helpers
# --------------------------------------------------------------------------

RETRYABLE = {429, 500, 502, 503, 504}


def gemini(cfg, parts, use_search=False, temperature=0.9, attempts=5):
    """Single-turn Gemini call, with backoff for rate limits and outages.

    Free-tier quotas (429) and transient 503s are common, so every call retries
    with exponential backoff before giving up. Falls back to a secondary model
    if the primary one is exhausted.
    """
    key = cfg["gemini"]["api_key"]
    models = [cfg["gemini"].get("model", "gemini-flash-latest")]
    for fallback in ("gemini-2.5-flash", "gemini-flash-lite-latest"):
        if fallback not in models:
            models.append(fallback)

    body = {
        "contents": [{"parts": parts}],
        "generationConfig": {"temperature": temperature},
    }
    if use_search:
        body["tools"] = [{"google_search": {}}]

    last_err = None
    for model in models:
        delay = 5
        for attempt in range(attempts):
            try:
                r = requests.post(
                    f"{GEMINI_BASE}/{model}:generateContent",
                    headers={"x-goog-api-key": key,
                             "Content-Type": "application/json"},
                    json=body, timeout=120)
                if r.status_code in RETRYABLE:
                    last_err = f"{r.status_code} on {model}"
                    if attempt < attempts - 1:
                        wait = delay
                        # Honour Google's own retry hint when present
                        try:
                            for d in r.json().get("error", {}).get("details", []):
                                if "retryDelay" in str(d):
                                    wait = max(wait, int(
                                        re.sub(r"\D", "", str(d.get("retryDelay", "")))
                                        or delay))
                        except Exception:  # noqa: BLE001
                            pass
                        print(f"[retry] {last_err}, waiting {wait}s "
                              f"(attempt {attempt + 1}/{attempts})", file=sys.stderr)
                        time.sleep(min(wait, 60))
                        delay = min(delay * 2, 60)
                        continue
                    break
                r.raise_for_status()
                data = r.json()
                chunks = data["candidates"][0]["content"]["parts"]
                text = "".join(c.get("text", "") for c in chunks).strip()
                if text:
                    return text
                last_err = f"empty response from {model}"
                break
            except requests.HTTPError as e:
                last_err = f"{e.response.status_code} on {model}: {e.response.text[:200]}"
                break
            except (KeyError, IndexError) as e:
                last_err = f"unparseable response from {model}: {e}"
                break
        print(f"[warn] {model} failed ({last_err}); trying next model",
              file=sys.stderr)

    raise RuntimeError(f"All Gemini models failed. Last error: {last_err}")


def fetch_image_part(url):
    """Download an image and return it as an inline Gemini part."""
    r = requests.get(url, timeout=60)
    r.raise_for_status()
    mime = r.headers.get("Content-Type", "image/jpeg").split(";")[0]
    return {"inline_data": {"mime_type": mime,
                            "data": base64.b64encode(r.content).decode()}}


# --------------------------------------------------------------------------
# Layer 1 - live trend research
# --------------------------------------------------------------------------

def research_trending_hashtags(cfg):
    # Cache for a day — no point burning free-tier quota re-researching per post.
    lib = load_library()
    cached = lib.get("trend_cache")
    if cached:
        age = datetime.now(timezone.utc) - datetime.fromisoformat(cached["at"])
        if age < timedelta(hours=20):
            print(f"[info] using cached trend research ({len(cached['tags'])} tags)",
                  file=sys.stderr)
            return cached["tags"]

    today = datetime.now(timezone.utc).strftime("%d %B %Y")
    prompt = f"""Today is {today}. Research what is CURRENTLY working on Instagram
right now for a UK business selling framed movie-quote posters and cinematic wall
art (Scarface, Creed, Snowfall, gym/hustle motivation, mancave and office decor).

Search for current, recent information — not general knowledge. Look for:
- hashtags actively used on high-performing wall art / film poster / home decor posts in the last few weeks
- any seasonal or trending angle right now that this niche could ride

Return ONLY a JSON array of 25 lowercase hashtag strings WITHOUT the # symbol,
ordered most promising first. Favour a mix: a few broad high-volume tags, mostly
mid-size niche tags where a small account can actually rank, and a few very
specific long-tail tags. No explanation, JSON array only."""

    try:
        raw = gemini(cfg, [{"text": prompt}], use_search=True, temperature=0.4)
    except Exception as e:  # noqa: BLE001
        print(f"[warn] trend research failed, falling back: {e}", file=sys.stderr)
        return []

    m = re.search(r"\[.*\]", raw, re.S)
    if not m:
        return []
    try:
        tags = json.loads(m.group(0))
    except json.JSONDecodeError:
        return []
    clean = [str(t).lstrip("#").strip().lower() for t in tags if str(t).strip()]

    if clean:
        lib["trend_cache"] = {"at": _now(), "tags": clean}
        save_library(lib)
    return clean


# --------------------------------------------------------------------------
# Layer 2 - Instagram volume validation (rate-limit aware)
# --------------------------------------------------------------------------

def validate_hashtags(cfg, candidates, lib):
    """Confirm hashtags resolve on Instagram, respecting the 30/7day ceiling.

    Note: Meta does not expose a raw post-count on the hashtag node, so what we
    record is (a) that the tag is real and queryable, and (b) how much recent
    top-media activity it shows — a rough proxy for how alive the tag is.
    """
    token = cfg["meta"]["system_user_token"]
    ig_id = cfg["meta"]["ig_business_account_id"]
    used = lookups_used_this_week(lib)
    budget = min(MAX_NEW_LOOKUPS_PER_DAY, max(0, 30 - len(used)))

    for tag in candidates:
        if budget <= 0:
            break
        if tag in lib["tags"]:
            continue  # already known, costs nothing
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
        except Exception as e:  # noqa: BLE001
            print(f"[warn] hashtag check failed for {tag}: {e}", file=sys.stderr)

    lib["lookups"] = lookups_used_this_week(lib)
    save_library(lib)
    return lib


def _now():
    return datetime.now(timezone.utc).isoformat()


# --------------------------------------------------------------------------
# Layer 3 - what actually worked on your own account
# --------------------------------------------------------------------------

def own_performance(cfg, limit=25):
    """Recent posts ranked by engagement, with the hashtags each one used.

    Field availability varies by account type and granted permissions, so we
    degrade from richest to simplest rather than failing the whole run.
    """
    token = cfg["meta"]["system_user_token"]
    ig_id = cfg["meta"]["ig_business_account_id"]
    posts = []
    for fields in ("id,caption,like_count,comments_count,timestamp",
                   "id,caption,comments_count,timestamp",
                   "id,caption,timestamp",
                   "id,caption"):
        try:
            r = requests.get(f"{GRAPH}/{ig_id}/media", params={
                "fields": fields, "limit": limit, "access_token": token},
                timeout=60)
            if r.status_code != 200:
                continue
            posts = r.json().get("data", [])
            break
        except Exception as e:  # noqa: BLE001
            print(f"[warn] insights attempt failed: {e}", file=sys.stderr)
    if not posts:
        print("[warn] no insights available — continuing without them",
              file=sys.stderr)
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


# --------------------------------------------------------------------------
# Caption generation
# --------------------------------------------------------------------------

def decide_cta_mode():
    """Alternate between driving clicks and driving engagement."""
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
        "someone. Do NOT push the shop link this time — this post is for reach."
    )

    parts = []
    for url in image_urls[:3]:
        try:
            parts.append(fetch_image_part(url))
        except Exception as e:  # noqa: BLE001
            print(f"[warn] could not attach {url}: {e}", file=sys.stderr)

    product_line = f"\nThe product featured is: {product}." if product else ""

    parts.append({"text": f"""You are writing the Instagram caption for Final Frame Prints,
a brand selling framed cinematic movie-quote posters — Scarface, Creed, Snowfall,
that kind of energy. Gritty, aspirational, hustle-and-ambition territory. Buyers
are mostly men 18-40 kitting out a gym, office, mancave or first flat.{product_line}
{brief_block}
Look at the attached poster image(s) and write the caption.

Rules:
- Apply the research brief above — use the proven hook styles and structures,
  avoid everything on the AVOID list.
- Match the ENERGY of the poster itself. Cinematic, punchy, a bit of swagger.
  Short lines. Big first line — it has to stop the scroll in the first 3 words.
- Emotionally triggering: ambition, respect, owning your space, no-excuses grit.
- Never cheesy, never corporate, no "elevate your space" cliches.
- 40-70 words max. Line breaks between thoughts.
- {cta_brief}
- Do NOT include any hashtags — they get appended separately.
- Output ONLY the caption text, nothing else."""})

    caption = gemini(cfg, parts, temperature=1.0)
    tag_block = " ".join(f"#{t}" for t in hashtags)
    return f"{caption}\n\n{tag_block}", mode


# --------------------------------------------------------------------------

def product_hashtags(cfg, product):
    """Hashtags derived from THIS product. No web search needed, so it works
    even when grounded calls are rate-limited."""
    if not product:
        return []
    prompt = f"""Here is the product being posted:

{product}

Generate Instagram hashtags for THIS SPECIFIC product. Rules:
- Must be relevant to this exact film/character/collection — never tag a
  different film's characters.
- Mix: 3-4 broad wall-art/decor tags, 6-8 mid-size niche tags where a small
  account can actually rank, 3-4 very specific long-tail tags for this film.
- All lowercase, no # symbol, no spaces.

Return ONLY a JSON array of 15 strings. No explanation."""
    try:
        raw = gemini(cfg, [{"text": prompt}], use_search=False, temperature=0.7)
    except Exception as e:  # noqa: BLE001
        print(f"[warn] product hashtags failed: {e}", file=sys.stderr)
        return []
    m = re.search(r"\[.*\]", raw, re.S)
    if not m:
        return []
    try:
        tags = json.loads(m.group(0))
    except json.JSONDecodeError:
        return []
    return [str(t).lstrip("#").strip().lower().replace(" ", "")
            for t in tags if str(t).strip()]


def relevant_to(tag, product_text):
    """Reject tags naming a film/character that isn't in this product."""
    others = {
        "creed": ["creed", "adonis", "rocky", "balboa", "michaelbjordan",
                  "michealbjordan", "mbj"],
        "scarface": ["scarface", "tony", "montana", "pacino"],
        "snowfall": ["snowfall", "franklin", "saint", "snowfallfx", "damsonidris"],
    }
    p = (product_text or "").lower()
    for film, words in others.items():
        if film in p:
            continue  # this IS the product's film, fine
        if any(w in tag for w in words):
            return False
    return True


def build(cfg, image_urls, product=None):
    lib = load_library()

    # Primary source: hashtags about the product actually being posted.
    product_tags_list = product_hashtags(cfg, product)

    # Secondary: live trend research (often blocked on free tier — optional).
    try:
        trending = research_trending_hashtags(cfg)
    except Exception as e:  # noqa: BLE001
        print(f"[warn] trend research unavailable: {e}", file=sys.stderr)
        trending = (lib.get("trend_cache") or {}).get("tags", [])
    lib = validate_hashtags(cfg, trending, lib)

    # Tertiary: tags that performed on your own account — but only generic ones,
    # never another film's character tags.
    perf = own_performance(cfg)
    proven = [t for t in winning_tags(perf) if relevant_to(t, product)]

    ordered, seen = [], set()
    for t in product_tags_list + trending + proven:
        if t in seen or not relevant_to(t, product):
            continue
        seen.add(t)
        info = lib["tags"].get(t)
        if info and not info.get("valid", True):
            continue  # known-bad tag, skip
        ordered.append(t)

    hashtags = ordered[:HASHTAG_COUNT]
    print(f"[info] hashtags: {len(product_tags_list)} from product, "
          f"{len(trending)} trending, {len(proven)} proven → {len(hashtags)} used",
          file=sys.stderr)

    # Research brief: what copy is actually converting in this niche today.
    brief, brief_block = {}, ""
    try:
        import swipefile
        brief = swipefile.build_brief(cfg)
        brief_block = swipefile.as_prompt_block(brief)
    except Exception as e:  # noqa: BLE001
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
