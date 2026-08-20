#!/usr/bin/env python3
"""Scene generator — puts YOUR real posters into AI-generated rooms.

Built from analysis of high-performing wall-art accounts (Displate, Posterstore,
drool_art, postersdrop). What those posts have in common, and what this file
reproduces:

  1. ANGLED, not flat-on — the wall recedes, the frame has depth
  2. PROPS — shelf, lamp, books, plant, wood slat panelling. Never a bare wall
  3. HARD DIRECTIONAL LIGHT — window light raking across, visible shadow edges
  4. NEON / PICTURE-LIT variant — closest match to the gaming/music audience
  5. GALLERY WALL — 2-3 prints clustered; the highest-engagement format seen

The room is AI-generated; the artwork is always your real file, so the product
is pixel-accurate.

Usage:
  python3 sceneshop.py --poster <url> --handle scarface
  python3 sceneshop.py --posters <url1,url2,url3> --handle x --scenes gallery
  python3 sceneshop.py --list-scenes
"""
import argparse
import io
import json
import os
import random
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter

import perspective as P

HERE = Path(__file__).parent
CONFIG_PATH = HERE / "config.json"
OUT_DIR = HERE / "creatives"
CF_MODEL = "@cf/black-forest-labs/flux-1-schnell"

CANVAS = 1080

# Shot discipline shared by every prompt.
BASE = ("interior photograph, shot at a slight three-quarter angle so the wall "
        "recedes, eye level, 35mm lens, photorealistic, real home, natural "
        "imperfections, sharp focus, no people, "
        "bare empty wall with nothing hanging on it, no posters, no pictures, "
        "no frames, no artwork, no wall decor")

# Hard, directional light — the signature of the reference posts.
LIGHT = {
    "Power Collection":      "single hard light source from the left, deep "
                             "black shadows, low-key dramatic contrast, "
                             "moody dark atmosphere",
    "Discipline Collection": "hard raking window light from one side casting "
                             "sharp visible shadow edges across the wall, "
                             "cool grey daylight, high contrast",
    "Ambition Collection":   "warm golden hour sunlight raking across the wall "
                             "at a low angle, long hard shadows, amber haze",
}
DEFAULT_LIGHT = ("hard directional window light casting a visible shadow edge "
                 "across the wall, bright natural daylight")

# `box` = (left, top, width, height) fractions. `lean`/`side` set the angle.
SCENES = {
    "gym": {
        "room": ("dark modern home gym, matte black wall, rubber flooring, "
                 "dumbbell rack, weight bench, a towel over the bench, "
                 "large empty wall above"),
        "box": (0.24, 0.10, 0.46, 0.56), "lean": 0.15, "side": "right",
    },
    "office": {
        "room": ("modern home office, walnut desk below, brass desk lamp, "
                 "stack of books, small plant, closed laptop, vertical wood "
                 "slat wall panelling to one side, empty wall above the desk"),
        "box": (0.28, 0.07, 0.42, 0.50), "lean": 0.13, "side": "left",
    },
    "lounge": {
        "room": ("stylish living room, low grey sofa, oak console table with "
                 "art books and a ceramic vase, tall potted plant, sheer "
                 "curtain, large empty wall above the console"),
        "box": (0.25, 0.08, 0.46, 0.52), "lean": 0.14, "side": "right",
    },
    "loft": {
        "room": ("industrial loft, exposed red brick wall, concrete floor, "
                 "black metal shelving with a vinyl record and speaker, "
                 "tall factory window, empty brick wall in the centre"),
        "box": (0.27, 0.11, 0.44, 0.52), "lean": 0.16, "side": "left",
    },
    "neon": {
        "room": ("dark modern lounge at night lit by magenta and blue LED "
                 "strip lighting, black sectional sofa, low table, star "
                 "projector glow on the ceiling, city window at night, "
                 "picture light fixtures mounted on the empty wall"),
        "box": (0.26, 0.12, 0.46, 0.50), "lean": 0.15, "side": "right",
        "light": ("magenta and cyan neon rim light, dark room, high contrast "
                  "colour glow, moody night atmosphere"),
    },
    "slat": {
        "room": ("contemporary room with vertical walnut wood slat acoustic "
                 "panelling on one side and plain painted wall on the other, "
                 "low sideboard, ceramic lamp, trailing plant, empty wall"),
        "box": (0.30, 0.09, 0.40, 0.50), "lean": 0.12, "side": "left",
    },
}

DETAIL_SCENE = "detail"
GALLERY_SCENE = "gallery"

# Gallery wall layouts — (left, top, w, h) per print, fractions of canvas.
GALLERY_LAYOUTS = {
    2: [(0.13, 0.20, 0.32, 0.46), (0.53, 0.20, 0.32, 0.46)],
    3: [(0.09, 0.18, 0.25, 0.38), (0.38, 0.15, 0.25, 0.44),
        (0.67, 0.18, 0.25, 0.38)],
}


def load_config():
    return json.loads(CONFIG_PATH.read_text()) if CONFIG_PATH.exists() else {}


def cf_credentials(cfg):
    cf = cfg.get("cloudflare", {}) or {}
    account = cf.get("account_id") or os.environ.get("CLOUDFLARE_ACCOUNT_ID")
    token = cf.get("api_token") or os.environ.get("CLOUDFLARE_API_TOKEN")
    if not account or not token or "PASTE" in str(account) or "PASTE" in str(token):
        return None, None
    return account, token


def scene_prompt(scene_key, collection=None):
    spec = SCENES[scene_key]
    light = spec.get("light") or LIGHT.get(collection or "", DEFAULT_LIGHT)
    return f"{spec['room']}, {light}, {BASE}"


def generate_scene(cfg, prompt, seed=None):
    account, token = cf_credentials(cfg)
    if not account:
        print("[warn] no Cloudflare credentials — cannot generate scenes",
              file=sys.stderr)
        return None

    url = (f"https://api.cloudflare.com/client/v4/accounts/{account}"
           f"/ai/run/{CF_MODEL}")
    body = {"prompt": prompt, "steps": 8}
    if seed is not None:
        body["seed"] = int(seed)

    for attempt in range(3):
        try:
            r = requests.post(url, headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json"},
                json=body, timeout=120)
            if r.status_code != 200:
                print(f"[warn] cloudflare {r.status_code}: {r.text[:200]}",
                      file=sys.stderr)
                time.sleep(4 * (attempt + 1))
                continue
            if "application/json" in r.headers.get("Content-Type", ""):
                import base64
                b64 = (r.json().get("result") or {}).get("image")
                if not b64:
                    print(f"[warn] unexpected payload: {r.text[:200]}",
                          file=sys.stderr)
                    return None
                raw = base64.b64decode(b64)
            else:
                raw = r.content
            return Image.open(io.BytesIO(raw)).convert("RGB")
        except Exception as e:  # noqa: BLE001
            print(f"[warn] scene generation failed: {e}", file=sys.stderr)
            time.sleep(4 * (attempt + 1))
    return None


def fetch_poster(url):
    r = requests.get(url, timeout=60)
    r.raise_for_status()
    return Image.open(io.BytesIO(r.content)).convert("RGB")


def trim_mockup(img, tol=14):
    """Crop to the actual print artwork.

    Several of these designs carry their own cream/parchment margin baked
    into the artwork, framing the photo+quote block. If we composite that
    whole canvas and then add our own frame on top, the result is a
    frame-within-a-frame with a slab of dead space between them. So: find
    the artwork's own content block (the darker, busier region — text and
    photo) using a background estimate averaged from all four corners, and
    crop straight to that. No 40%-of-canvas bail-out — a large margin is
    exactly the case this needs to trim, not skip.
    """
    g = img.convert("L")
    w, h = img.size
    corners = [g.getpixel((2, 2)), g.getpixel((w - 3, 2)),
               g.getpixel((2, h - 3)), g.getpixel((w - 3, h - 3))]
    bg = sum(corners) / len(corners)
    mask = g.point(lambda p: 255 if abs(p - bg) > tol else 0)
    box = mask.getbbox()
    if not box:
        return img
    # Small pad so we don't shave the artwork's own border line
    pad = max(2, int(min(w, h) * 0.01))
    box = (max(0, box[0] - pad), max(0, box[1] - pad),
           min(w, box[2] + pad), min(h, box[3] + pad))
    return img.crop(box)


def add_frame(poster, border=26, frame_rgb=(16, 16, 18)):
    w, h = poster.size
    out = Image.new("RGB", (w + border * 2, h + border * 2), frame_rgb)
    out.paste(poster, (border, border))
    return out


def light_match(poster, scene_key, collection=None):
    """Nudge the artwork's brightness/contrast toward the room's lighting so
    the composite doesn't look pasted on."""
    dark = scene_key in ("gym", "neon") or collection == "Power Collection"
    poster = ImageEnhance.Brightness(poster).enhance(0.88 if dark else 1.0)
    return ImageEnhance.Contrast(poster).enhance(1.06)


def place(scene, poster, spec):
    """Warp the framed poster onto the wall at the scene's angle."""
    scene = scene.resize((CANVAS, CANVAS), Image.LANCZOS)
    left, top, bw, bh = spec["box"]

    target_w, target_h = CANVAS * bw, CANVAS * bh
    pw, ph = poster.size
    scale = min(target_w / pw, target_h / ph)
    poster = poster.resize((max(1, int(pw * scale)), max(1, int(ph * scale))),
                           Image.LANCZOS)
    w, h = poster.size

    x = CANVAS * left + (target_w - w) / 2
    y = CANVAS * top + (target_h - h) / 2
    quad = P.wall_quad(x, y, w, h,
                       lean=spec.get("lean", 0.14),
                       side=spec.get("side", "right"))
    return P.warp_onto(scene, poster, quad)


def gallery(scene, posters, collection=None):
    """2-3 prints clustered on one wall — the format that performed best in
    the reference set."""
    scene = scene.resize((CANVAS, CANVAS), Image.LANCZOS)
    n = min(len(posters), 3)
    layout = GALLERY_LAYOUTS.get(n, GALLERY_LAYOUTS[2])
    out = scene

    for poster, (left, top, bw, bh) in zip(posters[:n], layout):
        framed = add_frame(light_match(poster, "gallery", collection), border=13)
        target_w, target_h = CANVAS * bw, CANVAS * bh
        pw, ph = framed.size
        scale = min(target_w / pw, target_h / ph)
        framed = framed.resize((max(1, int(pw * scale)), max(1, int(ph * scale))),
                               Image.LANCZOS)
        w, h = framed.size
        x = CANVAS * left + (target_w - w) / 2
        y = CANVAS * top + (target_h - h) / 2
        # Gentler lean so the cluster stays readable
        quad = P.wall_quad(x, y, w, h, lean=0.07, side="right")
        out = P.warp_onto(out, framed, quad)
    return out


def detail_shot(poster):
    """Close crop of the print — sells paper and print quality.

    Centre-weighted with only a small jitter. A wide-open random crop can
    land on the edge of the artwork and slice through a headline mid-word,
    which reads as broken rather than "zoomed in for detail".
    """
    poster = poster.convert("RGB")
    w, h = poster.size
    cw, ch = int(w * 0.62), int(h * 0.62)
    jitter_x, jitter_y = int(w * 0.06), int(h * 0.06)
    cx = w // 2 + random.randint(-jitter_x, jitter_x)
    cy = h // 2 + random.randint(-jitter_y, jitter_y)
    x = max(0, min(w - cw, cx - cw // 2))
    y = max(0, min(h - ch, cy - ch // 2))
    crop = poster.crop((x, y, x + cw, y + ch)).resize(
        (CANVAS, CANVAS), Image.LANCZOS)
    crop = Image.blend(crop, Image.new("RGB", crop.size, (255, 244, 226)), 0.06)
    mask = Image.new("L", crop.size, 0)
    ImageDraw.Draw(mask).rectangle([40, 40, CANVAS - 40, CANVAS - 40], fill=255)
    mask = mask.filter(ImageFilter.GaussianBlur(60))
    return Image.composite(crop, Image.blend(
        crop, Image.new("RGB", crop.size, (0, 0, 0)), 0.35), mask)


def zoom_to_hero(img, box, factor=0.66):
    """Crop in so the artwork dominates — slide 1 must read as a thumbnail."""
    w, h = img.size
    left, top, bw, bh = box
    cx = (left + bw / 2) * w
    cy = (top + bh / 2) * h + h * 0.02
    side = min(w, h) * factor
    x0 = max(0, min(w - side, cx - side / 2))
    y0 = max(0, min(h - side, cy - side / 2))
    return img.crop((int(x0), int(y0), int(x0 + side), int(y0 + side))) \
              .resize((CANVAS, CANVAS), Image.LANCZOS)


def build(cfg, poster_url, handle, scenes=None, collection=None,
          extra_posters=None, out_dir=OUT_DIR):
    """Generate a 3-slide carousel for one product.

    Default: hero shot, wide room shot (different scene), detail crop.
    Pass scenes=['gallery'] with extra_posters for a gallery wall.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    poster = trim_mockup(fetch_poster(poster_url))
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    saved = []

    if scenes:
        plan = [(s, "") for s in scenes]
    else:
        rooms = random.sample(list(SCENES), 2)
        plan = [(rooms[0], "hero"), (rooms[1], "wide"), (DETAIL_SCENE, "")]

    for idx, (name, framing) in enumerate(plan, 1):
        suffix = f"{name}{'-' + framing if framing else ''}"
        path = out_dir / f"{stamp}-{handle}-{idx}-{suffix}.jpg"
        try:
            if name == DETAIL_SCENE:
                img = detail_shot(poster)

            elif name == GALLERY_SCENE:
                room = generate_scene(cfg, scene_prompt("lounge", collection))
                if room is None:
                    continue
                others = [trim_mockup(fetch_poster(u))
                          for u in (extra_posters or [])[:2]]
                img = gallery(room, [poster] + others, collection)

            else:
                if name not in SCENES:
                    print(f"[warn] unknown scene '{name}'", file=sys.stderr)
                    continue
                room = generate_scene(cfg, scene_prompt(name, collection))
                if room is None:
                    continue
                spec = SCENES[name]
                framed = add_frame(light_match(poster, name, collection))
                img = place(room, framed, spec)
                if framing == "hero":
                    img = zoom_to_hero(img, spec["box"])

            img.save(path, "JPEG", quality=91, optimize=True)
            saved.append(path)
            print(f"[info] created {path.name}")
        except Exception as e:  # noqa: BLE001
            print(f"[warn] scene '{name}' failed: {e}", file=sys.stderr)

    return saved


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--poster")
    ap.add_argument("--posters", help="Comma-separated, for gallery walls")
    ap.add_argument("--handle", default="product")
    ap.add_argument("--scenes", default=None)
    ap.add_argument("--collection", default=None)
    ap.add_argument("--list-scenes", action="store_true")
    args = ap.parse_args()

    if args.list_scenes:
        for k in SCENES:
            print(f"\n{k.upper()}  (lean {SCENES[k]['lean']}, "
                  f"{SCENES[k]['side']} edge far)")
            print(f"  {scene_prompt(k)[:150]}…")
        print(f"\n{GALLERY_SCENE.upper()}\n  2-3 prints clustered on one wall")
        print(f"\n{DETAIL_SCENE.upper()}\n  close crop of the print itself")
        return

    urls = [u.strip() for u in (args.posters or "").split(",") if u.strip()]
    poster = args.poster or (urls[0] if urls else None)
    if not poster:
        sys.exit("--poster or --posters required")

    cfg = load_config()
    scenes = ([s.strip() for s in args.scenes.split(",") if s.strip()]
              if args.scenes else None)
    paths = build(cfg, poster, args.handle, scenes, args.collection,
                  extra_posters=urls[1:])
    print(json.dumps([str(p) for p in paths], indent=2))


if __name__ == "__main__":
    main()
