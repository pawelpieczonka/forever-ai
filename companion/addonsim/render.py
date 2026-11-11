"""Draws the simulated screen: what wow.lua emits (sim.draw -> host.draw) back to front, in physical pixels.

Solid colours (the add-on's WHITE8x8 backdrops and colour textures) and texts are drawn as the game draws them. The
game's own art cannot be shipped, so the few textures the add-on uses are drawn as look-alikes (check box, size grip)
and any other texture as a grey placeholder, listed in ``Canvas.placeholders``. A texture an add-on ships in its own
folder (a .tga under Interface\\AddOns) is drawn from that file, tinted by its vertex colour."""

from __future__ import annotations

import math

from pathlib import Path

from PIL import Image, ImageChops, ImageDraw

from .text import TextEngine

WORLD_TOP, WORLD_BOTTOM = (46, 58, 66), (18, 22, 27)  # where the game world would be


def _rgba(r, g, b, a) -> tuple[int, int, int, int]:
    def c(v):
        return max(0, min(255, int(round(float(v) * 255))))
    return c(r), c(g), c(b), c(a)


class Canvas:
    def __init__(self, width: int, height: int, text: TextEngine, world: bool = True):
        self.width, self.height = int(width), int(height)
        if world:
            grad = Image.linear_gradient("L").resize((1, self.height))
            top = Image.new("RGB", (1, self.height), WORLD_TOP)
            bottom = Image.new("RGB", (1, self.height), WORLD_BOTTOM)
            self.im = Image.composite(bottom, top, grad).resize((self.width, self.height))
        else:
            self.im = Image.new("RGB", (self.width, self.height), (0, 0, 0))
        self.draw_ = ImageDraw.Draw(self.im, "RGBA")
        self.text = text
        self.placeholders: set[str] = set()
        self.files: Path | None = None  # the game folder: textures of the add-ons are read from it
        self._pictures: dict[str, Image.Image | None] = {}
        self.boxes: dict[str, tuple] = {}

    # ------------------------------------------------------------------------------------------- helpers
    def _clip(self, c0, c1, c2, c3) -> tuple[int, int, int, int]:
        return (max(0, int(math.floor(c0 + 0.5))), max(0, int(math.floor(c1 + 0.5))),
                min(self.width, int(math.floor(c2 + 0.5))), min(self.height, int(math.floor(c3 + 0.5))))

    def _box(self, x0, y0, x1, y1) -> tuple[int, int, int, int]:
        return (int(math.floor(x0 + 0.5)), int(math.floor(y0 + 0.5)), int(math.floor(x1 + 0.5)),
                int(math.floor(y1 + 0.5)))

    def _paint(self, bbox, clip, fn) -> None:
        """Runs fn(draw, dx, dy) so that nothing outside ``clip`` changes."""
        cl = (max(bbox[0], clip[0]), max(bbox[1], clip[1]), min(bbox[2], clip[2]), min(bbox[3], clip[3]))
        if cl[2] <= cl[0] or cl[3] <= cl[1]:
            return
        if cl == tuple(bbox):
            fn(self.draw_, 0, 0)
            return
        sub = self.im.crop(cl)
        fn(ImageDraw.Draw(sub, "RGBA"), -cl[0], -cl[1])
        self.im.paste(sub, cl[:2])

    def _rect(self, box, clip, fill) -> None:
        if fill[3] <= 0:
            return
        x0, y0, x1, y1 = box
        if x1 <= x0 or y1 <= y0:
            return
        cl = (max(x0, clip[0]), max(y0, clip[1]), min(x1, clip[2]), min(y1, clip[3]))
        if cl[2] <= cl[0] or cl[3] <= cl[1]:
            return
        if fill[3] >= 255:
            self.im.paste(fill[:3], cl)
        else:
            self.draw_.rectangle((cl[0], cl[1], cl[2] - 1, cl[3] - 1), fill=fill)

    # ------------------------------------------------------------------------------------------- ops from wow.lua
    def op(self, kind: bytes, *a) -> None:
        kind = kind.decode()
        x0, y0, x1, y1 = a[0], a[1], a[2], a[3]
        clip = self._clip(*a[4:8])
        color = _rgba(*a[8:12])
        if kind == "rect":
            self._rect(self._box(x0, y0, x1, y1), clip, color)
        elif kind == "edge":
            size = max(1, int(round(a[12])))
            bx0, by0, bx1, by1 = self._box(x0, y0, x1, y1)
            for box in ((bx0, by0, bx1, by0 + size), (bx0, by1 - size, bx1, by1),
                        (bx0, by0 + size, bx0 + size, by1 - size), (bx1 - size, by0 + size, bx1, by1 - size)):
                self._rect(box, clip, color)
        elif kind == "tex":
            self._texture(self._box(x0, y0, x1, y1), clip, color, a[12].decode(errors="replace"))
        elif kind == "text":
            self._text((x0, y0, x1, y1), clip, color, *a[12:])
        elif kind == "cursor":
            self._cursor((x0, y0, x1, y1), clip, color, *a[12:])

    def _texture(self, box, clip, color, path: str) -> None:
        name = path.replace("/", "\\").rsplit("\\", 1)[-1].lower()
        x0, y0, x1, y1 = box
        w, h = x1 - x0, y1 - y0
        if w <= 0 or h <= 0:
            return
        alpha = color[3]
        if name == "white8x8":
            self._rect(box, clip, color)
        elif name.startswith("ui-checkbox"):
            ix0, iy0, ix1, iy1 = x0 + w * 0.17, y0 + h * 0.17, x1 - w * 0.17, y1 - h * 0.17
            line = max(1.0, w * 0.06)

            def check(d, dx, dy):
                if name in ("ui-checkbox-up", "ui-checkbox-down"):
                    d.rounded_rectangle((ix0 + dx, iy0 + dy, ix1 + dx - 1, iy1 + dy - 1), radius=max(1, w * 0.06),
                                        fill=(20, 20, 22, int(0.92 * alpha)),
                                        outline=(140, 140, 146, alpha), width=int(round(line)))
                elif name == "ui-checkbox-highlight":
                    d.rectangle((ix0 + dx, iy0 + dy, ix1 + dx - 1, iy1 + dy - 1),
                                fill=(255, 255, 255, int(alpha * 0.16)))
                elif name.startswith("ui-checkbox-check"):
                    gold = (255, 210, 0, alpha) if name == "ui-checkbox-check" else (150, 150, 150, alpha)
                    pts = [(x0 + w * 0.27 + dx, y0 + h * 0.5 + dy), (x0 + w * 0.44 + dx, y0 + h * 0.68 + dy),
                           (x0 + w * 0.76 + dx, y0 + h * 0.3 + dy)]
                    d.line(pts, fill=gold, width=max(2, int(round(w * 0.11))), joint="curve")
            self._paint(box, clip, check)
        elif name.startswith("ui-chatim-sizegrabber"):
            shade = (230, 230, 230, alpha) if name.endswith("highlight") else (150, 150, 156, alpha)

            def grip(d, dx, dy):
                for k in (0.25, 0.5, 0.75):
                    d.line((x1 - w * k + dx, y1 - 1 + dy, x1 - 1 + dx, y1 - h * k + dy), fill=shade,
                           width=max(1, int(round(w / 12))))
            if not name.endswith("highlight"):  # the highlight is only shown under the mouse
                self._paint(box, clip, grip)
        elif self._picture(path) is not None:
            self._image(box, clip, color, self._picture(path))
        else:
            self.placeholders.add(path)

            def placeholder(d, dx, dy):
                d.rectangle((x0 + dx, y0 + dy, x1 + dx - 1, y1 + dy - 1), fill=(120, 120, 130, int(alpha * 0.25)),
                            outline=(160, 160, 170, int(alpha * 0.6)))
            self._paint(box, clip, placeholder)

    def _picture(self, path: str) -> Image.Image | None:
        """A texture from an add-on's folder (the game reads .tga there): the real picture, or None."""
        if path not in self._pictures:
            pic = None
            parts = path.replace("\\", "/").split("/")
            if self.files is not None and len(parts) > 2 and parts[0].lower() == "interface" and parts[1].lower() == "addons":
                f = Path(self.files, *parts)
                for cand in (f, f.with_name(f.name + ".tga")):
                    if cand.is_file():
                        with Image.open(cand) as im:
                            pic = im.convert("RGBA")
                        break
            self._pictures[path] = pic
        return self._pictures[path]

    def _image(self, box, clip, color, pic: Image.Image) -> None:
        x0, y0, x1, y1 = box
        cl = (max(x0, clip[0]), max(y0, clip[1]), min(x1, clip[2]), min(y1, clip[3]))
        if cl[2] <= cl[0] or cl[3] <= cl[1]:
            return
        pic = pic.resize((x1 - x0, y1 - y0), Image.LANCZOS)
        tinted = ImageChops.multiply(pic, Image.new("RGBA", pic.size, color[:3] + (255,)))
        tinted.putalpha(pic.getchannel("A").point(lambda v: v * color[3] // 255))
        part = tinted.crop((cl[0] - x0, cl[1] - y0, cl[2] - x0, cl[3] - y0))
        self.im.paste(part, cl[:2], part)

    def _text(self, box, clip, color, text: bytes, font: bytes, size, flags: bytes, justify_h: bytes,
              justify_v: bytes, wrap, spacing, max_lines, wrap_width, sdx, sdy, sr, sg, sb, sa, scroll_end) -> None:
        s = text.decode("utf-8", errors="replace")
        game_font = font.decode(errors="replace")
        lay = self.text.layout(s, game_font, float(size), float(wrap_width), bool(wrap), float(spacing), int(max_lines))
        if not lay.lines:
            return
        face = self.text.face(game_font, float(size))
        x0, y0, x1, y1 = box
        jv = justify_v.decode()
        if jv == "TOP":
            ty = y0
        elif jv == "BOTTOM":
            ty = y1 - lay.height
        else:
            ty = y0 + (y1 - y0 - lay.height) / 2
        ascent, descent = face.getmetrics()
        outline = b"OUTLINE" in flags.upper()
        shadow = (sdx or sdy) and sa > 0
        shadow_color = _rgba(sr, sg, sb, sa)
        jh = justify_h.decode()
        bbox = (int(min(x0, x1)) - 4, int(ty) - 4, int(max(x1, x0 + lay.width)) + 4, int(ty + lay.height) + 4)

        def paint(d, dx, dy):
            y = ty
            for line in lay.lines:
                if jh == "CENTER":
                    x = x0 + (x1 - x0 - line.width) / 2
                elif jh == "RIGHT":
                    x = x1 - line.width
                else:
                    x = x0
                if scroll_end and line.width > x1 - x0:  # a text field shows the end of a long text
                    x = x1 - line.width
                base = y + (lay.line_height - (ascent + descent)) / 2 + ascent
                for p in line.pieces:
                    fill = color if p.color is None else (p.color[0], p.color[1], p.color[2], color[3])
                    if p.icon:
                        size = lay.line_height
                        d.rectangle((x + dx + 1, y + dy + 1, x + dx + size - 2, y + dy + size - 2),
                                    fill=(140, 140, 150, int(color[3] * 0.5)))
                        x += lay.line_height
                        continue
                    if shadow:
                        d.text((x + dx + sdx, base + dy + sdy), p.text, font=face, fill=shadow_color, anchor="ls")
                    if outline:
                        d.text((x + dx, base + dy), p.text, font=face, fill=fill, anchor="ls", stroke_width=1,
                               stroke_fill=(0, 0, 0, color[3]))
                    else:
                        d.text((x + dx, base + dy), p.text, font=face, fill=fill, anchor="ls")
                    x += self.text.width(face, p.text)
                y += lay.line_height + spacing

        self._paint(bbox, clip, paint)

    def _cursor(self, box, clip, color, text: bytes, font: bytes, size, flags: bytes) -> None:
        s = text.decode("utf-8", errors="replace")
        game_font = font.decode(errors="replace")
        lay = self.text.layout(s, game_font, float(size), 0, False)
        x0, y0, x1, y1 = box
        x = min(x1 - 1, x0 + lay.width + 1)
        mid = (y0 + y1) / 2
        self._rect(self._box(x, mid - size / 2, x + max(1, size / 12), mid + size / 2), clip, color)

    def image(self) -> Image.Image:
        return self.im
