#!/usr/bin/env python3
"""Content queue — posts Freddie's own hand-made carousel sets.

The queue is a plain folder of images. Drop files in, they get posted, they
move to `posted/` and never come round again. No database, no state file to
drift out of sync: the folder IS the state.

FILE NAMING
    <set>-<n><shot>.png      e.g. scarface-1h.png

    shot codes, and the order they post in:
        h = hero      close on the wall, reads as a thumbnail
        m = mounted   wide room shot
        p = product   leaning, physical object

    So `scarface-1h`, `scarface-1m`, `scarface-1p` are one 3-slide carousel.

A set is only eligible once ALL its declared slides are present — a half
uploaded set is skipped rather than posted with a slide missing.

Images are served to the social APIs over raw.githubusercontent.com, which
means the repo must be public and the file must be committed before the run.

POSTING ORDER
    Default order rotates collections: one Creed, one Scarface, one
    Snowfall, then back to Creed — rather than blasting through all of one
    collection before touching the next.

    To override that order (e.g. "skip ahead to Snowfall next"), drop a
    plain text file at `content-queue/order.txt`, one set name per line
    (blank lines and lines starting with # are ignored):

        snowfall-2
        creed-3
        scarface-1

    Anything listed in order.txt goes first, in that order; anything not
    listed falls back to the normal rotation after it. Delete order.txt (or
    empty it) to go back to plain rotation.
"""
import os
import re
import shutil
from collections import defaultdict
from pathlib import Path
from urllib.parse import quote

HERE = Path(__file__).parent
QUEUE_DIR = HERE / "content-queue"
POSTED_DIR = QUEUE_DIR / "posted"
ORDER_FILE = QUEUE_DIR / "order.txt"

RAW_BASE = ("https://raw.githubusercontent.com/freddiestewart33-web/"
            "ffp-autoposter/main/ffp-autoposter/content-queue")

IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp"}

# Slide order within a carousel.
SHOT_ORDER = ["h", "m", "p"]

# set prefix -> Shopify product handle.
# The handles are what the live catalogue actually uses; several are historic
# and don't match the product name (e.g. Creed "Nothing to lose" still lives at
# `premium-matte-paper-poster`), which is exactly why this map is explicit
# rather than guessed from the filename.
SETS = {
    # Scarface — Power Collection
    "scarface-1": "scarface-poster",                        # I always tell the truth
    "scarface-2": "scarface-all-i-have-in-this-world",      # All I have in this world
    "scarface-3": "scarface-collection",                    # The world is yours
    "scarface-4": "scarface-you-gotta-make-the-money-first",

    # Creed — Discipline Collection
    "creed-1": "creed-collection",                          # Keep moving forward
    "creed-2": "creed-poster",                              # I gotta prove it
    "creed-3": "premium-matte-paper-poster",                # Nothing to lose
    "creed-4": "creed-your-toughest-opponent",

    # Snowfall — Ambition Collection
    "snowfall-1": "snowfall-poster",                        # Run the game
    "snowfall-2": "snowfall-never-get-to-greedy",
    "snowfall-3": "snowfall-prides-gonna-be-the-death-of-you",
    "snowfall-4": "snowfall-brick-by-brick",
}

NAME_RE = re.compile(r"^(?P<set>[a-z0-9]+-\d+)(?P<shot>[a-z])$", re.I)


def _round_robin_order():
    """[creed-1, scarface-1, snowfall-1, creed-2, scarface-2, ...] — one set
    per collection per lap, collections in alphabetical order (which happens
    to read Creed, Scarface, Snowfall — exactly the rotation Freddie wants).
    """
    by_collection = defaultdict(list)
    for name in SETS:
        prefix, _, num = name.rpartition("-")
        by_collection[prefix].append((int(num), name))
    for lst in by_collection.values():
        lst.sort()
    collections = [by_collection[c] for c in sorted(by_collection)]
    max_len = max(len(c) for c in collections)
    order = []
    for i in range(max_len):
        for coll in collections:
            if i < len(coll):
                order.append(coll[i][1])
    return order


ROUND_ROBIN_ORDER = _round_robin_order()


def _posting_order():
    """order.txt entries first (in the order written), then the normal
    rotation for anything not explicitly listed."""
    if ORDER_FILE.exists():
        listed = [l.strip() for l in ORDER_FILE.read_text().splitlines()
                  if l.strip() and not l.strip().startswith("#")]
        rest = [s for s in ROUND_ROBIN_ORDER if s not in listed]
        return listed + rest
    return ROUND_ROBIN_ORDER


def _parse(path):
    """('scarface-1', 'h') from scarface-1h.png, or None if it doesn't fit."""
    if path.suffix.lower() not in IMAGE_EXT:
        return None
    m = NAME_RE.match(path.stem)
    if not m:
        return None
    return m.group("set").lower(), m.group("shot").lower()


def available_sets(queue_dir=QUEUE_DIR):
    """Complete, unposted sets, as {set_name: [paths in slide order]}.

    'Complete' means every shot code found for that set is present and the
    set has at least a hero. We don't demand all three — if you only made a
    hero and a mounted, that's a valid 2-slide carousel — but we do demand
    the hero, since it's slide one.
    """
    if not queue_dir.exists():
        return {}

    groups = defaultdict(dict)
    for path in sorted(queue_dir.iterdir()):
        if path.is_dir():
            continue
        parsed = _parse(path)
        if not parsed:
            continue
        set_name, shot = parsed
        groups[set_name][shot] = path

    out = {}
    for set_name, shots in groups.items():
        if "h" not in shots:
            print(f"[warn] queue set '{set_name}' has no hero slide — skipping")
            continue
        ordered = [shots[s] for s in SHOT_ORDER if s in shots]
        extra = sorted(k for k in shots if k not in SHOT_ORDER)
        ordered += [shots[k] for k in extra]
        out[set_name] = ordered
    return out


def next_set(queue_dir=QUEUE_DIR):
    """The set to post now, or None if the queue is empty.

    Follows order.txt first if present, otherwise rotates collections
    (one Creed, one Scarface, one Snowfall, repeat) rather than blasting
    through a whole collection before touching the next.
    """
    sets = available_sets(queue_dir)
    if not sets:
        return None
    for name in _posting_order():
        if name in sets:
            return name, sets[name]
    # Anything present but not in SETS (e.g. a new collection added without
    # updating the rotation) — fall back to alphabetical so it still posts.
    name = sorted(sets)[0]
    return name, sets[name]


def raw_urls(paths):
    return [f"{RAW_BASE}/{quote(p.name)}" for p in paths]


def product_handle(set_name):
    handle = SETS.get(set_name)
    if not handle:
        print(f"[warn] no product mapped for queue set '{set_name}' — "
              f"caption will fall back to catalogue rotation")
    return handle


def mark_posted(paths, posted_dir=POSTED_DIR):
    """Move the set out of the queue so it can never be picked again."""
    posted_dir.mkdir(parents=True, exist_ok=True)
    moved = []
    for p in paths:
        dest = posted_dir / p.name
        if dest.exists():
            dest = posted_dir / f"{p.stem}-{os.urandom(3).hex()}{p.suffix}"
        shutil.move(str(p), str(dest))
        moved.append(dest)
    return moved


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true")
    args = ap.parse_args()

    sets = available_sets()
    if not sets:
        print("Queue is empty — the generator fallback would run.")
        return
    order = _posting_order()
    print(f"{len(sets)} set(s) queued (posting order"
          f"{' — order.txt active' if ORDER_FILE.exists() else ' — default rotation'}):\n")
    for name in sorted(sets, key=lambda n: order.index(n) if n in order else 999):
        handle = SETS.get(name, "UNMAPPED")
        slides = ", ".join(p.name for p in sets[name])
        print(f"  {name:<12} -> {handle}")
        print(f"  {'':<12}    {slides}\n")
    nxt = next_set()
    print(f"Next to post: {nxt[0]}")


if __name__ == "__main__":
    main()
