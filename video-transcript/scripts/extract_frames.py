#!/usr/bin/env python3
"""Extract key frames from a video: scene-change detection + uniform sampling.

Usage:
  python3 extract_frames.py VIDEO OUT_DIR [--scene-threshold 0.25]
      [--uniform-interval 180] [--min-gap 45] [--width 960]

Writes OUT_DIR/keyframes/kf_<sec>.jpg (scene changes) and
OUT_DIR/keyframes/uf_<sec>.jpg (uniform samples), prints kept frame count.
Finds ffmpeg via PATH, then via the imageio_ffmpeg pip package.
"""
import argparse, os, re, shutil, subprocess, sys

def find_ffmpeg():
    p = shutil.which("ffmpeg")
    if p:
        return p
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except ImportError:
        sys.exit("ffmpeg not found: `pip3 install imageio-ffmpeg` or install ffmpeg")

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("video")
    ap.add_argument("out_dir")
    ap.add_argument("--scene-threshold", type=float, default=0.25)
    ap.add_argument("--uniform-interval", type=int, default=180)
    ap.add_argument("--min-gap", type=int, default=45)
    ap.add_argument("--width", type=int, default=960)
    a = ap.parse_args()

    ff = find_ffmpeg()
    kf = os.path.join(a.out_dir, "keyframes")
    raw = os.path.join(a.out_dir, "frames_raw")
    os.makedirs(kf, exist_ok=True)
    os.makedirs(raw, exist_ok=True)

    # 1) scene-change frames with timestamps
    log = os.path.join(a.out_dir, "scene.log")
    subprocess.run(
        [ff, "-hide_banner", "-i", a.video,
         "-vf", f"select='gt(scene,{a.scene_threshold})',scale={a.width}:-2,showinfo",
         "-vsync", "vfr", "-q:v", "2", os.path.join(raw, "f_%04d.jpg")],
        stderr=open(log, "w"), check=True)
    times = [float(m) for m in re.findall(r"pts_time:([0-9.]+)", open(log).read())]

    # 2) uniform frames (fps filter names sequentially; timestamp = (n-1)*interval)
    subprocess.run(
        [ff, "-hide_banner", "-loglevel", "error", "-i", a.video,
         "-vf", f"fps=1/{a.uniform_interval},scale={a.width}:-2",
         "-q:v", "2", os.path.join(raw, "uf_%05d.jpg")], check=True)

    # 3) merge, dedupe within min-gap seconds, rename by timestamp
    shots = [(t, i + 1) for i, t in enumerate(sorted(times))]
    for p in os.listdir(raw):
        m = re.match(r"uf_(\d+)\.jpg", p)
        if m:
            shots.append(((int(m.group(1)) - 1) * a.uniform_interval, p))
    kept, last = [], -1e9
    for t, ref in sorted(shots):
        if t - last >= a.min_gap:
            kept.append((t, ref)); last = t
    for t, ref in kept:
        src = ref if isinstance(ref, str) else f"f_{ref:04d}.jpg"
        ext = os.path.splitext(src)[1]
        shutil.move(os.path.join(raw, src), os.path.join(kf, f"fr_{int(t):05d}{ext}"))
    shutil.rmtree(raw)
    if os.path.exists(log):
        os.remove(log)
    print(f"kept {len(kept)} keyframes in {kf}")

if __name__ == "__main__":
    main()
