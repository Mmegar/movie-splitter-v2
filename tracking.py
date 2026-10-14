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

BAND_W, BAND_H = 1080, 1200     # the zoomed picture on the 1080x1920 screen
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


def targets(frames):
    """Per sampled frame: (x of what to look at, 0..1 of the width, or None) and whether a new shot starts."""
    import cv2
    if not len(frames):
        return [], []
    h, w = frames[0].shape[:2]
    faces = Faces(w, h)
    out, cuts = [], []
    prev_gray = prev_hist = None
    last = None                      # what we followed in the previous frame (x, face width)
    for frame in frames:
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        hist = cv2.calcHist([gray], [0], None, [32], [0, 256])
        cv2.normalize(hist, hist)
        cut = prev_gray is None or is_cut(prev_hist, hist)
        if cut:
            last = None
        found = [f for f in faces.find(frame, gray) if f[2] >= w * 0.04]
        x = None
        if found:
            best, best_score = None, -1.0
            for fx, fy, fw, fh, conf in found:
                cx = fx + fw / 2
                size = (fw * fh) / (w * h)
                mouth = 0.0
                if prev_gray is not None and not cut:
                    y0, y1 = int(fy + fh * 0.6), int(min(h, fy + fh * 1.05))
                    x0, x1 = int(max(0, fx + fw * 0.2)), int(min(w, fx + fw * 0.8))
                    if y1 > y0 and x1 > x0:
                        mouth = float(cv2.absdiff(gray[y0:y1, x0:x1], prev_gray[y0:y1, x0:x1]).mean()) / 255
                stay = 1.0 if last and abs(cx - last) < fw else 0.0      # keep following the same person
                score = size * 4 + mouth * 6 + stay * 0.15 + conf * 0.05
                if score > best_score:
                    best, best_score = cx, score
            x = best
        elif prev_gray is not None and not cut:
            diff = cv2.absdiff(gray, prev_gray).astype(np.float32)
            diff[diff < 25] = 0
            cols = diff.sum(axis=0)
            total = cols.sum()
            if total > w * h * 2.0:                                     # enough movement to mean something
                x = float((cols * np.arange(w)).sum() / total)
        if x is not None:
            last = x
        out.append(None if x is None else x / w)
        cuts.append(cut)
        prev_gray, prev_hist = gray, hist
    return out, cuts


def camera_path(points, cuts, view):
    """
    Smooth camera centres (0..1 of the width) from the raw targets. view = how much of the width the
    crop shows (0..1). Holds still for small moves, glides after bigger ones, jumps only at a new shot.
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
        known = [p for p in points[a:min(b, a + SAMPLE_FPS)] if p is not None] or \
                [p for p in points[a:b] if p is not None]
        cam = clamp(float(np.median(known))) if known else 0.5
        target = cam
        dead = view * 0.08                       # small moves inside this are ignored
        for i in range(a, b):
            if points[i] is not None:
                target = clamp(points[i])
            gap = target - cam
            if abs(gap) > dead:
                cam += gap * 0.4                 # glide 40% of the way each sample (5 a second)
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
        pts, cuts = targets(frames)
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
