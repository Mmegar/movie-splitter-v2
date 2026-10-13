from PIL import Image, ImageDraw, ImageFilter
import math
S = 4                     # draw big, shrink for smooth edges
W, H = 220 * S, 140 * S
cx, cy = 100 * S, 70 * S  # body centre; head points right (+x)

def leg(d, base, joints, width):
    pts = [base] + joints
    d.line(pts, fill=(38, 20, 8, 255), width=width, joint="curve")
    for p in pts[1:-1]:
        d.ellipse([p[0]-width/2, p[1]-width/2, p[0]+width/2, p[1]+width/2], fill=(38, 20, 8, 255))

def frame(phase):
    im = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    lw = int(3.4 * S)
    # legs: (attach x offset, base angle deg) per side; tripod gait swings alternate legs
    specs = [(26, 70, 1), (6, 105, -1), (-12, 135, 1)]
    for side in (1, -1):
        for ax, ang, grp in specs:
            swing = 12 * (1 if (grp * side * (1 if phase else -1)) > 0 else -1)
            a = math.radians(ang + swing)
            bx, by = cx + ax * S, cy + side * 14 * S
            l1, l2 = (16 if ang < 120 else 22) * S, (22 if ang < 120 else 34) * S
            j1 = (bx + math.cos(a) * l1, by + side * math.sin(a) * l1)
            a2 = a + math.radians(-30 if ang < 90 else 28)
            j2 = (j1[0] + math.cos(a2) * l2, j1[1] + side * math.sin(a2) * l2)
            leg(d, (bx, by), [j1, j2], lw)
    # antennae
    for side in (1, -1):
        p = [(cx + 52 * S, cy + side * 4 * S)]
        for i in range(1, 9):
            t = i / 8
            p.append((cx + (52 + 60 * t) * S, cy + side * (4 + 22 * t * t + (6 if phase else 0) * t) * S))
        d.line(p, fill=(45, 24, 10, 255), width=int(1.6 * S), joint="curve")
    # abdomen / wings
    d.ellipse([cx - 54 * S, cy - 23 * S, cx + 30 * S, cy + 23 * S], fill=(92, 46, 16, 255))
    d.ellipse([cx - 50 * S, cy - 19 * S, cx + 22 * S, cy + 19 * S], fill=(120, 62, 22, 255))
    d.line([(cx - 46 * S, cy), (cx + 24 * S, cy)], fill=(70, 34, 12, 255), width=int(1.5 * S))
    # highlight
    d.ellipse([cx - 30 * S, cy - 11 * S, cx + 4 * S, cy - 5 * S], fill=(160, 96, 48, 140))
    # pronotum (shield) and head
    d.ellipse([cx + 16 * S, cy - 19 * S, cx + 48 * S, cy + 19 * S], fill=(70, 36, 14, 255))
    d.ellipse([cx + 24 * S, cy - 10 * S, cx + 40 * S, cy + 10 * S], fill=(110, 60, 24, 255))
    d.ellipse([cx + 44 * S, cy - 8 * S, cx + 58 * S, cy + 8 * S], fill=(48, 24, 10, 255))
    # soft shadow underneath
    sh = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(sh).ellipse([cx - 50 * S, cy - 14 * S, cx + 56 * S, cy + 22 * S], fill=(0, 0, 0, 90))
    sh = sh.filter(ImageFilter.GaussianBlur(6 * S))
    out = Image.alpha_composite(sh, im)
    return out.resize((W // S, H // S), Image.LANCZOS)

frame(0).save("assets/roach_a.png")
frame(1).save("assets/roach_b.png")
