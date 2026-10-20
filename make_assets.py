"""Generates the app icon (drpdf.ico) and checkbox tick (check.png)."""
from PIL import Image, ImageDraw

S = 1024
img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
d = ImageDraw.Draw(img)

# rounded square with vertical gradient
grad = Image.new("RGBA", (S, S))
gd = ImageDraw.Draw(grad)
top, bot = (124, 131, 255), (88, 60, 220)
for y in range(S):
    t = y / S
    gd.line([(0, y), (S, y)], fill=tuple(int(a + (b - a) * t) for a, b in zip(top, bot)) + (255,))
mask = Image.new("L", (S, S), 0)
ImageDraw.Draw(mask).rounded_rectangle([40, 40, S - 40, S - 40], radius=220, fill=255)
img.paste(grad, (0, 0), mask)

w = (255, 255, 255, 255)
ink = (104, 92, 236, 255)
# back document
d.rounded_rectangle([200, 300, 560, 780], radius=40, fill=(255, 255, 255, 140))
# front document with folded corner
d.polygon([(400, 230), (720, 230), (820, 330), (820, 830), (400, 830)], fill=w)
d.polygon([(720, 230), (720, 330), (820, 330)], fill=(205, 208, 255, 255))
# text lines
for i, y in enumerate((400, 470, 540)):
    d.rounded_rectangle([470, y, 750 - (90 if i % 2 else 0), y + 28], radius=14, fill=ink)
# "PDF" label
from PIL import ImageFont
d.rounded_rectangle([440, 630, 870, 790], radius=36, fill=(232, 64, 76, 255))
font = ImageFont.truetype("C:/Windows/Fonts/arialbd.ttf", 118)
d.text((655, 712), "PDF", font=font, fill=w, anchor="mm")
# conversion arrow on the back document
d.rounded_rectangle([250, 520, 360, 560], radius=20, fill=ink)
d.polygon([(350, 480), (420, 540), (350, 600)], fill=ink)
# medical cross badge (the "Dr.")
cx, cy, r = 230, 230, 150
d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(95, 211, 154, 255), outline=w, width=24)
a, b = 30, 85
d.rectangle([cx - a, cy - b, cx + a, cy + b], fill=w)
d.rectangle([cx - b, cy - a, cx + b, cy + a], fill=w)

img.save("drpdf.ico", sizes=[(s, s) for s in (16, 24, 32, 48, 64, 128, 256)])
img.resize((256, 256), Image.LANCZOS).save("drpdf.png")

# checkbox tick
c = Image.new("RGBA", (72, 72), (0, 0, 0, 0))
ImageDraw.Draw(c).line([(16, 38), (30, 52), (56, 22)], fill=w, width=9, joint="curve")
c.resize((18, 18), Image.LANCZOS).save("check.png")
print("assets ok")
