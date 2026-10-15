"""
"Zoomed + tracking" cutting style: the picture is zoomed into a band across the middle of the
phone screen, and the crop follows what your eyes would follow in each shot:

  1. the face of whoever is talking (the face whose mouth moves most),
  2. otherwise the biggest / most important face (staying with the same person while they're on screen),
  3. otherwise where the movement is (someone running, a car),
  4. otherwise it stays where it was (or the middle, at the start of a shot).

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

BAND_W, BAND_H = 1080, 960      # the zoomed picture on the 1080x1920 screen (wider = more of the scene)
BAND_TOP = (1920 - BAND_H) // 2


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


def is_cut(prev_hist, hist):
    import cv2
    return prev_hist is not None and cv2.compareHist(prev_hist, hist, cv2.HISTCMP_CORREL) < 0.55


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
        cut = prev_gray is None or is_cut(prev_hist, hist)
        found = []
        for fx, fy, fw, fh, conf in finder.find(frame, gray):
            if fw < w * 0.04:
                continue
            mouth = 0.0
            if prev_gray is not None and not cut:
                y0, y1 = int(fy + fh * 0.6), int(min(h, fy + fh * 1.05))
                x0, x1 = int(max(0, fx + fw * 0.2)), int(min(w, fx + fw * 0.8))
                if y1 > y0 and x1 > x0:
                    mouth = float(cv2.absdiff(gray[y0:y1, x0:x1], prev_gray[y0:y1, x0:x1]).mean()) / 255
            found.append(((fx + fw / 2) / w, fw / w, mouth))
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


def camera_path(points, cuts, view):
    """
    Smooth camera centres (0..1 of the width) from the raw targets. view = how much of the width the
    crop shows (0..1). No panning unless the subject would leave the frame; jumps only at a new shot.
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
    for a, b in zip(shots, shots[1:]):
        if b <= a:
            continue
        seen = [p for p in points[a:b] if p is not None]
        if seen and max(seen) - min(seen) <= view * 0.7:
            # everything the shot looks at fits in the frame: one still framing, no panning at all
            path[a:b] = [clamp((max(seen) + min(seen)) / 2)] * (b - a)
            continue
        known = [p for p in points[a:min(b, a + SAMPLE_FPS)] if p is not None] or seen
        cam = clamp(float(np.median(known))) if known else 0.5
        target = cam
        dead = view * 0.3                        # only move when the subject nears the edge of the frame
        for i in range(a, b):
            if points[i] is not None:
                target = clamp(points[i])
            gap = target - cam
            if abs(gap) > dead:
                cam += (gap - dead * (1 if gap > 0 else -1)) * 0.5 + gap * 0.1   # catch up, then stop
            path[i] = cam
        for _ in range(2):                       # soften the start and stop of each glide
            seg = path[a:b]
            path[a:b] = [seg[0]] + [(seg[k - 1] + seg[k] * 2 + seg[k + 1]) / 4 for k in range(1, len(seg) - 1)] + \
                        ([seg[-1]] if len(seg) > 1 else [])
    return [clamp(c) for c in path]


def crop_size(W, H):
    """Crop (in the movie's pixels) with the band's shape, as tall as the picture."""
    cw, ch = int(H * BAND_W / BAND_H) // 2 * 2, H // 2 * 2
    if cw > W:
        cw, ch = W // 2 * 2, int(W * BAND_H / BAND_W) // 2 * 2
    return cw, ch


def plan(src, start, length, pre_filter, W, H, workdir, fps_out=30):
    """
    Works out the moving crop for one part. Writes the camera moves for ffmpeg (sendcmd) and returns
    the filter text that crops the picture to the band: "sendcmd=...,crop@track=...,"
    W, H: the picture's size after pre_filter (black bars already cut off).
    """
    cw, ch = crop_size(W, H)
    sw = SAMPLE_W
    sh = max(2, int(round(sw * H / W / 2)) * 2)
    try:
        frames = read_frames(src, start, length, pre_filter, sw, sh)
        pts, cuts = targets(frames, cw / W)
        path = camera_path(pts, cuts, cw / W)
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
    x0 = (W - cw) // 2 if not lines else int(lines[0].split()[-1].rstrip(";"))
    cmd_file = Path(workdir) / "track.txt"
    cmd_file.write_text("\n".join(lines) + "\n" if lines else "", encoding="utf-8")
    send = f"sendcmd=f={cmd_file.name}," if lines else ""
    return f"{send}crop@track=w={cw}:h={ch}:x={x0}:y={y},"
