"""
Builds cat_sprites_life/ for ProfessorCatLife.py.

* Takes the walk / idle / sleep frames from cat_sprites_real/.
* Cuts the sitting, grooming and lying poses out of the Gemini sprite sheet.
* Normalizes everything: one shared pixel scale, every frame anchored at the
  same ground point (paws at the bottom, body mass centred), so frames don't
  jump or change size during animation.

Usage:  python prepare_life_sprites.py
"""
import sys
from pathlib import Path

import numpy as np
from PIL import Image
from scipy import ndimage

HERE = Path(__file__).resolve().parent
SRC = HERE / "cat_sprites_real"
SHEET = HERE / "Gemini_Generated_Image_i20ztdi20ztdi20z.jpeg"
OUT = HERE / "cat_sprites_life"
SHEET_ALPHA_CACHE = HERE / "build" / "i20_alpha.png"
PAD = 6


def largest_blob(img):
    """Keep only the largest opaque region (drops stray labels / specks)."""
    a = np.array(img)[:, :, 3]
    mask = a > 30
    lab, n = ndimage.label(ndimage.binary_closing(mask, iterations=2))
    if n == 0:
        return img
    sizes = ndimage.sum(mask, lab, range(1, n + 1))
    keep = lab == (int(np.argmax(sizes)) + 1)
    arr = np.array(img)
    arr[:, :, 3] = np.where(keep, arr[:, :, 3], 0)
    # soften the hard fringe a bit
    arr[:, :, 3] = np.where(arr[:, :, 3] < 40, 0, arr[:, :, 3])
    return Image.fromarray(arr)


def trim(img):
    bbox = img.getchannel("A").point(lambda v: 255 if v > 30 else 0).getbbox()
    return img.crop(bbox)


def anchor(img):
    """(x, y) of the ground anchor: alpha-weighted centre of mass, bottom row."""
    a = np.array(img)[:, :, 3].astype(float)
    cols = a.sum(axis=0)
    cx = (cols * np.arange(len(cols))).sum() / max(cols.sum(), 1)
    return cx, img.height


def load_sheet_alpha():
    if SHEET_ALPHA_CACHE.exists():
        return Image.open(SHEET_ALPHA_CACHE).convert("RGBA")
    # The sheet's "transparent" checkerboard is neutral grey, while the cat is
    # warm tan/brown, so a colour key works (and needs far less memory than rembg).
    print("Keying out the checkerboard background on the Gemini sheet...")
    rgb = np.array(Image.open(SHEET).convert("RGB")).astype(int)
    sat = rgb.max(axis=2) - rgb.min(axis=2)
    bright = rgb.mean(axis=2)
    mask = (sat > 14) | (bright > 150)
    mask = ndimage.binary_opening(mask, iterations=1)
    mask = ndimage.binary_closing(mask, iterations=2)
    mask = ndimage.binary_fill_holes(mask)
    # soft edge: alpha ramps with saturation near the border
    soft = np.clip((sat - 8) * 20, 0, 255)
    alpha = np.where(ndimage.binary_erosion(mask, iterations=1), 255, np.where(mask, soft, 0))
    out = Image.fromarray(np.dstack([rgb, alpha]).astype(np.uint8), "RGBA")
    SHEET_ALPHA_CACHE.parent.mkdir(exist_ok=True)
    out.save(SHEET_ALPHA_CACHE)
    return out


def sheet_blobs(sheet):
    """Return bounding boxes of every cat on the sheet, in reading order."""
    a = np.array(sheet)[:, :, 3] > 40
    lab, _ = ndimage.label(ndimage.binary_closing(a, iterations=4))
    boxes = []
    for sl in ndimage.find_objects(lab):
        h, w = sl[0].stop - sl[0].start, sl[1].stop - sl[1].start
        if h > 60 and w > 60:
            boxes.append((sl[1].start, sl[0].start, sl[1].stop, sl[0].stop))
    # group into rows by vertical centre, then sort left-to-right
    boxes.sort(key=lambda b: (b[1] + b[3]) / 2)
    rows, cur = [], []
    for b in boxes:
        if cur and (b[1] + b[3]) / 2 - (cur[-1][1] + cur[-1][3]) / 2 > 60:
            rows.append(sorted(cur))
            cur = []
        cur.append(b)
    if cur:
        rows.append(sorted(cur))
    return [b for r in rows for b in r]


def main():
    frames = {}  # name -> list of trimmed RGBA images (native scale)

    def from_dir(prefix):
        out, i = [], 1
        while (SRC / f"{prefix}_{i:02d}.png").exists():
            out.append(trim(largest_blob(Image.open(SRC / f"{prefix}_{i:02d}.png").convert("RGBA"))))
            i += 1
        return out

    frames["walk"] = from_dir("walk")
    frames["stand"] = from_dir("idle")
    frames["sleep"] = from_dir("sleep")
    if not frames["walk"]:
        sys.exit(f"No walk frames in {SRC}")

    # ---- extra poses from the Gemini sheet ----
    if SHEET.exists():
        sheet = load_sheet_alpha()
        boxes = sheet_blobs(sheet)
        print(f"Found {len(boxes)} cats on the sheet")
        if len(boxes) >= 16:
            cut = lambda k: trim(largest_blob(sheet.crop(boxes[k - 1])))
            # scale sheet cats so their walking height matches cat_sprites_real
            ref_h = np.median([f.height for f in frames["walk"]])
            sheet_h = np.median([cut(k).height for k in range(1, 9)])
            s = ref_h / sheet_h
            rs = lambda im: im.resize((round(im.width * s), round(im.height * s)), Image.LANCZOS)
            frames["sit"] = [rs(cut(10)), rs(cut(11))]
            frames["groom"] = [rs(cut(12))]      # paw raised; turned into licking below
            frames["lie"] = [rs(cut(15))]
        else:
            print("Could not detect all 16 cats on the sheet; skipping sit/groom/lie poses")

    # ---- one cat: same size, colours and (softer) stripes in every pose ----
    for name, k in POSE_SCALE.items():
        if frames.get(name):
            frames[name] = [im.resize((round(im.width * k), round(im.height * k)), Image.LANCZOS)
                            for im in frames[name]]
    target = lab_stats(frames["walk"])
    for name in ("stand", "sleep", "sit", "groom", "lie"):
        if frames.get(name):
            frames[name] = match_colours(frames[name], target)
    for name, head_box in HEAD_BOX.items():
        if frames.get(name):
            frames[name] = [soften_pattern(im, head_box) for im in frames[name]]
    if frames.get("groom"):
        frames["groom"] = make_lick_frames(frames["groom"][0])
    # standing cat: shorter storybook legs to match the rigged walk
    frames["stand"] = [shorten_legs(im) for im in frames["stand"]]

    # ---- normalize onto shared canvases (per pose group) ----
    OUT.mkdir(exist_ok=True)
    for old in OUT.glob("*.png"):
        old.unlink()
    for name, imgs in frames.items():
        anchors = [anchor(im) for im in imgs]
        left = max(ax for ax, _ in anchors)
        right = max(im.width - ax for im, (ax, _) in zip(imgs, anchors))
        half = int(np.ceil(max(left, right))) + PAD
        height = max(im.height for im in imgs) + PAD
        for i, (im, (ax, ay)) in enumerate(zip(imgs, anchors), 1):
            canvas = Image.new("RGBA", (half * 2, height), (0, 0, 0, 0))
            canvas.paste(im, (int(round(half - ax)), height - ay), im)
            canvas.save(OUT / f"{name}_{i:02d}.png")
        print(f"{name}: {len(imgs)} frames -> {half * 2}x{height}")
    steady_colours("sleep")
    idle_leg = idle_leg_colours(frames["stand"])
    if idle_leg:
        for name, regions in LEG_REGIONS.items():
            recolour_legs(name, regions, idle_leg)
    rig_walk_frames(idle_leg)
    print(f"Done. Sprites written to {OUT}")


# ----------------------------------------------------------------------
# Colour matching: make every pose look like the same cat as the walk frames
# ----------------------------------------------------------------------
def _rgb2lab(rgb):
    c = rgb / 255.0
    c = np.where(c > 0.04045, ((c + 0.055) / 1.055) ** 2.4, c / 12.92)
    M = np.array([[0.4124, 0.3576, 0.1805], [0.2126, 0.7152, 0.0722], [0.0193, 0.1192, 0.9505]])
    xyz = c @ M.T / np.array([0.95047, 1.0, 1.08883])
    f = np.where(xyz > 0.008856, np.cbrt(xyz), 7.787 * xyz + 16 / 116)
    return np.stack([116 * f[..., 1] - 16, 500 * (f[..., 0] - f[..., 1]), 200 * (f[..., 1] - f[..., 2])], -1)


def _lab2rgb(lab):
    fy = (lab[..., 0] + 16) / 116
    fx, fz = fy + lab[..., 1] / 500, fy - lab[..., 2] / 200
    f = np.stack([fx, fy, fz], -1)
    xyz = np.where(f > 0.2069, f ** 3, (f - 16 / 116) / 7.787) * np.array([0.95047, 1.0, 1.08883])
    Mi = np.array([[3.2406, -1.5372, -0.4986], [-0.9689, 1.8758, 0.0415], [0.0557, -0.2040, 1.0570]])
    c = xyz @ Mi.T
    c = np.where(c > 0.0031308, 1.055 * np.clip(c, 0, None) ** (1 / 2.4) - 0.055, 12.92 * c)
    return np.clip(c * 255, 0, 255)


def lab_stats(imgs):
    px = np.concatenate([np.array(im)[np.array(im)[:, :, 3] > 200][:, :3] for im in imgs]).astype(float)
    lab = _rgb2lab(px)
    return lab.mean(0), lab.std(0)


def match_colours(imgs, target, strength=0.9):
    """Reinhard colour transfer in Lab: shift/scale each pose's colours so their
    overall tone, saturation and contrast match the walking cat."""
    (tm, ts), (sm, ss) = target, lab_stats(imgs)
    out = []
    for im in imgs:
        a = np.array(im).astype(float)
        lab = _rgb2lab(a[..., :3])
        matched = (lab - sm) / np.maximum(ss, 1e-3) * ts + tm
        lab = lab + (matched - lab) * strength
        a[..., :3] = _lab2rgb(lab)
        out.append(Image.fromarray(a.astype(np.uint8), "RGBA"))
    return out


# ----------------------------------------------------------------------
# Same cat in every pose: size, stripe pattern, grooming
# ----------------------------------------------------------------------
# Scale each pose so its head is the same size as the walking cat's head
# (measured side by side on a shared floor line).
# The idle drawings are painted ~15% smaller than the walk drawings, so the
# standing cat is scaled up to the same body size.
# The grooming drawing on the sheet is ~10% smaller than the sitting one.
POSE_SCALE = {"stand": 1.15, "sit": 0.92, "groom": 1.0, "lie": 0.78, "sleep": 0.70}
# Where the face is (fractions of the pose's bounding box) - left untouched when
# the stripes are softened, so eyes, nose and whiskers stay crisp.
HEAD_BOX = {"sit": (0.45, 0.0, 1.0, 0.36), "groom": (0.45, 0.0, 1.0, 0.34),
            "lie": (0.66, 0.05, 1.0, 0.95), "sleep": (0.62, 0.25, 1.0, 1.0)}


def soften_pattern(im, head_box, amount=0.55):
    """The sitting/lying/sleeping drawings have many thin dark stripes and ink
    outlines, while the walking cat has a few soft wide stripes. Pull dark marks
    part-way toward the surrounding coat colour so every pose reads as one cat."""
    a = np.array(im).astype(float)
    alpha = a[..., 3] / 255.0
    rgb = a[..., :3]
    w = ndimage.gaussian_filter(alpha, 5) + 1e-6
    coat = np.stack([ndimage.gaussian_filter(rgb[..., c] * alpha, 5) / w for c in range(3)], -1)
    lum = rgb.mean(-1)
    coat_lum = coat.mean(-1)
    dark = np.clip((coat_lum - lum - 12) / 45.0, 0, 1) * amount
    H, W = alpha.shape
    x0, y0, x1, y1 = head_box
    dark[int(y0 * H):int(y1 * H), int(x0 * W):int(x1 * W)] = 0
    a[..., :3] = rgb + (coat - rgb) * dark[..., None]
    return Image.fromarray(np.clip(a, 0, 255).astype(np.uint8), "RGBA")


def shorten_legs(im, keep=0.6):
    """Squash the painted legs below the belly line (paws stay the same size),
    like the short, sturdy legs of the storybook reference cat."""
    a = np.array(im)
    m = a[:, :, 3] > 40
    H = m.shape[0]
    # belly line: first row in the lower half where the silhouette splits into legs
    r = m.astype(np.int8)
    runs = (np.diff(r, axis=1) == 1).sum(1) + r[:, 0]
    split = [y for y in range(int(H * 0.5), H) if runs[y] >= 3 and runs[y:y + 6].min() >= 2]
    if not split:
        return im
    belly = split[0] - 2
    paw_h = 7                                        # leave the paws unsquashed
    leg_h = H - belly - paw_h
    if leg_h < 10:
        return im
    top = im.crop((0, 0, im.width, belly))
    legs = im.crop((0, belly, im.width, H - paw_h))
    legs = legs.resize((im.width, max(1, round(leg_h * keep))), Image.LANCZOS)
    paws = im.crop((0, H - paw_h, im.width, H))
    out = Image.new("RGBA", (im.width, belly + legs.height + paw_h), (0, 0, 0, 0))
    out.paste(top, (0, 0))
    out.paste(legs, (0, belly))
    out.paste(paws, (0, belly + legs.height))
    return out


def make_lick_frames(src):
    """Grooming that actually reaches: the head is cut out above the neck, tilted
    chin-down and lowered until the mouth touches the raised paw; frames alternate
    tongue-on-paw and a small lift, like a cat licking its paw."""
    import math
    a0 = np.array(src)
    H0, W0 = a0.shape[:2]
    # work on a padded canvas so the tilted head is never clipped at the frame edge
    P = 30
    a = np.pad(a0, ((P, P), (P, P), (0, 0)))
    H, W = a.shape[:2]
    neck_y = int(0.317 * H0) + P
    pivot = (0.591 * W0 + P, 0.305 * H0 + P)        # back of the neck
    mouth = (0.864 * W0 + P, 0.274 * H0 + P)
    paw_top = (0.827 * W0 + P, 0.610 * H0 + P)
    yy, xx = np.mgrid[0:H, 0:W]
    head = a.copy()
    head[..., 3] = (head[..., 3] * np.clip((neck_y + 2 - yy) / 10.0, 0, 1)).astype(np.uint8)
    # body keeps a rounded shoulder line where the head used to be
    top = neck_y - 2 + 0.010 * (xx - pivot[0] + 8) ** 2
    body = a.copy()
    body[..., 3] = (body[..., 3] * np.clip((yy - top) / 2.5, 0, 1)).astype(np.uint8)
    from PIL import ImageDraw

    def frame(angle, dip, tongue):
        t = math.radians(angle)
        hd = Image.fromarray(head).rotate(-angle, center=pivot, resample=Image.BICUBIC)
        mx, my = mouth[0] - pivot[0], mouth[1] - pivot[1]
        mr = (pivot[0] + mx * math.cos(t) - my * math.sin(t), pivot[1] + mx * math.sin(t) + my * math.cos(t))
        out = Image.fromarray(body)
        layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        layer.alpha_composite(hd, (int(round(paw_top[0] - mr[0])), int(round(paw_top[1] - dip - mr[1]))))
        out.alpha_composite(layer)
        if tongue:
            d = ImageDraw.Draw(out)
            tx, ty = paw_top[0] - 1, paw_top[1] - dip + 1
            d.ellipse([tx - 2.5, ty - 0.5, tx + 2.5, ty + 3.5], fill=(222, 140, 138, 255),
                      outline=(170, 95, 92, 255))
        return trim(out)
    return [frame(32, 2, True), frame(28, 7, False), frame(34, 1, True), frame(28, 7, False)]


# ----------------------------------------------------------------------
# Walk rig: remove all four painted legs; the game draws them with a real gait
# ----------------------------------------------------------------------
def clean_underside(a):
    """After the painted legs are cut away, the belly still carries leg stubs:
    small spikes hanging below the silhouette and dark brush strokes where the legs
    overlapped the chest and rump. Smooth the bottom edge into one clean curve and
    paint the stubs over with the surrounding coat colour."""
    a = a.copy()
    H, W = a.shape[:2]
    m = a[:, :, 3] > 40
    yy = np.arange(H)[:, None]
    bottom = np.where(m.any(0), (m * yy).max(0), -1).astype(float)
    cols = np.where(bottom > H * 0.55)[0]
    if len(cols) < 10:
        return a
    prof = bottom[cols]
    # morphological opening from below: removes downward bumps narrower than ~20px
    smooth = ndimage.grey_opening(prof, size=21)
    smooth = ndimage.uniform_filter1d(smooth, 7)
    line = np.full(W, np.inf)
    line[cols] = np.minimum(prof, smooth + 0.5)
    alpha = a[:, :, 3].astype(float)
    lower = yy > H * 0.55
    clip = np.clip((line[None, :] + 1.0 - yy) / 1.5, 0, 1)
    a[:, :, 3] = np.where(lower, alpha * clip, alpha).astype(np.uint8)
    # dark leg remnants near the underside -> coat colour
    al = a[:, :, 3] / 255.0
    rgb = a[:, :, :3].astype(float)
    w = ndimage.gaussian_filter(al, 7) + 1e-6
    coat = np.stack([ndimage.gaussian_filter(rgb[..., c] * al, 7) / w for c in range(3)], -1)
    # coat estimate without the dark marks themselves: take the brighter of local and wider coat
    coat2 = np.stack([ndimage.gaussian_filter(rgb[..., c] * al, 14) / (ndimage.gaussian_filter(al, 14) + 1e-6)
                      for c in range(3)], -1)
    coat = np.where(coat2.mean(-1, keepdims=True) > coat.mean(-1, keepdims=True), coat2, coat)
    # both dark strokes and the pale outlines next to them
    diff = coat.mean(-1) - rgb.mean(-1)
    dark = np.clip((diff - 8) / 25.0, 0, 1) + np.clip((-diff - 6) / 18.0, 0, 1)
    band = (yy > line[None, :] - 30) & lower
    band = ndimage.binary_dilation(band, iterations=1)
    dark = ndimage.gaussian_filter(dark * band, 1.0)
    rgb = rgb + (coat - rgb) * np.clip(dark * 1.4, 0, 1)[..., None]
    # faint brush outlines of the old legs: a median filter wipes thin strokes but keeps
    # the watercolour mottling; fade it in only along the underside
    med = np.stack([ndimage.median_filter(rgb[..., c], size=7) for c in range(3)], -1)
    fine = rgb - np.stack([ndimage.gaussian_filter(rgb[..., c], 0.8) for c in range(3)], -1)
    zone = ndimage.gaussian_filter(((yy > line[None, :] - 26) & lower).astype(float), 3)[..., None]
    rgb = rgb + (med + fine * 0.6 - rgb) * zone
    a[:, :, :3] = np.clip(rgb, 0, 255).astype(np.uint8)
    return a


# Front legs in the sitting / grooming drawings (fractions of each saved canvas):
# (x0, x1, y_fade_start, y_full, y1). Those drawings paint the legs in the orange-tan
# coat; the idling and walking cat have grey-brown legs, so these get recoloured.
LEG_REGIONS = {
    "sit_01": [(0.50, 0.78, 0.64, 0.80, 1.0)],
    "sit_02": [(0.47, 0.77, 0.62, 0.78, 1.0)],
    "groom_01": [(0.55, 0.78, 0.52, 0.66, 0.93), (0.68, 0.82, 0.50, 0.56, 0.64)],
}
for _k in ("groom_02", "groom_03", "groom_04"):
    LEG_REGIONS[_k] = LEG_REGIONS["groom_01"]


def recolour_legs(name, regions, idle, strength=0.85):
    """Pull the leg columns of a saved pose toward the idle cat's leg colours: coat
    colour where the leg leaves the body, grey-brown toward the paw. Works in Lab and
    keeps each pixel's offset from the local mean, so brush texture and the ink
    outlines survive."""
    path = OUT / f"{name}.png"
    if not path.exists():
        return
    a = np.array(Image.open(path).convert("RGBA")).astype(float)
    H, W = a.shape[:2]
    yy, xx = np.mgrid[0:H, 0:W]
    mask = np.zeros((H, W))
    for x0, x1, ys, yf, y1 in regions:
        box = (xx >= x0 * W) & (xx <= x1 * W) & (yy <= y1 * H)
        ramp = np.clip((yy - ys * H) / max(1.0, (yf - ys) * H), 0, 1)
        mask = np.maximum(mask, box * ramp)
    mask = ndimage.gaussian_filter(mask, 2.0) * (a[..., 3] > 0) * strength
    if mask.max() <= 0:
        return
    lab = _rgb2lab(a[..., :3])
    sel = mask > 0.4
    m = lab[sel].mean(0)
    # target: idle 'top' colour higher up, 'mid'/'bot' toward the paw
    t_top, t_mid = _rgb2lab(np.array(idle["top"], float)), _rgb2lab(np.array(idle["mid"], float))
    depth = np.clip((yy / H - 0.6) / 0.35, 0, 1)[..., None]
    target = t_top * (1 - depth) + t_mid * depth
    new = (lab - m) * np.array([0.85, 0.6, 0.6]) + target
    lab = lab + (new - lab) * mask[..., None]
    a[..., :3] = _lab2rgb(lab)
    Image.fromarray(np.clip(a, 0, 255).astype(np.uint8), "RGBA").save(path)


def steady_colours(prefix, sigma=6.0):
    """The sleep drawings are separate paintings, so their overall tone and the
    position of the watercolour blotches differ from frame to frame and the coat
    seems to flicker while the cat breathes. Give every frame the same broad colour
    wash (the alpha-weighted average of all frames' blurred colours) and keep only
    each frame's own fine detail (eyes, whiskers, outlines)."""
    paths = sorted(OUT.glob(f"{prefix}_*.png"))
    if len(paths) < 2:
        return
    arrs = [np.array(Image.open(q).convert("RGBA")).astype(float) for q in paths]
    blur = lambda x: ndimage.gaussian_filter(x, sigma)
    lows, weights = [], []
    for a in arrs:
        al = a[..., 3] / 255.0
        w = blur(al) + 1e-6
        lows.append(np.stack([blur(a[..., c] * al) / w for c in range(3)], -1))
        weights.append(w)
    W = sum(weights)
    shared = sum(l * w[..., None] for l, w in zip(lows, weights)) / W[..., None]
    for q, a, low in zip(paths, arrs, lows):
        a[..., :3] = np.clip(a[..., :3] - low + shared, 0, 255)
        Image.fromarray(a.astype(np.uint8), "RGBA").save(q)


def idle_leg_colours(stand_frames):
    """Leg colours of the idling cat (below the belly line): top / middle / bottom
    medians of the visible legs, plus the shadowed far-leg tone. The walking cat's
    drawn legs use these so both poses share one fur colour."""
    tops, mids, bots, darks = [], [], [], []
    for im in stand_frames:
        a = np.array(im).astype(int)
        m = a[:, :, 3] > 200
        H = int(np.where(m.any(1))[0].max()) + 1
        r = m.astype(np.int8)
        runs = (np.diff(r, axis=1) == 1).sum(1) + r[:, 0]
        split = [y for y in range(int(H * 0.5), H) if runs[y] >= 3]
        if not split:
            continue
        b = split[0]
        L = H - b
        px = lambda y0, y1: a[y0:y1][m[y0:y1]][:, :3]
        tops.append(np.median(px(b, b + L // 3), 0))
        mids.append(np.median(px(b + L // 3, b + 2 * L // 3), 0))
        bots.append(np.median(px(b + 2 * L // 3, H), 0))
        darks.append(np.percentile(px(b, H), 15, axis=0))
    if not tops:
        return None
    med = lambda v: [int(c) for c in np.median(v, 0)]
    return {"top": med(tops), "mid": med(mids), "bot": med(bots), "dark": med(darks)}


def rig_walk_frames(idle_leg=None):
    """Removes the painted legs from the walk frames so the game can animate all
    four legs with a coordinated gait (near legs in front, darker far legs behind,
    planted paws). Writes walk_rig.json with hip/shoulder positions per frame and
    leg colours sampled from the art."""
    import json
    R = 15
    yy, xx = np.mgrid[-R:R + 1, -R:R + 1]
    disk = (xx ** 2 + yy ** 2) <= R * R
    hips, shoulders, leg_top, leg_bot, haunch_px, chest_px = [], [], [], [], [], []
    paths = sorted(OUT.glob("walk_*.png"))
    arrays = [np.array(Image.open(p).convert("RGBA")) for p in paths]
    H, W = arrays[0].shape[:2]
    MARGIN = 12
    for path, a in zip(paths, arrays):
        m = a[:, :, 3] > 40
        opened = ndimage.binary_opening(np.pad(m, R + 1), structure=disk)[R + 1:-R - 1, R + 1:-R - 1]
        region = np.zeros_like(m)
        region[int(H * 0.62):, :] = True
        body = np.where(region, opened, m)
        lab, n = ndimage.label(body)
        if n > 1:
            sizes = ndimage.sum(body, lab, range(1, n + 1))
            body = lab == (int(np.argmax(sizes)) + 1)
        # round off the rump and chest corners where the legs were cut away, so the
        # rear curves smoothly into the haunch (and leftover dark leg tops disappear)
        band0 = body[int(H * 0.55):int(H * 0.66)]
        bx = np.where(band0.any(0))[0]
        bl, br = bx.min(), bx.max()
        R2 = 24
        yy2, xx2 = np.mgrid[-R2:R2 + 1, -R2:R2 + 1]
        disk2 = (xx2 ** 2 + yy2 ** 2) <= R2 * R2
        rounded = ndimage.binary_opening(np.pad(body, R2 + 1), structure=disk2)[R2 + 1:-R2 - 1, R2 + 1:-R2 - 1]
        corners = np.zeros_like(body)
        corners[int(H * 0.60):, :int(bl + 0.32 * (br - bl))] = True    # rump
        corners[int(H * 0.66):, int(bl + 0.72 * (br - bl)):] = True    # chest
        body = np.where(corners, rounded & body, body)
        lab, n = ndimage.label(body)
        if n > 1:
            sizes = ndimage.sum(body, lab, range(1, n + 1))
            body = lab == (int(np.argmax(sizes)) + 1)
        legs = m & ~body
        ys = np.where(legs)[0]
        if len(ys):
            lo, hi = ys.min(), ys.max()
            span = hi - lo + 1
            t = legs.copy(); t[lo + span // 3:] = False
            b = legs.copy(); b[:hi - span // 3] = False
            leg_top.append(a[t][:, :3]); leg_bot.append(a[b][:, :3])
        a = a.copy()
        a[:, :, 3] = np.where(body, a[:, :, 3], 0)
        a = clean_underside(a)
        # re-centre the legless body so it does not jitter sideways between frames
        cols = (a[:, :, 3] > 40).sum(0).astype(float)
        cx = (cols * np.arange(W)).sum() / cols.sum()
        shift = int(round(W / 2 - cx))
        canvas = np.zeros((H, W + 2 * MARGIN, 4), np.uint8)
        canvas[:, MARGIN + shift:MARGIN + shift + W] = a
        bm = canvas[:, :, 3] > 40
        # hip and shoulder sit at fixed fractions along the lower torso
        band = bm[int(H * 0.55):int(H * 0.66)]
        xs = np.where(band.any(0))[0]
        left, right = xs.min(), xs.max()
        def attach(frac, drop):
            x = int(left + frac * (right - left))
            col = np.where(bm[:, x])[0]
            return x, int(col.max()) - drop, int(col.max())
        hx, hy, hb = attach(0.17, 17)
        sx, sy, sb = attach(0.76, 11)
        hips.append([hx, hy]); shoulders.append([sx, sy])
        haunch_px.append(canvas[hb - 14:hb - 4, hx - 8:hx + 8][..., :3].reshape(-1, 3))
        chest_px.append(canvas[sb - 12:sb - 3, sx - 8:sx + 8][..., :3].reshape(-1, 3))
        Image.fromarray(canvas).save(path)
    med = lambda arrs: [int(v) for v in np.median(np.concatenate(arrs), axis=0)]
    rig = {
        "ground": H - 2,
        "hips": hips,
        "shoulders": shoulders,
        "thigh": med(leg_top),
        "paw": med(leg_bot),
        "body": med(haunch_px),
        "chest": med(chest_px),
    }
    if idle_leg:
        rig["idle_leg"] = idle_leg
    (OUT / "walk_rig.json").write_text(json.dumps(rig, indent=1))
    print(f"walk: 4 legs rigged ({len(hips)} frames); leg colours {rig['thigh']} -> {rig['paw']}")


if __name__ == "__main__":
    main()
