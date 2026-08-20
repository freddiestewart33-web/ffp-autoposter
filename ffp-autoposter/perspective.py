#!/usr/bin/env python3
"""Perspective warping for wall-mounted artwork.

Every high-performing reference post is shot at a slight angle — you see the
wall recede and the frame has depth. A dead-on paste reads as a stock mockup.
This warps the framed poster onto a trapezoid so it sits on the wall properly.

No numpy: the 8 perspective coefficients are solved with plain Gaussian
elimination so the workflow stays dependency-light.
"""
from PIL import Image, ImageDraw, ImageFilter


def _solve(matrix, rhs):
    """Gaussian elimination with partial pivoting. Returns solution vector."""
    n = len(rhs)
    m = [row[:] + [rhs[i]] for i, row in enumerate(matrix)]

    for col in range(n):
        pivot = max(range(col, n), key=lambda r: abs(m[r][col]))
        if abs(m[pivot][col]) < 1e-12:
            raise ValueError("degenerate perspective quad")
        m[col], m[pivot] = m[pivot], m[col]

        for r in range(col + 1, n):
            f = m[r][col] / m[col][col]
            for c in range(col, n + 1):
                m[r][c] -= f * m[col][c]

    out = [0.0] * n
    for r in range(n - 1, -1, -1):
        s = m[r][n] - sum(m[r][c] * out[c] for c in range(r + 1, n))
        out[r] = s / m[r][r]
    return out


def find_coeffs(target, source):
    """Coefficients mapping the OUTPUT quad back to the INPUT rectangle.

    PIL's PERSPECTIVE transform samples the source as:
        x_src = (a*x + b*y + c) / (g*x + h*y + 1)
        y_src = (d*x + e*y + f) / (g*x + h*y + 1)
    """
    matrix, rhs = [], []
    for (tx, ty), (sx, sy) in zip(target, source):
        matrix.append([tx, ty, 1, 0, 0, 0, -sx * tx, -sx * ty])
        rhs.append(sx)
        matrix.append([0, 0, 0, tx, ty, 1, -sy * tx, -sy * ty])
        rhs.append(sy)
    return _solve(matrix, rhs)


def wall_quad(x, y, w, h, lean=0.14, side="right", rise=0.03):
    """Corners for a poster viewed from one side.

    `lean` is how strongly it recedes (0 = flat on, 0.2 = strongly angled).
    `side` is which edge is FURTHER from the camera.
    `rise` lifts the far edge slightly, as happens when shooting from below.
    """
    dx = w * lean
    dy = h * lean * 0.5
    lift = h * rise

    if side == "right":
        # right edge further away → narrower and inset
        return [
            (x, y),                              # top-left  (near)
            (x + w - dx, y + dy - lift),         # top-right (far)
            (x + w - dx, y + h - dy - lift),     # bottom-right (far)
            (x, y + h),                          # bottom-left (near)
        ]
    return [
        (x + dx, y + dy - lift),                 # top-left (far)
        (x + w, y),                              # top-right (near)
        (x + w, y + h),                          # bottom-right (near)
        (x + dx, y + h - dy - lift),             # bottom-left (far)
    ]


def warp_onto(scene, poster, quad):
    """Warp `poster` onto `quad` in `scene`, with a contact shadow."""
    sw, sh = scene.size
    xs = [p[0] for p in quad]
    ys = [p[1] for p in quad]
    bx0, by0 = int(min(xs)), int(min(ys))
    bx1, by1 = int(max(xs)), int(max(ys))
    bw, bh = max(1, bx1 - bx0), max(1, by1 - by0)

    local = [(px - bx0, py - by0) for px, py in quad]
    pw, ph = poster.size
    src = [(0, 0), (pw, 0), (pw, ph), (0, ph)]

    coeffs = find_coeffs(local, src)

    warped = poster.convert("RGBA").transform(
        (bw, bh), Image.PERSPECTIVE, coeffs, Image.BICUBIC)

    # Alpha mask of exactly the quad, so edges stay crisp
    mask = Image.new("L", (bw, bh), 0)
    ImageDraw.Draw(mask).polygon(local, fill=255)
    warped.putalpha(mask)

    # Shadow: same quad, offset away from the light, blurred
    pad = 60
    shadow = Image.new("RGBA", (sw, sh), (0, 0, 0, 0))
    off_x, off_y = 10, 16
    ImageDraw.Draw(shadow).polygon(
        [(px + bx0 + off_x, py + by0 + off_y) for px, py in local],
        fill=(0, 0, 0, 120))
    shadow = shadow.filter(ImageFilter.GaussianBlur(18))

    out = Image.alpha_composite(scene.convert("RGBA"), shadow)
    out.paste(warped, (bx0, by0), warped)
    return out.convert("RGB")
