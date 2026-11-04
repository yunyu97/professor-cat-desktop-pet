"""
Builds cat_sprites_life/ for ProfessorCatLife.py.

* Takes the walk / idle / sleep frames from cat_sprites_real/.
* Cuts the sitting, grooming and lying poses out of the Gemini sprite sheet.
* Normalizes everything: one shared pixel scale, every frame anchored at the
  same ground point (paws at the bottom, body mass centred), so frames don't
  jump or change size during animation.

Usage:  python prepare_life_sprites.py
"""
import io
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
            frames["groom"] = [rs(cut(11)), rs(cut(12))]
            frames["lie"] = [rs(cut(15))]
        else:
            print("Could not detect all 16 cats on the sheet; skipping sit/groom/lie poses")

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
    rig_walk_frames()
    print(f"Done. Sprites written to {OUT}")


def rig_walk_frames():
    """Remove the painted hind legs from the walk frames so the game can draw them
    itself (near leg in front, darker far leg behind, planted paws). Writes
    walk_rig.json with the hip position per frame and colours sampled from the art."""
    import json
    R, Y0, REAR_X = 16, 100, 105
    yy, xx = np.mgrid[-R:R + 1, -R:R + 1]
    disk = (xx ** 2 + yy ** 2) <= R * R
    hips, leg_px_top, leg_px_bot, body_px = [], [], [], []
    i = 1
    while (OUT / f"walk_{i:02d}.png").exists():
        path = OUT / f"walk_{i:02d}.png"
        a = np.array(Image.open(path).convert("RGBA"))
        m = a[:, :, 3] > 40
        H = m.shape[0]
        region = np.zeros_like(m)
        region[Y0:, :REAR_X] = True
        opened = ndimage.binary_opening(np.pad(m, R + 1), structure=disk)[R + 1:-R - 1, R + 1:-R - 1]
        body = np.where(region, opened, m)
        lab, n = ndimage.label(body)
        if n > 1:
            sizes = ndimage.sum(body, lab, range(1, n + 1))
            body = lab == (int(np.argmax(sizes)) + 1)
        legs = m & ~body & region
        # sample colours: thigh (upper leg) and paw (lower leg)
        ys = np.where(legs)[0]
        if len(ys):
            lo, hi = ys.min(), ys.max()
            span = hi - lo + 1
            top = legs.copy(); top[lo + span // 3:] = False
            bot = legs.copy(); bot[:hi - span // 3] = False
            leg_px_top.append(a[top][:, :3])
            leg_px_bot.append(a[bot][:, :3])
        hip_x = 52
        col = np.where(body[:, hip_x])[0]
        bottom = int(col.max()) if len(col) else H - 45
        body_px.append(a[max(0, bottom - 14):bottom - 4, hip_x - 8:hip_x + 8][..., :3].reshape(-1, 3))
        hips.append([hip_x, bottom - 15])
        a[:, :, 3] = np.where(body, a[:, :, 3], 0)
        Image.fromarray(a).save(path)
        i += 1
    if not hips:
        return
    med = lambda arrs: [int(v) for v in np.median(np.concatenate(arrs), axis=0)]
    rig = {
        "ground": int(np.array(Image.open(OUT / "walk_01.png")).shape[0]) - 2,
        "hips": hips,
        "thigh": med(leg_px_top),
        "paw": med(leg_px_bot),
        "body": med(body_px),
    }
    (OUT / "walk_rig.json").write_text(json.dumps(rig, indent=1))
    print(f"walk: hind legs rigged ({len(hips)} frames), colours {rig['thigh']} -> {rig['paw']}")


if __name__ == "__main__":
    main()
