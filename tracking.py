"""
"Zoomed + tracking" cutting style: the picture is zoomed into a band across the middle of the
phone screen, and the crop follows what your eyes would follow in each shot:

  1. the face of whoever is talking (the face whose mouth moves most),
  2. otherwise the biggest / most important face (staying with the same person while they're on screen),
  3. otherwise a person's body (back to the camera, too far away for a face),
  4. otherwise where the movement is (someone running, a car),
  5. otherwise it stays where it was (or the middle, at the start of a shot).

Small faces far behind the main person (extras, posters, crowds) are ignored, so the crop stays on the
character rather than on something in the background.

It moves smoothly like a camera operator (ignores small wobbles, glides after the subject) and only
jumps when the movie itself cuts to a new shot.

Faces: OpenCV's YuNet model (downloaded once on GitHub) or, if that isn't available, OpenCV's
built-in face finder.
"""
import subprocess
from pathlib import Path

import numpy as np

SAMPLE_FPS = 5           # frames looked at per second
SAMPLE_W = 480           # ...at this small width (fast)
MODEL = Path(__file__).resolve().parent / "assets" / "face_yunet.onnx"
MODEL_URL = ("https://media.githubusercontent.com/media/opencv/opencv_zoo/main/models/face_detection_yunet/"
             "face_detection_yunet_2023mar.onnx")

BAND_W, BAND_H = 1080, 864      # the zoomed picture on the 1080x1920 screen (shorter = more of the scene)
BAND_TOP = (1920 - BAND_H) // 2
BACKGROUND_FACE = 0.4           # a face under this share of the biggest face in the frame is background


def get_model():
    """Downloads the face model once (GitHub can; nothing breaks if it can't)."""
    if MODEL.exists():
        return True
    try:
        import urllib.request
        MODEL.parent.mkdir(parents=True, exist_ok=True)
        urllib.request.urlretrieve(MODEL_URL, str(MODEL))
        return True
    except Exception as e:
        print(f"(face model not downloaded, using the simpler face finder: {e})", flush=True)
        return False


class Faces:
    def __init__(self, w, h):
        import cv2
        self.cv2 = cv2
        self.yunet = None
        if MODEL.exists() and hasattr(cv2, "FaceDetectorYN"):
            try:
                self.yunet = cv2.FaceDetectorYN.create(str(MODEL), "", (w, h), 0.6, 0.3, 50)
            except Exception:
                self.yunet = None
        if self.yunet is None:
            self.front = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
            self.side = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_profileface.xml")

    def bodies(self, frame):
        """[(x, y, w, h)] of whole people (OpenCV's people finder), biggest first."""
        if not hasattr(self, "hog"):
            self.hog = self.cv2.HOGDescriptor()
            self.hog.setSVMDetector(self.cv2.HOGDescriptor_getDefaultPeopleDetector())
        h = frame.shape[0]
        scale = 2 if h < 300 else 1                  # people are often under half the height of a small frame
        img = self.cv2.resize(frame, None, fx=scale, fy=scale) if scale > 1 else frame
        try:
            boxes, weights = self.hog.detectMultiScale(img, winStride=(8, 8), padding=(8, 8), scale=1.08)
        except Exception:
            return []
        out = [(x / scale, y / scale, w / scale, bh / scale) for (x, y, w, bh), wt in
               zip(boxes, list(weights) if len(weights) else []) if float(wt) > 0.5]
        return sorted(out, key=lambda b: -b[2] * b[3])

    def find(self, frame, gray):
        """[(x, y, w, h, confidence)] in this frame's pixels."""
        if self.yunet is not None:
            _, found = self.yunet.detect(frame)
            return [(float(f[0]), float(f[1]), float(f[2]), float(f[3]), float(f[-1])) for f in (found if found is not None else [])]
        out = []
        min_size = max(16, gray.shape[0] // 12)
        for casc in (self.front, self.side):
            for (x, y, w, h) in casc.detectMultiScale(gray, 1.15, 5, minSize=(min_size, min_size)):
                if not any(abs(x - o[0]) < w * 0.5 and abs(y - o[1]) < h * 0.5 for o in out):
                    out.append((float(x), float(y), float(w), float(h), 0.8))
        return out


def read_frames(src, start, length, pre_filter, w, h):
    """Small BGR frames of the part, SAMPLE_FPS a second. pre_filter: e.g. the black-bar crop ("crop=...,")."""
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-ss", f"{start:.3f}", "-t", f"{length:.3f}",
           "-i", str(src), "-an", "-sn", "-vf", f"{pre_filter}fps={SAMPLE_FPS},scale={w}:{h},format=bgr24",
           "-f", "rawvideo", "-"]
    data = subprocess.run(cmd, capture_output=True, check=True).stdout
    n = len(data) // (w * h * 3)
    return np.frombuffer(data[:n * w * h * 3], dtype=np.uint8).reshape(n, h, w, 3)


def is_cut(prev_hist, hist, prev_gray=None, gray=None):
    """A real change of shot: the colours AND the picture both change a lot at once. A flash, a fast pan
    or a lighting change alone doesn't count (those made the camera jump in the middle of a shot)."""
    import cv2
    if prev_hist is None:
        return False
    corr = cv2.compareHist(prev_hist, hist, cv2.HISTCMP_CORREL)
    if prev_gray is None or gray is None:
        return corr < 0.55
    if corr >= 0.5:
        return False
    # compare the shapes in the picture with brightness/contrast taken out: a flash or a lighting change
    # keeps the same shapes, a new shot doesn't
    a = prev_gray.astype(np.float32)
    b = gray.astype(np.float32)
    a = (a - a.mean()) / (a.std() + 2)
    b = (b - b.mean()) / (b.std() + 2)
    return float(np.abs(a - b).mean()) > 0.6


def detections(frames):
    """Per sampled frame: faces [(centre x, width, mouth movement)] (0..1 of the width), where the movement
    is (x or None), and whether a new shot starts there."""
    import cv2
    if not len(frames):
        return [], [], []
    h, w = frames[0].shape[:2]
    finder = Faces(w, h)
    faces, moves, cuts = [], [], []
    prev_gray = prev_hist = None
    for frame in frames:
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        hist = cv2.calcHist([gray], [0], None, [32], [0, 256])
        cv2.normalize(hist, hist)
        cut = prev_gray is None or is_cut(prev_hist, hist, prev_gray, gray)
        found = []
        seen = [f for f in finder.find(frame, gray) if f[2] >= w * 0.04]
        biggest = max((f[2] for f in seen), default=0)
        for fx, fy, fw, fh, conf in seen:
            if fw < biggest * BACKGROUND_FACE:
                continue                                                # an extra far behind the main person
            mouth = 0.0
            if prev_gray is not None and not cut:
                y0, y1 = int(fy + fh * 0.6), int(min(h, fy + fh * 1.05))
                x0, x1 = int(max(0, fx + fw * 0.2)), int(min(w, fx + fw * 0.8))
                if y1 > y0 and x1 > x0:
                    mouth = float(cv2.absdiff(gray[y0:y1, x0:x1], prev_gray[y0:y1, x0:x1]).mean()) / 255
            found.append(((fx + fw / 2) / w, fw / w, mouth))
        if not found:                                                   # no face: look for a whole person
            for bx, by, bw, bh in finder.bodies(frame)[:2]:
                if bh >= h * 0.3:
                    found.append(((bx + bw / 2) / w, bw * 0.6 / w, 0.0))
        move = None
        if not found and prev_gray is not None and not cut:
            diff = cv2.absdiff(gray, prev_gray).astype(np.float32)
            diff[diff < 25] = 0
            cols = diff.sum(axis=0)
            total = cols.sum()
            if total > w * h * 2.0:                                     # enough movement to mean something
                move = float((cols * np.arange(w)).sum() / total) / w
        faces.append(found)
        moves.append(move)
        cuts.append(cut)
        prev_gray, prev_hist = gray, hist
    return faces, moves, cuts


def plan_shot(faces, moves, view):
    """
    What to look at in each frame of ONE shot. Decided once for the whole shot, so the camera never
    swings back and forth between people talking to each other:
      - several people who fit in the frame together: frame them all and hold still;
      - otherwise the main person of the shot (on screen most, biggest, talks most), followed only if
        they themselves move;
      - nobody: follow the movement.
    """
    n = len(faces)
    people = []                      # {"xs": {frame: x}, "size": sum of widths, "mouth": sum}
    for i, found in enumerate(faces):
        for cx, fw, mouth in found:
            best = None
            for p in people:
                last = p["xs"][max(p["xs"])]
                if abs(cx - last) < max(fw, 0.08) and i not in p["xs"] and (best is None or abs(cx - last) < best[0]):
                    best = (abs(cx - last), p)
            if best:
                p = best[1]
            else:
                p = {"xs": {}, "size": 0.0, "mouth": 0.0}
                people.append(p)
            p["xs"][i] = cx
            p["size"] += fw
            p["mouth"] += mouth
    need = max(2, int(n * 0.2))      # ignore faces seen only for a moment (background, mistakes)
    people = [p for p in people if len(p["xs"]) >= min(need, n)]
    if people:                       # small faces behind the main people are extras, not the story
        big = max(p["size"] / len(p["xs"]) for p in people)
        people = [p for p in people if p["size"] / len(p["xs"]) >= big * BACKGROUND_FACE]
    if not people:
        out, last = [], None
        for m in moves:
            last = m if m is not None else last
            out.append(last)
        return out
    if len(people) >= 2:
        spots = [float(np.median(list(p["xs"].values()))) for p in people]
        width = max(p["size"] / len(p["xs"]) for p in people)
        if max(spots) - min(spots) + width <= view * 0.95:        # everyone fits: hold on the group
            return [(max(spots) + min(spots)) / 2] * n
    main = max(people, key=lambda p: len(p["xs"]) / n + p["size"] / len(p["xs"]) * 2 + p["mouth"] / n * 4)
    out, last = [], None
    for i in range(n):
        last = main["xs"].get(i, last)
        out.append(last)
    return out


def targets(frames, view=0.375):
    """Per sampled frame: (x to centre on, 0..1 of the width, or None) and whether a new shot starts."""
    faces, moves, cuts = detections(frames)
    out = []
    starts = [i for i, c in enumerate(cuts) if c] + [len(cuts)]
    for a, b in zip(starts, starts[1:]):
        out += plan_shot(faces[a:b], moves[a:b], view)
    return out, cuts


PAN_EASE = 0.15        # each look (1/SAMPLE_FPS s) the camera covers this share of the way to its subject...
PAN_MAX = 0.05         # ...but at most this share of the crop's width: a slow, steady pan, never a whip
REFRAME = 0.6          # at a new shot the camera only jumps when the subject is further than this (x the crop width)


def glide(cam, target, view):
    """One step of a smooth pan from cam towards target."""
    gap = target - cam
    step = max(-view * PAN_MAX, min(view * PAN_MAX, gap * PAN_EASE))
    return cam + step


def camera_path(points, cuts, view, prev=None):
    """
    Smooth camera centres (0..1 of the width) from the raw targets. view = how much of the width the
    crop shows (0..1). No panning unless the subject would leave the frame; it pans slowly and steadily,
    and only jumps at a new shot whose subject is far from where the camera is.
    prev: where the camera was at the end of the footage just before this (the same scene carrying on),
    so it pans on from there instead of snapping back to a new framing.
    """
    n = len(points)
    if not n:
        return []
    half = view / 2
    clamp = lambda c: min(1 - half, max(half, c))
    path = [0.5] * n
    shots = [i for i in range(n) if cuts[i]] + [n]
    if not shots or shots[0] != 0:
        shots = [0] + shots
    if prev is not None:
        prev = clamp(prev)
    for a, b in zip(shots, shots[1:]):
        if b <= a:
            continue
        seen = [p for p in points[a:b] if p is not None]
        if prev is not None and (b - a < SAMPLE_FPS or not seen):
            path[a:b] = [prev] * (b - a)         # a very short shot (or nobody in it): keep the framing
            continue
        if seen and max(seen) - min(seen) <= view * 0.7:
            # everything the shot looks at fits in the frame: one still framing, no panning at all
            still = clamp((max(seen) + min(seen)) / 2)
            if prev is not None and abs(still - prev) < view * 0.25 and max(seen) - min(seen) <= view * 0.7 - 2 * abs(still - prev):
                still = prev                     # it fits from where the camera already is: don't jump
            if prev is not None and still != prev and abs(still - prev) < view * REFRAME:
                cam = prev                       # pan over to the new framing instead of jumping there
                for i in range(a, b):
                    cam = glide(cam, still, view)
                    path[i] = cam
            else:
                path[a:b] = [still] * (b - a)
            prev = path[b - 1]
            continue
        known = [p for p in points[a:min(b, a + SAMPLE_FPS)] if p is not None] or seen
        cam = clamp(float(np.median(known))) if known else 0.5
        if prev is not None and abs(cam - prev) < view * REFRAME:
            cam = prev                           # close enough: carry on from here and pan, instead of jumping
        target = cam
        dead = view * 0.2                        # only start moving when the subject nears the edge of the frame
        moving = False
        for i in range(a, b):
            if points[i] is not None:
                target = clamp(points[i])
            gap = target - cam
            if not moving and abs(gap) > dead:
                moving = True
            if moving:                           # glide until they're centred again, then hold still
                cam = glide(cam, target, view)
                if abs(target - cam) < view * 0.05:
                    moving = False
            path[i] = cam
        for _ in range(2):                       # soften the start and stop of each glide
            seg = path[a:b]
            path[a:b] = [seg[0]] + [(seg[k - 1] + seg[k] * 2 + seg[k + 1]) / 4 for k in range(1, len(seg) - 1)] + \
                        ([seg[-1]] if len(seg) > 1 else [])
        prev = path[b - 1]
    return [clamp(c) for c in path]


def crop_size(W, H, band=(BAND_W, BAND_H)):
    """Crop (in the movie's pixels) with the band's shape, as tall as the picture."""
    bw, bh = band
    cw, ch = int(H * bw / bh) // 2 * 2, H // 2 * 2
    if cw > W:
        cw, ch = W // 2 * 2, int(W * bh / bw) // 2 * 2
    return cw, ch


_LAST = {"src": None, "end": None, "centre": None}   # where the previous piece's camera stopped


def plan(src, start, length, pre_filter, W, H, workdir, fps_out=30, band=(BAND_W, BAND_H), name="track"):
    """
    Works out the moving crop for one part. Writes the camera moves for ffmpeg (sendcmd) and returns
    the filter text that crops the picture to the band: "sendcmd=...,crop@track=...,"
    W, H: the picture's size after pre_filter (black bars already cut off). band: the zoomed picture's
    shape on screen (taller = more zoomed in).
    """
    get_model()
    cw, ch = crop_size(W, H, band)
    sw = SAMPLE_W
    sh = max(2, int(round(sw * H / W / 2)) * 2)
    try:
        frames = read_frames(src, start, length, pre_filter, sw, sh)
        pts, cuts = targets(frames, cw / W)
        carry = (_LAST["centre"] if _LAST["src"] == str(src) and _LAST["end"] is not None
                 and abs(start - _LAST["end"]) < 1.0 else None)      # the same scene carrying on
        path = camera_path(pts, cuts, cw / W, prev=carry)
    except Exception as e:
        print(f"  (tracking failed, keeping the middle: {e})", flush=True)
        path = []
    found = sum(p is not None for p in (pts if path else []))
    y = (H - ch) // 2
    lines = []
    if path:
        times = np.arange(len(path)) / SAMPLE_FPS
        frame_t = np.arange(0, length, 1 / fps_out)
        # step at new shots, glide in between: interpolate within each shot only
        shot_id = np.cumsum([1 if c else 0 for c in cuts])
        idx = np.clip(np.searchsorted(times, frame_t, side="right") - 1, 0, len(path) - 1)
        nxt = np.clip(idx + 1, 0, len(path) - 1)
        frac = np.clip((frame_t - times[idx]) * SAMPLE_FPS, 0, 1)
        same = shot_id[idx] == shot_id[nxt]
        centre = np.where(same, np.array(path)[idx] * (1 - frac) + np.array(path)[nxt] * frac, np.array(path)[idx])
        xs = np.clip(np.round(centre * W - cw / 2), 0, W - cw).astype(int)
        prev = None
        for t, x in zip(frame_t, xs):
            if x != prev:
                lines.append(f"{t:.3f} crop@track x {int(x)};")
                prev = x
        print(f"  tracking: {found} of {len(path)} moments had a face or movement to follow; "
              f"{len(lines)} camera moves", flush=True)
    if path:
        _LAST.update(src=str(src), end=start + length, centre=float(path[-1]))
    x0 = (W - cw) // 2 if not lines else int(lines[0].split()[-1].rstrip(";"))
    cmd_file = Path(workdir) / f"{name}.txt"
    cmd_file.write_text("\n".join(lines) + "\n" if lines else "", encoding="utf-8")
    send = f"sendcmd=f={cmd_file.name}," if lines else ""
    return f"{send}crop@track=w={cw}:h={ch}:x={x0}:y={y},"


# ---------------------------------------------------------------- the movie's own burned-in subtitles
def text_rows(gray, top=0.55):
    """(first row, last row) of the tallest strip of subtitle-like text in the lower part of a grey frame:
    bright letters with a dark outline, spread across the middle of the picture. None when there's none."""
    import cv2
    h, w = gray.shape
    y0 = int(h * top)
    r = gray[y0:]
    text = (r > 200) & (cv2.dilate((r < 60).astype(np.uint8), np.ones((5, 5), np.uint8)) > 0)
    ok = []
    for row in text:
        xs = np.flatnonzero(row)
        ok.append(len(xs) >= 6 and xs[0] < w * 0.5 < xs[-1] and xs[-1] - xs[0] < w * 0.95)
    best, i = None, 0
    while i < len(ok):
        if ok[i]:
            j = i
            while j < len(ok) and ok[j]:
                j += 1
            if j - i >= max(4, int(h * 0.015)) and (best is None or j - i > best[1] - best[0]):
                best = (i, j)
            i = j
        else:
            i += 1
    return None if best is None else (y0 + best[0], y0 + best[1])


def burned_subtitles(src, pre_filter, start, end, samples=40, width=480):
    """
    Where the movie's own burned-in subtitles sit, as (top, bottom) shares of the picture's height (after
    pre_filter, the black-bar crop), or None when the movie has none. Looks at `samples` frames spread
    between start and end; it's a subtitle strip when a good share of them have text in the same place.
    """
    rows, h = [], 0
    for k in range(samples):
        t = start + (end - start) * (k + 0.5) / samples
        cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-ss", f"{t:.2f}", "-i", str(src), "-frames:v", "1",
               "-an", "-sn", "-vf", f"{pre_filter}scale={width}:-2,format=gray", "-f", "rawvideo", "-"]
        try:
            data = subprocess.run(cmd, capture_output=True, check=True, timeout=60).stdout
        except Exception:
            continue
        h = len(data) // width
        if h < 50:
            continue
        found = text_rows(np.frombuffer(data[:h * width], dtype=np.uint8).reshape(h, width))
        if found:
            rows.append(found)
    if not h or len(rows) < max(3, samples * 0.2):
        return None
    top = float(np.percentile([a for a, _ in rows], 10)) / h
    bottom = float(np.percentile([b for _, b in rows], 90)) / h
    if bottom - top > 0.22:                       # text all over the place: not a subtitle line
        return None
    return max(0.0, top - 0.02), min(1.0, bottom + 0.02)
