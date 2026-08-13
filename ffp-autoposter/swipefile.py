#!/usr/bin/env python3
"""Swipe-file researcher - finds what caption/ad copy is winning in the niche."""
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

HERE = Path(__file__).parent
CONFIG_PATH = HERE / "config.json"
BRIEF_PATH = HERE / "swipe_brief.json"
LIBRARY_PATH = HERE / "hashtag_library.json"
GRAPH = "https://graph.facebook.com/v21.0"

REFRESH_AFTER_HOURS = 20
MAX_COMPETITORS = 6
MAX_HASHTAG_SAMPLES = 3


def load_config():
    if not CONFIG_PATH.exists():
        sys.exit("config.json not found")
    return json.loads(CONFIG_PATH.read_text())


def _now():
    return datetime.now(timezone.utc).isoformat()


def brief_is_fresh():
    if not BRIEF_PATH.exists():
        return False
    try:
        b = json.loads(BRIEF_PATH.read_text())
        age = datetime.now(timezone.utc) - datetime.fromisoformat(b["generated"])
        return age < timedelta(hours=REFRESH_AFTER_HOURS)
    except Exception:
        return False


def competitor_posts(cfg):
    token = cfg["meta"]["system_user_token"]
    ig_id = cfg["meta"]["ig_business_account_id"]
    handles = cfg.get("research", {}).get("competitors", [])[:MAX_COMPETITORS]
    harvested = []

    for handle in handles:
        handle = handle.lstrip("@").strip()
        for fields in (
            f"business_discovery.username({handle})"
            "{followers_count,media{caption,like_count,comments_count,timestamp}}",
            f"business_discovery.username({handle})"
            "{followers_count,media{like_count,comments_count,timestamp}}",
        ):
            try:
                r = requests.get(f"{GRAPH}/{ig_id}", params={
                    "fields": fields, "access_token": token}, timeout=45)
                if r.status_code != 200:
                    continue
                bd = r.json().get("business_discovery", {})
                followers = bd.get("followers_count") or 1
                for m in bd.get("media", {}).get("data", [])[:25]:
                    likes = m.get("like_count", 0)
                    comments = m.get("comments_count", 0)
                    harvested.append({
                        "source": f"@{handle}",
                        "caption": m.get("caption"),
                        "likes": likes,
                        "comments": comments,
                        "engagement_rate": round((likes + 2 * comments) / followers, 5),
                    })
                break
            except Exception as e:
                print(f"[warn] business_discovery {handle}: {e}", file=sys.stderr)

    return harvested


def hashtag_top_posts(cfg):
    token = cfg["meta"]["system_user_token"]
    ig_id = cfg["meta"]["ig_business_account_id"]
    if not LIBRARY_PATH.exists():
        return []
    lib = json.loads(LIBRARY_PATH.read_text())

    known = [(t, i["hashtag_id"]) for t, i in lib.get("tags", {}).items()
             if i.get("valid") and i.get("hashtag_id")]
    known.sort(key=lambda kv: -lib["tags"][kv[0]].get("avg_top_likes", 0))

    harvested = []
    for tag, hid in known[:MAX_HASHTAG_SAMPLES]:
        try:
            r = requests.get(f"{GRAPH}/{hid}/top_media", params={
                "user_id": ig_id,
                "fields": "caption,like_count,comments_count",
                "limit": 20, "access_token": token}, timeout=45)
            if r.status_code != 200:
                continue
            for m in r.json().get("data", []):
                harvested.append({
                    "source": f"#{tag}",
                    "caption": m.get("caption"),
                    "likes": m.get("like_count", 0),
                    "comments": m.get("comments_count", 0),
                })
        except Exception as e:
            print(f"[warn] top_media {tag}: {e}", file=sys.stderr)
    return harvested


def synthesise(cfg, competitor_data, hashtag_data):
    import captioner

    with_text = [p for p in competitor_data + hashtag_data if p.get("caption")]
    with_text.sort(key=lambda p: p.get("engagement_rate", 0) or p.get("likes", 0),
                   reverse=True)
    sample = with_text[:40]

    if sample:
        observed = "\n\n".join(
            f"[{p['source']} - {p.get('likes',0)} likes, {p.get('comments',0)} comments]\n"
            f"{(p['caption'] or '')[:400]}"
            for p in sample)
        observed_block = (
            "Here are REAL captions from high-performing posts in this exact "
            f"niche, pulled today with their engagement numbers:\n\n{observed}\n\n"
            "Analyse what these have in common that made them work."
        )
    else:
        observed_block = (
            "NOTE: Instagram did not return caption text for competitor or "
            "top posts today, so base your analysis purely on live web research."
        )

    today = datetime.now(timezone.utc).strftime("%d %B %Y")
    prompt = f"""Today is {today}. You are a direct-response copy strategist for
Final Frame Prints - framed cinematic movie-quote posters (Scarface, Creed,
Snowfall, gym/hustle motivation). Buyers: mostly men 18-40 kitting out a gym,
office, mancave or first flat. Shop: {cfg.get('brand', {}).get('shop_url', '')}

{observed_block}

ALSO search the web for current, recent information on what is working RIGHT NOW
in social ad copy and Instagram captions for wall art, posters, print-on-demand
and home decor - hooks, formats, angles, opening lines that are converting this
month. Prioritise recent sources over general advice.

Return ONLY valid JSON in exactly this shape:
{{
  "hooks": ["8 opening lines that stop the scroll, in this brand's voice"],
  "formulas": ["5 caption structures that are converting right now, described briefly"],
  "angles": ["5 emotional angles that resonate with this specific buyer"],
  "avoid": ["4 things that are overused or killing engagement right now"],
  "notes": "2-3 sentences on what changed or is trending this week"
}}"""

    raw = captioner.gemini(cfg, [{"text": prompt}], use_search=True, temperature=0.6)
    m = re.search(r"\{.*\}", raw, re.S)
    if not m:
        raise RuntimeError(f"Could not parse brief: {raw[:300]}")
    return json.loads(m.group(0))


def build_brief(cfg, force=False):
    if not force and brief_is_fresh():
        return json.loads(BRIEF_PATH.read_text())

    comp = competitor_posts(cfg)
    tags = hashtag_top_posts(cfg)
    try:
        brief = synthesise(cfg, comp, tags)
    except Exception as e:
        print(f"[warn] brief synthesis failed: {e}", file=sys.stderr)
        if BRIEF_PATH.exists():
            return json.loads(BRIEF_PATH.read_text())
        return {}

    brief["generated"] = _now()
    brief["captions_sampled"] = len([p for p in comp + tags if p.get("caption")])
    brief["posts_analysed"] = len(comp) + len(tags)
    BRIEF_PATH.write_text(json.dumps(brief, indent=2))
    return brief


def as_prompt_block(brief):
    if not brief:
        return ""
    def lines(key, label):
        vals = brief.get(key) or []
        return f"{label}:\n" + "\n".join(f"  - {v}" for v in vals) + "\n" if vals else ""
    return (
        "\nRESEARCH BRIEF - what is converting in this niche right now "
        f"(compiled {brief.get('generated', '')[:10]}, "
        f"from {brief.get('posts_analysed', 0)} real posts + live web research):\n"
        + lines("hooks", "Proven hook styles")
        + lines("formulas", "Caption structures that convert")
        + lines("angles", "Emotional angles that land")
        + lines("avoid", "AVOID - overused / underperforming")
        + (f"This week: {brief['notes']}\n" if brief.get("notes") else "")
    )


if __name__ == "__main__":
    cfg = load_config()
    print(json.dumps(build_brief(cfg, force="--force" in sys.argv), indent=2))
