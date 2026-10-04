#!/usr/bin/env python3
"""Render a short, original Samsung Galaxy concept spot from procedural motion graphics.

Run from the repository root after installing requirements.txt:
    python scripts/render_ad.py

The generated phone and component illustrations are concept artwork, not a depiction of
any particular Samsung model or manufacturing process.
"""
from __future__ import annotations

import argparse
import math
import os
import random
import subprocess
import tempfile
import wave
from functools import lru_cache
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont
import imageio_ffmpeg

ROOT = Path(__file__).resolve().parents[1]
WIDTH, HEIGHT = 1920, 1080
FPS = 24
DURATION = 22.5
FRAME_COUNT = int(round(FPS * DURATION))
TRANSITION = 0.48
SCENE_BOUNDS = (0.0, 4.0, 8.1, 12.5, 16.7, DURATION)
OUT_DEFAULT = ROOT / "samsung-galaxy-precision-ad.mp4"
POSTER_DEFAULT = ROOT / "samsung-galaxy-precision-ad-poster.jpg"
FONT_JP_PATH = ROOT / "assets" / "ipaexg.ttf"
FONT_LATIN_PATH = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
FONT_LATIN_BOLD_PATH = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"

WHITE = (241, 247, 255)
SUBTLE = (169, 190, 211)
CYAN = (96, 221, 237)
GOLD = (221, 175, 92)


def clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def smooth(value: float) -> float:
    value = clamp(value)
    return value * value * (3.0 - 2.0 * value)


def blend(a: Image.Image, b: Image.Image, amount: float) -> Image.Image:
    return Image.blend(a, b, clamp(amount))


def font_jp(size: int) -> ImageFont.FreeTypeFont:
    if FONT_JP_PATH.exists():
        return ImageFont.truetype(str(FONT_JP_PATH), size=size)
    # Useful fallback for machines with an installed Japanese system font.
    for candidate in (
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
        "/System/Library/Fonts/ヒラギノ角ゴシック W3.ttc",
    ):
        if Path(candidate).exists():
            return ImageFont.truetype(candidate, size=size)
    raise FileNotFoundError(
        "Japanese font not found. Keep assets/ipaexg.ttf in the repository, or install a Japanese sans-serif font."
    )


def font_lat(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    path = FONT_LATIN_BOLD_PATH if bold else FONT_LATIN_PATH
    if not Path(path).exists():
        return ImageFont.load_default()
    return ImageFont.truetype(path, size=size)


def make_background(base: tuple[int, int, int], glows: list[tuple[float, float, float, tuple[int, int, int], float]], seed: int) -> Image.Image:
    """Create a subtly textured, vignetted cinematic background."""
    yy, xx = np.mgrid[0:HEIGHT, 0:WIDTH].astype(np.float32)
    pixels = np.empty((HEIGHT, WIDTH, 3), dtype=np.float32)
    pixels[:, :, 0] = base[0]
    pixels[:, :, 1] = base[1]
    pixels[:, :, 2] = base[2]
    for cx, cy, spread, color, strength in glows:
        distance = ((xx - cx) ** 2 + (yy - cy) ** 2) / (spread * spread)
        weight = np.exp(-distance * 1.8) * strength
        for channel in range(3):
            pixels[:, :, channel] += (color[channel] - base[channel]) * weight
    # Soft vignette keeps the typography legible and gives the product space to breathe.
    vx = (xx - WIDTH * 0.5) / (WIDTH * 0.78)
    vy = (yy - HEIGHT * 0.5) / (HEIGHT * 0.82)
    vignette = np.clip(1.0 - 0.16 * (vx * vx + vy * vy), 0.69, 1.0)
    pixels *= vignette[:, :, None]
    rng = np.random.default_rng(seed)
    grain = rng.normal(0.0, 1.05, (HEIGHT, WIDTH, 1)).astype(np.float32)
    pixels += grain
    arr = np.uint8(np.clip(pixels, 0, 255))
    image = Image.fromarray(arr, "RGB").convert("RGBA")

    # Fine calibrated grid and drafting marks; subdued enough not to fight the copy.
    overlay = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    for x in range(72, WIDTH, 150):
        draw.line((x, 0, x, HEIGHT), fill=(66, 125, 151, 12), width=1)
    for y in range(74, HEIGHT, 150):
        draw.line((0, y, WIDTH, y), fill=(66, 125, 151, 11), width=1)
    for x in range(75, WIDTH, 150):
        for y in range(77, HEIGHT, 150):
            draw.ellipse((x - 1, y - 1, x + 1, y + 1), fill=(120, 196, 211, 20))
    # A few cropped technical arcs around the outer field.
    draw.arc((WIDTH - 575, -210, WIDTH + 170, 535), 15, 230, fill=(60, 145, 168, 29), width=2)
    draw.arc((WIDTH - 485, -120, WIDTH + 75, 440), 12, 228, fill=(60, 145, 168, 20), width=1)
    draw.line((90, 82, 400, 82), fill=(91, 208, 221, 55), width=1)
    image.alpha_composite(overlay)
    return image


@lru_cache(maxsize=64)
def glow_sprite(radius: int, color: tuple[int, int, int], peak_alpha: int = 92) -> Image.Image:
    radius = max(2, int(radius))
    size = radius * 2 + 1
    yy, xx = np.mgrid[0:size, 0:size].astype(np.float32)
    rr = ((xx - radius) ** 2 + (yy - radius) ** 2) / (radius * radius)
    alpha = np.uint8(np.clip(peak_alpha * np.exp(-rr * 5.5), 0, 255))
    rgb = np.empty((size, size, 4), dtype=np.uint8)
    rgb[:, :, 0] = color[0]
    rgb[:, :, 1] = color[1]
    rgb[:, :, 2] = color[2]
    rgb[:, :, 3] = alpha
    return Image.fromarray(rgb, "RGBA")


def paste_glow(image: Image.Image, center: tuple[float, float], radius: int, color: tuple[int, int, int], peak_alpha: int = 92) -> None:
    sprite = glow_sprite(int(radius), color, peak_alpha)
    x = int(center[0] - radius)
    y = int(center[1] - radius)
    image.alpha_composite(sprite, (x, y))


def paste_center(image: Image.Image, layer: Image.Image, center: tuple[float, float], scale: float = 1.0) -> None:
    if scale != 1.0:
        size = (max(1, int(layer.width * scale)), max(1, int(layer.height * scale)))
        layer = layer.resize(size, Image.Resampling.LANCZOS)
    image.alpha_composite(layer, (int(center[0] - layer.width / 2), int(center[1] - layer.height / 2)))


def draw_tracked(draw: ImageDraw.ImageDraw, text: str, xy: tuple[float, float], font: ImageFont.FreeTypeFont,
                 fill: tuple[int, int, int, int], tracking: float = 2.0) -> None:
    x, y = xy
    for character in text:
        draw.text((round(x), round(y)), character, font=font, fill=fill)
        x += draw.textlength(character, font=font) + tracking


def draw_headline(image: Image.Image, index: int, eyebrow: str, main: str, sub: str, progress: float) -> None:
    alpha = int(255 * smooth(progress / 0.16))
    if alpha <= 0:
        return
    lift = int((1.0 - smooth(progress / 0.18)) * 13)
    layer = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    x = 150
    y = 355 + lift
    draw_tracked(d, eyebrow.upper(), (x, y), font_lat(21, True), (100, 219, 232, alpha), 3.2)
    d.line((x, y + 45, x + 128, y + 45), fill=(79, 197, 214, int(alpha * 0.73)), width=2)
    d.text((x - 1, y + 88), main, font=font_jp(59), fill=(WHITE[0], WHITE[1], WHITE[2], alpha),
           stroke_width=1, stroke_fill=(4, 16, 27, int(alpha * 0.72)))
    d.text((x, y + 181), sub, font=font_jp(37), fill=(SUBTLE[0], SUBTLE[1], SUBTLE[2], alpha),
           stroke_width=1, stroke_fill=(4, 16, 27, int(alpha * 0.5)))
    # Minimal progress motif: five hairline segments, with the current stage lit.
    bar_y = 862
    bar_width = 394
    gap = 9
    segment = (bar_width - 4 * gap) / 5
    for step in range(5):
        bx = x + step * (segment + gap)
        intensity = 0.95 if step == index else (0.26 if step < index else 0.12)
        d.rounded_rectangle((bx, bar_y, bx + segment, bar_y + 3), radius=1,
                            fill=(93, 208, 223, int(alpha * intensity)))
    draw_tracked(d, f"0{index + 1}  /  05", (x, bar_y + 22), font_lat(15), (145, 169, 189, int(alpha * 0.75)), 2.2)
    image.alpha_composite(layer)


def rounded_gradient(size: tuple[int, int], top: tuple[int, int, int], bottom: tuple[int, int, int], radius: int,
                     horizontal: bool = False) -> Image.Image:
    w, h = size
    count = w if horizontal else h
    a = np.asarray(top, dtype=np.float32)
    b = np.asarray(bottom, dtype=np.float32)
    ramp = np.linspace(0.0, 1.0, count, dtype=np.float32)[:, None]
    colors = a[None, :] * (1.0 - ramp) + b[None, :] * ramp
    if horizontal:
        arr = np.repeat(colors[None, :, :], h, axis=0)
    else:
        arr = np.repeat(colors[:, None, :], w, axis=1)
    rgba = np.zeros((h, w, 4), dtype=np.uint8)
    rgba[:, :, :3] = np.uint8(np.clip(arr, 0, 255))
    mask = Image.new("L", (w, h), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, w - 1, h - 1), radius=radius, fill=255)
    rgba[:, :, 3] = np.asarray(mask)
    return Image.fromarray(rgba, "RGBA")


def build_chip_art() -> Image.Image:
    w, h = 850, 710
    chip = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    shadow = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    sd = ImageDraw.Draw(shadow)
    sd.rounded_rectangle((74, 63, 788, 665), radius=35, fill=(0, 0, 0, 185))
    chip.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(22)), (0, 8))
    d = ImageDraw.Draw(chip)
    d.rounded_rectangle((55, 35, 792, 650), radius=34, fill=(30, 43, 56, 255), outline=(121, 158, 179, 255), width=3)
    d.rounded_rectangle((75, 55, 772, 630), radius=28, fill=(8, 17, 29, 255), outline=(50, 79, 97, 255), width=3)
    # A dense but controlled perimeter of package contacts.
    for x in range(108, 742, 29):
        for yy in (69, 604):
            d.rounded_rectangle((x, yy, x + 15, yy + 27), radius=4, fill=(174, 145, 91, 255))
            d.line((x + 3, yy + 4, x + 12, yy + 4), fill=(241, 209, 143, 255), width=2)
    for y in range(105, 590, 29):
        for xx in (80, 741):
            d.rounded_rectangle((xx, y, xx + 28, y + 14), radius=4, fill=(164, 136, 88, 255))
    d.rounded_rectangle((112, 91, 730, 588), radius=18, fill=(18, 29, 42, 255), outline=(85, 131, 151, 255), width=2)
    # Silicon surface and fine routing.
    silicon = rounded_gradient((568, 438), (23, 48, 64), (8, 17, 30), 22, horizontal=True)
    chip.alpha_composite(silicon, (141, 121))
    d = ImageDraw.Draw(chip)
    d.rounded_rectangle((141, 121, 709, 559), radius=22, outline=(42, 188, 205, 235), width=2)
    # Repeated traces terminate at a clean central die; a few amber vias add material contrast.
    for n in range(9):
        y = 156 + n * 12
        xend = 293 + (n % 3) * 15
        d.line(((157, y), (xend, y), (xend, 215)), fill=(39, 139 + n % 3 * 12, 164, 190), width=2)
        d.ellipse((xend - 3, y - 3, xend + 3, y + 3), fill=(114, 218, 229, 220))
        y2 = 509 - n * 12
        d.line(((698, y2), (560 - (n % 3) * 13, y2), (560 - (n % 3) * 13, 470)), fill=(38, 133, 156, 195), width=2)
    for n in range(8):
        x = 180 + n * 17
        d.line(((x, 135), (x, 220)), fill=(47, 122, 144, 180), width=2)
        d.line(((694 - n * 17, 460), (694 - n * 17, 545)), fill=(45, 121, 145, 180), width=2)
    d.rounded_rectangle((282, 203, 568, 483), radius=14, fill=(5, 14, 26, 255), outline=(112, 207, 218, 255), width=3)
    d.rounded_rectangle((300, 221, 550, 465), radius=8, fill=(15, 31, 45, 255), outline=(36, 103, 125, 255), width=2)
    # Core grid and luminous die boundaries.
    for x in range(318, 541, 19):
        d.line((x, 236, x, 448), fill=(39, 88, 106, 120), width=1)
    for y in range(237, 449, 19):
        d.line((313, y, 537, y), fill=(39, 88, 106, 120), width=1)
    for row in range(4):
        for col in range(4):
            bx = 333 + col * 46
            by = 252 + row * 43
            d.rounded_rectangle((bx, by, bx + 31, by + 26), radius=4,
                                fill=(18 + col * 2, 68 + row * 4, 85 + col * 3, 230),
                                outline=(71, 168, 187, 180), width=1)
            d.ellipse((bx + 12, by + 9, bx + 17, by + 14), fill=(163, 230, 237, 220))
    d.text((116, 80), "SILICON / PROCESSOR", font=font_lat(16, True), fill=(176, 210, 221, 245))
    d.text((596, 531), "CORE ARRAY", font=font_lat(12, True), fill=(115, 174, 191, 230))
    d.line((135, 573, 707, 573), fill=(55, 87, 104, 190), width=1)
    # Fine corner gleams.
    for x, y in ((95, 75), (749, 75), (95, 607), (749, 607)):
        d.ellipse((x - 4, y - 4, x + 4, y + 4), fill=(235, 198, 123, 255))
    return chip.rotate(-8.0, resample=Image.Resampling.BICUBIC, expand=True)


def draw_component(draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int], label: str = "", accent: tuple[int, int, int] = (88, 206, 216)) -> None:
    x1, y1, x2, y2 = box
    draw.rounded_rectangle((x1 + 6, y1 + 8, x2 + 6, y2 + 8), radius=13, fill=(1, 7, 14, 180))
    draw.rounded_rectangle(box, radius=12, fill=(14, 24, 30, 255), outline=(94, 121, 119, 255), width=2)
    draw.rounded_rectangle((x1 + 7, y1 + 7, x2 - 7, y2 - 7), radius=8,
                           fill=(21, 37, 39, 255), outline=(39, 78, 79, 255), width=1)
    # shallow metallic edge and package bond pads
    for x in range(x1 + 15, x2 - 12, 18):
        draw.line((x, y1 - 4, x, y1 + 5), fill=(190, 153, 90, 255), width=2)
        draw.line((x, y2 - 5, x, y2 + 4), fill=(190, 153, 90, 255), width=2)
    for y in range(y1 + 15, y2 - 12, 18):
        draw.line((x1 - 4, y, x1 + 5, y), fill=(190, 153, 90, 255), width=2)
        draw.line((x2 - 5, y, x2 + 4, y), fill=(190, 153, 90, 255), width=2)
    inner = (x1 + 18, y1 + 15, x2 - 18, y2 - 15)
    draw.rounded_rectangle(inner, radius=5, outline=(*accent, 205), width=2)
    for yy in range(y1 + 25, y2 - 16, 13):
        draw.line((x1 + 24, yy, x2 - 23, yy), fill=(39, 99, 98, 120), width=1)
    if label:
        f = font_lat(12, True)
        draw.text((x1 + 18, y2 - 32), label, font=f, fill=(178, 205, 194, 240))


def build_board_art() -> Image.Image:
    w, h = 1190, 810
    board = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(board)
    shape = [(39, 178), (994, 52), (1154, 619), (155, 764)]
    shadow = [(x + 13, y + 18) for x, y in shape]
    d.polygon(shadow, fill=(0, 2, 6, 195))
    d.polygon(shape, fill=(14, 42, 41, 255), outline=(105, 147, 125, 255))
    d.line(shape + [shape[0]], fill=(126, 166, 133, 255), width=4)
    inset = [(70, 191), (984, 78), (1129, 602), (175, 735)]
    d.line(inset + [inset[0]], fill=(44, 91, 75, 255), width=2)
    # The circuitry is laid in orthogonal runs with rounded vias and muted gold/cyan lanes.
    routes = [
        [(415, 365), (312, 365), (312, 250), (170, 250)],
        [(438, 390), (287, 390), (287, 559), (204, 559)],
        [(708, 322), (831, 322), (831, 220), (1003, 220)],
        [(710, 393), (846, 393), (846, 522), (1054, 522)],
        [(585, 237), (585, 159), (744, 159), (744, 102)],
        [(533, 498), (533, 600), (387, 600), (387, 681)],
        [(676, 485), (747, 485), (747, 641), (951, 641)],
        [(419, 301), (359, 301), (359, 183), (270, 183)],
        [(700, 281), (765, 281), (765, 189), (912, 189)],
        [(478, 496), (478, 536), (315, 536), (315, 671)],
        [(640, 496), (640, 566), (774, 566), (774, 681)],
        [(387, 405), (238, 405), (238, 464), (139, 464)],
        [(735, 360), (916, 360), (916, 433), (1075, 433)],
        [(502, 211), (502, 147), (419, 147), (419, 101)],
        [(724, 432), (788, 432), (788, 594), (1001, 594)],
        [(413, 337), (334, 337), (334, 288), (235, 288)],
        [(715, 302), (886, 302), (886, 264), (1063, 264)],
        [(554, 499), (554, 658), (672, 658), (672, 703)],
        [(392, 441), (267, 441), (267, 624), (179, 624)],
        [(694, 454), (958, 454), (958, 552), (1100, 552)],
    ]
    for i, route in enumerate(routes):
        d.line(route, fill=(3, 17, 20, 255), width=9, joint="curve")
        hue = (65, 180, 185, 205) if i % 3 != 0 else (196, 151, 76, 205)
        d.line(route, fill=hue, width=3, joint="curve")
        for x, y in (route[0], route[-1]):
            d.ellipse((x - 6, y - 6, x + 6, y + 6), fill=(15, 30, 31, 255), outline=(182, 151, 91, 245), width=2)
            d.ellipse((x - 2, y - 2, x + 2, y + 2), fill=(112, 217, 211, 240))
    # Main application package with dense die detail.
    draw_component(d, (414, 282, 729, 488), "APPLICATION PROCESSOR", (67, 198, 207))
    d.rounded_rectangle((474, 329, 668, 425), radius=8, fill=(6, 17, 25, 255), outline=(94, 188, 192, 245), width=2)
    for x in range(489, 658, 13):
        d.line((x, 338, x, 417), fill=(39, 97, 103, 180), width=1)
    for y in range(344, 414, 11):
        d.line((481, y, 661, y), fill=(39, 97, 103, 150), width=1)
    d.text((512, 365), "SILICON", font=font_lat(15, True), fill=(191, 228, 224, 255))
    draw_component(d, (152, 211, 305, 315), "MEMORY", (94, 186, 184))
    draw_component(d, (190, 493, 348, 589), "POWER", (204, 162, 91))
    draw_component(d, (844, 179, 1009, 286), "IMAGE SENSOR", (88, 194, 210))
    draw_component(d, (893, 488, 1042, 585), "RF / CONNECTIVITY", (167, 183, 117))
    draw_component(d, (370, 611, 515, 690), "PMIC", (198, 161, 93))
    draw_component(d, (775, 626, 915, 706), "STORAGE", (92, 179, 183))
    # Scattered capacitors and test pads make the substrate read as a populated PCB.
    rng = random.Random(9271)
    for _ in range(70):
        x = rng.randint(100, 1080)
        y = rng.randint(190, 700)
        if 395 < x < 740 and 260 < y < 505:
            continue
        if (150 < x < 350 and 200 < y < 325) or (840 < x < 1050 and 170 < y < 300):
            continue
        ww, hh = rng.choice((8, 11, 14)), rng.choice((6, 8, 10))
        c = rng.choice(((103, 137, 111, 235), (166, 139, 87, 230), (67, 120, 123, 235)))
        d.rounded_rectangle((x, y, x + ww, y + hh), radius=2, fill=(20, 28, 25, 255), outline=c, width=1)
    for i in range(4):
        d.ellipse((83 + i * 22, 672, 94 + i * 22, 683), fill=(10, 20, 20, 255), outline=(198, 157, 91, 230), width=2)
    d.text((86, 149), "MULTI-LAYER BOARD", font=font_lat(15, True), fill=(172, 203, 185, 240))
    return board


def make_phone(front: bool, width: int = 460, height: int = 920) -> Image.Image:
    phone = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    d = ImageDraw.Draw(phone)
    outer = (18, 10, width - 18, height - 10)
    # A thin satin titanium edge catches light on the silhouette.
    d.rounded_rectangle((outer[0] - 2, outer[1] - 2, outer[2] + 2, outer[3] + 2), radius=64,
                        fill=(17, 22, 31, 255), outline=(174, 187, 201, 230), width=3)
    d.rounded_rectangle(outer, radius=61, fill=(13, 19, 29, 255), outline=(65, 81, 100, 255), width=3)
    # Antenna breaks on the side rail.
    d.line((20, 228, 21, 268), fill=(119, 138, 156, 190), width=2)
    d.line((20, 640, 21, 680), fill=(119, 138, 156, 190), width=2)
    d.line((width - 20, 272, width - 20, 316), fill=(105, 128, 148, 170), width=2)

    if front:
        screen = rounded_gradient((width - 38, height - 42), (12, 27, 53), (6, 8, 22), 47, horizontal=False)
        # Turn the simple gradient into a layered aurora wallpaper.
        sd = ImageDraw.Draw(screen)
        wave = Image.new("RGBA", screen.size, (0, 0, 0, 0))
        wd = ImageDraw.Draw(wave)
        sw, sh = screen.size
        for idx, (col, offset, amp) in enumerate((((22, 188, 221, 65), .15, .14),
                                                   ((79, 91, 228, 55), .38, .20),
                                                   ((139, 78, 199, 40), .68, .16))):
            pts = []
            for step in range(76):
                x = step * (sw - 1) / 75
                y = sh * (offset + amp * math.sin(x / sw * math.pi * 2.1 + idx * 1.4) + 0.06 * math.sin(x / sw * math.pi * 4.3))
                pts.append((int(x), int(y)))
            wd.line(pts, fill=col, width=50, joint="curve")
            wd.line(pts, fill=(col[0] + 55, min(255, col[1] + 45), min(255, col[2] + 35), 105), width=3, joint="curve")
        wave = wave.filter(ImageFilter.GaussianBlur(27))
        screen.alpha_composite(wave)
        # A cool vertical reflection along the right third of the glass.
        reflection = Image.new("RGBA", screen.size, (0, 0, 0, 0))
        rd = ImageDraw.Draw(reflection)
        rd.polygon(((sw * .72, 0), (sw * .91, 0), (sw * .58, sh), (sw * .43, sh)), fill=(161, 228, 244, 14))
        screen.alpha_composite(reflection)
        phone.alpha_composite(screen, (19, 21))
        d = ImageDraw.Draw(phone)
        d.rounded_rectangle((19, 21, width - 19, height - 21), radius=49, outline=(110, 128, 152, 180), width=2)
        # A restrained centered camera cutout and earpiece detail.
        d.ellipse((width // 2 - 11, 33, width // 2 + 11, 55), fill=(2, 5, 10, 255), outline=(73, 84, 105, 220), width=2)
        d.ellipse((width // 2 - 4, 40, width // 2 + 4, 48), fill=(20, 41, 65, 255))
        d.line((width // 2 - 35, 25, width // 2 + 35, 25), fill=(65, 77, 95, 135), width=2)
    else:
        body = rounded_gradient((width - 38, height - 42), (128, 144, 158), (40, 53, 69), 48, horizontal=True)
        # Tempered-glass back with a subtle blue-gray longitudinal sheen.
        phone.alpha_composite(body, (19, 21))
        d = ImageDraw.Draw(phone)
        d.rounded_rectangle((19, 21, width - 19, height - 21), radius=48, outline=(209, 219, 225, 155), width=2)
        d.line((width - 68, 89, width - 68, height - 90), fill=(210, 223, 230, 34), width=2)
        d.line((36, 160, 36, height - 160), fill=(228, 237, 241, 22), width=3)
        # Three individually machined camera rings with blue lens reflections.
        for idx, cy in enumerate((152, 263, 374)):
            cx = 111
            r = 40 if idx == 0 else 37
            d.ellipse((cx - r - 5, cy - r - 5, cx + r + 5, cy + r + 5), fill=(68, 79, 91, 255), outline=(202, 212, 218, 245), width=3)
            d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=(10, 17, 27, 255), outline=(24, 35, 48, 255), width=3)
            d.ellipse((cx - r + 8, cy - r + 8, cx + r - 8, cy + r - 8), fill=(18, 35, 55, 255), outline=(67, 105, 133, 235), width=2)
            d.ellipse((cx - 13, cy - 13, cx + 13, cy + 13), fill=(4, 12, 23, 255), outline=(79, 126, 160, 230), width=2)
            d.arc((cx - r + 7, cy - r + 7, cx + r - 7, cy + r - 7), 206, 295, fill=(159, 221, 237, 210), width=3)
            d.ellipse((cx - 5, cy - 18, cx + 2, cy - 11), fill=(180, 227, 240, 210))
        d.ellipse((168, 419, 190, 441), fill=(224, 217, 191, 255), outline=(239, 237, 220, 255), width=2)
        d.ellipse((170, 461, 188, 479), fill=(89, 101, 112, 255), outline=(177, 190, 199, 225), width=2)
        mark_font = font_lat(19, True)
        brand = "SAMSUNG"
        tw = d.textlength(brand, font=mark_font)
        d.text(((width - tw) / 2, height - 118), brand, font=mark_font, fill=(224, 232, 236, 220))
        # tiny design accent, not a model identifier
        d.line((width * .39, height - 91, width * .61, height - 91), fill=(204, 220, 227, 64), width=1)
    return phone


def rotated_phone(phone: Image.Image, degrees: float) -> Image.Image:
    return phone.rotate(degrees, resample=Image.Resampling.BICUBIC, expand=True)


def build_assembly_parts() -> dict[str, Image.Image]:
    # Each translucent layer is a generic concept component, not a service diagram.
    w, h = 400, 690
    frame = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    fd = ImageDraw.Draw(frame)
    fd.rounded_rectangle((8, 8, w - 8, h - 8), radius=55, fill=(27, 35, 47, 110), outline=(182, 204, 219, 235), width=5)
    fd.rounded_rectangle((21, 21, w - 21, h - 21), radius=44, outline=(70, 111, 136, 210), width=2)
    for yy in (105, 310, 556):
        fd.line((10, yy, 22, yy), fill=(156, 191, 209, 220), width=3)
        fd.line((w - 22, yy, w - 10, yy), fill=(156, 191, 209, 220), width=3)

    display = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    dd = ImageDraw.Draw(display)
    dd.rounded_rectangle((14, 10, w - 14, h - 10), radius=54, fill=(8, 14, 26, 248), outline=(171, 201, 218, 248), width=3)
    screen = rounded_gradient((w - 38, h - 44), (13, 55, 81), (8, 12, 29), 42)
    sd = ImageDraw.Draw(screen)
    sd.line(((40, 430), (140, 295), (222, 235), (322, 78)), fill=(56, 191, 214, 85), width=34, joint="curve")
    screen.alpha_composite(screen.filter(ImageFilter.GaussianBlur(15)))
    display.alpha_composite(screen, (19, 19))
    dd = ImageDraw.Draw(display)
    dd.ellipse((w // 2 - 9, 33, w // 2 + 9, 51), fill=(0, 2, 7, 255), outline=(86, 107, 129, 220), width=2)

    rear = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    rear_body = rounded_gradient((w - 28, h - 28), (126, 145, 158), (40, 56, 72), 53)
    rear.alpha_composite(rear_body, (14, 14))
    rd = ImageDraw.Draw(rear)
    rd.rounded_rectangle((14, 14, w - 14, h - 14), radius=53, outline=(218, 229, 234, 218), width=3)
    for cy in (103, 177, 251):
        rd.ellipse((49, cy - 24, 97, cy + 24), fill=(8, 17, 29, 255), outline=(215, 223, 227, 245), width=3)
        rd.ellipse((59, cy - 14, 87, cy + 14), fill=(21, 46, 67, 255), outline=(94, 146, 171, 230), width=2)
    rd.ellipse((107, 266, 122, 281), fill=(240, 229, 202, 235))

    battery = Image.new("RGBA", (320, 360), (0, 0, 0, 0))
    bd = ImageDraw.Draw(battery)
    bd.rounded_rectangle((8, 8, 312, 352), radius=24, fill=(51, 63, 74, 255), outline=(195, 205, 211, 245), width=3)
    bd.rounded_rectangle((19, 20, 301, 340), radius=18, fill=(37, 49, 61, 255), outline=(108, 132, 147, 240), width=2)
    for x in range(40, 290, 32):
        bd.line((x, 34, x, 326), fill=(91, 113, 128, 90), width=1)
    bd.rounded_rectangle((73, 95, 247, 260), radius=14, fill=(27, 38, 49, 255), outline=(153, 175, 187, 205), width=2)
    bd.text((103, 153), "BATTERY", font=font_lat(17, True), fill=(211, 225, 232, 245))
    bd.text((124, 180), "CELL", font=font_lat(13), fill=(158, 185, 197, 225))
    bd.rounded_rectangle((144, 4, 178, 20), radius=4, fill=(193, 200, 203, 255))

    pcb = Image.new("RGBA", (345, 275), (0, 0, 0, 0))
    pd = ImageDraw.Draw(pcb)
    pd.rounded_rectangle((5, 5, 340, 270), radius=19, fill=(18, 63, 52, 255), outline=(155, 189, 153, 255), width=3)
    routes = [((56, 74), (114, 74), (114, 111)), ((283, 71), (232, 71), (232, 104)),
              ((51, 197), (109, 197), (109, 165)), ((293, 192), (238, 192), (238, 158)),
              ((166, 25), (166, 76)), ((166, 216), (166, 254))]
    for route in routes:
        pd.line(route, fill=(95, 199, 164, 230), width=3, joint="curve")
    pd.rounded_rectangle((112, 82, 235, 188), radius=10, fill=(12, 24, 28, 255), outline=(185, 158, 94, 255), width=3)
    pd.rounded_rectangle((126, 97, 221, 173), radius=7, fill=(8, 18, 25, 255), outline=(62, 178, 187, 230), width=2)
    pd.text((135, 123), "SoC", font=font_lat(18, True), fill=(204, 228, 219, 255))
    for x, y in ((64, 122), (274, 125), (90, 225), (253, 225), (287, 48), (60, 45)):
        pd.rounded_rectangle((x - 14, y - 10, x + 14, y + 10), radius=4, fill=(24, 34, 35, 255), outline=(207, 170, 97, 240), width=2)
    for x in range(27, 320, 18):
        pd.ellipse((x, 245, x + 5, 250), fill=(204, 177, 116, 230))

    return {"frame": frame, "display": display, "rear": rear, "battery": battery, "pcb": pcb}


def draw_callout(image: Image.Image, text: str, y: int, target: tuple[int, int], alpha: int) -> None:
    layer = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    x = 1625
    d.line((x, y + 13, x + 106, y + 13, target[0], target[1]), fill=(89, 194, 207, int(alpha * 0.7)), width=1)
    d.ellipse((target[0] - 4, target[1] - 4, target[0] + 4, target[1] + 4), fill=(123, 223, 232, alpha))
    draw_tracked(d, text, (x, y - 13), font_lat(13, True), (184, 212, 222, alpha), 1.4)
    image.alpha_composite(layer)


def render_assembly(image: Image.Image, p: float, parts: dict[str, Image.Image]) -> None:
    ease = smooth(p)
    cx, cy = 1340, 545
    # Slightly offset components fan outward at the start, then converge into one device.
    positions = {
        "rear": (cx + 68 * (1 - ease), cy + 133 * (1 - ease)),
        "battery": (cx - 45 * (1 - ease), cy + 15 * (1 - ease)),
        "pcb": (cx + 67 * (1 - ease), cy - 65 * (1 - ease)),
        "frame": (cx, cy),
        "display": (cx - 72 * (1 - ease), cy - 128 * (1 - ease)),
    }
    # Thin alignment rails reveal the assembly axis without turning it into a technical diagram.
    guide = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    gd = ImageDraw.Draw(guide)
    guide_alpha = int(78 * (1.0 - ease))
    for x in (cx - 245, cx + 245):
        gd.line((x, 145, x, 930), fill=(73, 158, 180, guide_alpha), width=1)
        for y in range(175, 930, 30):
            gd.line((x - 7, y, x + 7, y), fill=(92, 177, 194, int(guide_alpha * .72)), width=1)
    image.alpha_composite(guide)

    # Back cover, internal cell, main board, frame, then front glass.
    paste_center(image, parts["rear"], positions["rear"], .92)
    paste_center(image, parts["battery"], positions["battery"], 1.0)
    paste_center(image, parts["pcb"], positions["pcb"], 1.0)
    paste_center(image, parts["frame"], positions["frame"], .97)
    paste_center(image, parts["display"], positions["display"], .97)

    # Glints on the travelling assembly stack.
    pulse_x = cx - 235 + 470 * smooth((p + .08) % 1.0)
    paste_glow(image, (pulse_x, cy + 5), 78, (71, 204, 220), 43)

    call_alpha = int(210 * (1.0 - smooth((p - .49) / .38)))
    if call_alpha > 2:
        draw_callout(image, "DISPLAY ASSEMBLY", 282, (int(positions["display"][0] + 165), int(positions["display"][1] - 206)), call_alpha)
        draw_callout(image, "MAIN BOARD", 432, (int(positions["pcb"][0] + 168), int(positions["pcb"][1] - 26)), call_alpha)
        draw_callout(image, "BATTERY CELL", 598, (int(positions["battery"][0] + 147), int(positions["battery"][1] + 12)), call_alpha)
        draw_callout(image, "FRAME + GLASS", 752, (int(positions["rear"][0] + 164), int(positions["rear"][1] + 215)), call_alpha)


def make_beam(height: int = HEIGHT, width: int = 210, color: tuple[int, int, int] = (96, 205, 226)) -> Image.Image:
    arr = np.zeros((height, width, 4), dtype=np.uint8)
    x = np.arange(width, dtype=np.float32)
    center = width * .52
    strength = 33 * np.exp(-((x - center) / (width * .27)) ** 2)
    arr[:, :, 0] = color[0]
    arr[:, :, 1] = color[1]
    arr[:, :, 2] = color[2]
    arr[:, :, 3] = np.uint8(strength[None, :])
    return Image.fromarray(arr, "RGBA").filter(ImageFilter.GaussianBlur(13))


def add_hero_phones(image: Image.Image, back_phone: Image.Image, front_phone: Image.Image, p: float,
                    scale: float = 1.0) -> None:
    ease = smooth(p)
    bob = math.sin(p * math.pi * 1.5) * 7
    shift = int((1.0 - ease) * 74)
    # A floating pool of light and a slim floor line ground the product.
    paste_glow(image, (1450 - shift * .25, 912 + bob), 320, (44, 111, 153), 47)
    floor = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    fd = ImageDraw.Draw(floor)
    fd.ellipse((1015 - shift, 873 + bob, 1835 - shift, 1012 + bob), fill=(5, 24, 38, 110), outline=(61, 148, 177, 55), width=2)
    fd.line((970, 941, 1850, 941), fill=(94, 174, 190, 32), width=1)
    image.alpha_composite(floor)
    paste_center(image, back_phone, (1263 - shift, 535 + bob), .93 * scale)
    paste_center(image, front_phone, (1576 - shift, 524 + bob), .93 * scale)
    beam_x = int(1120 + p * 720)
    beam = make_beam()
    image.alpha_composite(beam, (beam_x - beam.width // 2, 70))
    # A cool aura just behind the screen-side device.
    paste_glow(image, (1552 - shift, 515 + bob), 225, (40, 137, 204), int(38 + 14 * math.sin(p * math.pi)))


def build_scenes() -> tuple[list[Image.Image], Image.Image, Image.Image, dict[str, Image.Image]]:
    backgrounds = [
        make_background((3, 8, 18), [(1430, 520, 690, (3, 77, 103), .88), (1860, 120, 440, (9, 57, 88), .46)], 3101),
        make_background((4, 12, 18), [(1425, 470, 700, (7, 78, 67), .83), (1840, 870, 410, (8, 84, 85), .43)], 3102),
        make_background((3, 9, 18), [(1430, 540, 630, (8, 86, 113), .55), (1740, 140, 430, (14, 52, 105), .4)], 3103),
        make_background((3, 7, 18), [(1480, 475, 690, (14, 64, 113), .78), (1750, 850, 440, (31, 89, 139), .43)], 3104),
        make_background((2, 6, 16), [(1510, 520, 700, (10, 52, 104), .72), (1840, 190, 450, (16, 73, 113), .4)], 3105),
    ]
    chip = build_chip_art()
    board = build_board_art()
    phones = (rotated_phone(make_phone(False), -7.5), rotated_phone(make_phone(True), 7.0))
    parts = build_assembly_parts()
    return backgrounds, chip, board, {"phones": phones, "parts": parts}


def draw_scene_0(bg: Image.Image, chip: Image.Image, p: float, t: float) -> Image.Image:
    image = bg.copy()
    breathe = math.sin(t * 1.25)
    center = (1367 + 9 * breathe, 520 + 8 * math.sin(t * .9))
    paste_glow(image, center, 390, (20, 153, 183), int(39 + 13 * (1 + breathe) / 2))
    paste_center(image, chip, center, 1.0 + .006 * math.sin(t * .7))
    # Pinprick sparks, sparse and slow.
    d = ImageDraw.Draw(image)
    for i in range(18):
        x = int((i * 137 + t * (18 + i % 5 * 3)) % WIDTH)
        y = int((i * 83 + 45 * math.sin(t * .44 + i)) % HEIGHT)
        d.ellipse((x, y, x + 2, y + 2), fill=(71, 176, 202, 105))
    draw_headline(image, 0, "01  /  SILICON", "小さなチップから、", "精密な技術が動き出す。", p)
    return image


def draw_scene_1(bg: Image.Image, board: Image.Image, p: float, t: float) -> Image.Image:
    image = bg.copy()
    # The large substrate drifts slightly, like a slow macro camera move.
    dx = int(12 * math.sin(t * .53))
    dy = int(7 * math.cos(t * .41))
    paste_glow(image, (1390 + dx, 540 + dy), 445, (38, 149, 127), 50)
    image.alpha_composite(board, (681 + dx, 154 + dy))
    # Traveling electrical point along the outer trace field.
    q = smooth((p * 1.15) % 1.0)
    pulse = (1040 + q * 590 + dx, 430 + 180 * math.sin(q * math.pi * 1.4) + dy)
    paste_glow(image, pulse, 84, (97, 231, 217), 83)
    d = ImageDraw.Draw(image)
    d.ellipse((pulse[0] - 4, pulse[1] - 4, pulse[0] + 4, pulse[1] + 4), fill=(192, 255, 242, 220))
    draw_headline(image, 1, "02  /  CIRCUIT BOARD", "回路の一つひとつに、", "技術を丁寧に積み重ねる。", p)
    return image


def draw_scene_2(bg: Image.Image, parts: dict[str, Image.Image], p: float, t: float) -> Image.Image:
    image = bg.copy()
    paste_glow(image, (1345, 525), 470, (28, 113, 151), 45)
    render_assembly(image, p, parts)
    draw_headline(image, 2, "03  /  ASSEMBLY", "基板の一層から、", "一台のスマートフォンへ。", p)
    return image


def draw_scene_3(bg: Image.Image, phones: tuple[Image.Image, Image.Image], p: float, t: float) -> Image.Image:
    image = bg.copy()
    add_hero_phones(image, phones[0], phones[1], p, 1.0)
    draw_headline(image, 3, "04  /  FINISHED FORM", "細部まで、妥協なく。", "すべてを、ひとつのかたちに。", p)
    return image


def draw_end_card(image: Image.Image, p: float) -> None:
    alpha = int(255 * smooth(p / .15))
    if alpha <= 0:
        return
    lift = int((1 - smooth(p / .2)) * 12)
    layer = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    x, y = 155, 337 + lift
    draw_tracked(d, "CONCEPT FILM  /  2026", (x, y), font_lat(19, True), (100, 214, 230, alpha), 3.0)
    d.line((x, y + 44, x + 94, y + 44), fill=(85, 206, 223, int(alpha * .75)), width=2)
    draw_tracked(d, "SAMSUNG", (x - 1, y + 78), font_lat(35, True), (223, 233, 241, alpha), 4.4)
    d.text((x - 4, y + 129), "Galaxy", font=font_lat(86, False), fill=(246, 249, 255, alpha))
    d.line((x, y + 256, x + 354, y + 256), fill=(90, 158, 184, int(alpha * .62)), width=1)
    d.text((x, y + 289), "技術を、あなたの手の中に。", font=font_jp(39), fill=(206, 222, 235, alpha),
           stroke_width=1, stroke_fill=(4, 15, 29, int(alpha * .48)))
    draw_tracked(d, "FROM SILICON TO GALAXY", (x, y + 373), font_lat(15), (130, 157, 180, int(alpha * .85)), 2.1)
    legal = "本映像はコンセプトビジュアルです。製品の外観・仕様を示すものではありません。"
    d.text((x, 966), legal, font=font_jp(17), fill=(135, 155, 176, int(alpha * .86)))
    image.alpha_composite(layer)


def draw_scene_4(bg: Image.Image, phones: tuple[Image.Image, Image.Image], p: float, t: float) -> Image.Image:
    image = bg.copy()
    add_hero_phones(image, phones[0], phones[1], p, .93)
    draw_end_card(image, p)
    return image


def render_scene(index: int, p: float, t: float, assets: tuple[list[Image.Image], Image.Image, Image.Image, dict[str, Image.Image]]) -> Image.Image:
    backgrounds, chip, board, extras = assets
    if index == 0:
        return draw_scene_0(backgrounds[0], chip, p, t)
    if index == 1:
        return draw_scene_1(backgrounds[1], board, p, t)
    if index == 2:
        return draw_scene_2(backgrounds[2], extras["parts"], p, t)
    if index == 3:
        return draw_scene_3(backgrounds[3], extras["phones"], p, t)
    return draw_scene_4(backgrounds[4], extras["phones"], p, t)


def render_at_time(t: float, assets: tuple[list[Image.Image], Image.Image, Image.Image, dict[str, Image.Image]]) -> Image.Image:
    bounds = SCENE_BOUNDS
    idx = len(bounds) - 2
    for i in range(len(bounds) - 1):
        if bounds[i] <= t < bounds[i + 1]:
            idx = i
            break
    start, end = bounds[idx], bounds[idx + 1]
    p = clamp((t - start) / (end - start))
    frame = render_scene(idx, p, t, assets)
    if idx < 4:
        boundary = bounds[idx + 1]
        if t >= boundary - TRANSITION:
            q = smooth((t - (boundary - TRANSITION)) / TRANSITION)
            next_start, next_end = bounds[idx + 1], bounds[idx + 2]
            next_progress = clamp((t - boundary) / (next_end - next_start))
            incoming = render_scene(idx + 1, next_progress, t, assets)
            frame = blend(frame, incoming, q)
    return frame


def synthesize_audio(path: Path) -> None:
    sample_rate = 48_000
    n = int(sample_rate * DURATION)
    t = np.arange(n, dtype=np.float64) / sample_rate
    slow = 0.5 + 0.5 * np.sin(2 * math.pi * .038 * t + .35)
    pad = (
        .055 * np.sin(2 * math.pi * 55.0 * t)
        + .031 * np.sin(2 * math.pi * 82.41 * t + .42)
        + .018 * np.sin(2 * math.pi * 110.0 * t + 1.1)
        + (.012 + .01 * slow) * np.sin(2 * math.pi * (164.81 + 1.2 * slow) * t)
    )
    # A few quiet glass-like harmonic blooms at the chapter cuts; no sampled/copyrighted music.
    chime = np.zeros(n, dtype=np.float64)
    for event, fundamental in ((.35, 523.25), (4.02, 659.25), (8.12, 587.33), (12.52, 783.99), (16.72, 698.46)):
        local = np.maximum(0.0, t - event)
        active = (t >= event).astype(np.float64)
        env = np.exp(-local * 3.4) * active
        chime += env * (.020 * np.sin(2 * math.pi * fundamental * local) + .008 * np.sin(2 * math.pi * fundamental * 2 * local))
    # A restrained airy high layer gives the picture a little lift.
    air_l = .006 * np.sin(2 * math.pi * (246 + 8 * slow) * t + .2)
    air_r = .006 * np.sin(2 * math.pi * (246 + 8 * slow) * t + .8)
    fade = np.minimum(1.0, t / 1.2) * np.minimum(1.0, (DURATION - t) / 2.0)
    # Keep the ambient bed comfortably audible on phone speakers without crowding the picture.
    left = np.clip((pad + chime + air_l) * fade * 2.0, -0.95, 0.95)
    right = np.clip((pad * .97 + chime * .92 + air_r) * fade * 2.0, -0.95, 0.95)
    stereo = np.column_stack((left, right))
    pcm = np.int16(stereo * 32767)
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(2)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(pcm.tobytes())


def main() -> None:
    parser = argparse.ArgumentParser(description="Render the Samsung Galaxy concept advertisement.")
    parser.add_argument("--output", type=Path, default=OUT_DEFAULT, help="MP4 output path")
    parser.add_argument("--poster", type=Path, default=POSTER_DEFAULT, help="Poster image output path")
    parser.add_argument("--fps", type=int, default=FPS, help="Frame rate (default: 24)")
    args = parser.parse_args()

    if args.fps <= 0:
        raise SystemExit("--fps must be a positive integer")
    if not FONT_JP_PATH.exists():
        raise SystemExit(f"Japanese font is missing: {FONT_JP_PATH}")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.poster.parent.mkdir(parents=True, exist_ok=True)

    print("Preparing procedural scenes…", flush=True)
    assets = build_scenes()
    poster = render_at_time(DURATION - 1 / FPS, assets).convert("RGB")
    poster.save(args.poster, "JPEG", quality=94, optimize=True, subsampling=0)

    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    with tempfile.TemporaryDirectory(prefix="galaxy-ad-") as workdir:
        audio_path = Path(workdir) / "ambient-bed.wav"
        synthesize_audio(audio_path)
        command = [
            ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
            "-f", "rawvideo", "-vcodec", "rawvideo", "-pix_fmt", "rgba",
            "-s", f"{WIDTH}x{HEIGHT}", "-r", str(args.fps), "-i", "-",
            "-i", str(audio_path),
            "-t", f"{DURATION:.3f}",
            "-map", "0:v:0", "-map", "1:a:0",
            "-c:v", "libx264", "-preset", "medium", "-crf", "19",
            "-pix_fmt", "yuv420p", "-profile:v", "high", "-level:v", "4.1",
            "-c:a", "aac", "-b:a", "192k", "-shortest", "-movflags", "+faststart",
            "-metadata", "title=From Silicon to Galaxy — Samsung Galaxy Concept Film",
            "-metadata", "comment=Original concept motion graphics; not an official Samsung campaign.",
            str(args.output),
        ]
        process = subprocess.Popen(command, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
        assert process.stdin is not None
        try:
            for frame_number in range(int(round(args.fps * DURATION))):
                t = frame_number / args.fps
                frame = render_at_time(t, assets)
                process.stdin.write(frame.tobytes())
                if frame_number % (args.fps * 2) == 0:
                    print(f"Rendering {t:05.1f}s / {DURATION:.1f}s", flush=True)
            process.stdin.close()
            stderr = process.stderr.read() if process.stderr is not None else b""
            return_code = process.wait()
        except BrokenPipeError:
            stderr = process.stderr.read() if process.stderr is not None else b""
            process.wait()
            raise RuntimeError(f"FFmpeg stopped while receiving frames: {stderr.decode(errors='replace')}")
        if return_code != 0:
            raise RuntimeError(f"FFmpeg failed: {stderr.decode(errors='replace')}")

    print(f"Wrote {args.output} ({args.output.stat().st_size / (1024 * 1024):.1f} MB)", flush=True)
    print(f"Wrote {args.poster}", flush=True)


if __name__ == "__main__":
    main()
