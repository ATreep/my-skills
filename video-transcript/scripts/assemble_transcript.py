#!/usr/bin/env python3
"""Assemble a readable markdown transcript from punctuated.json + sections.json + keyframes.

Usage:
  python3 assemble_transcript.py --workdir DIR --title T [--course C] [--creator U]
      [--creator-label 作者] [--source-url URL] [--sections sections.json]
      [--transcript punctuated.json] [--keyframes keyframes]
      [--out transcript.md] [--outdir DIR] [--embed-images]

Expects (relative to DIR):
  punctuated.json  [{"start": ms, "end": ms, "text": "..."}, ...] — transcription step
  sections.json    [{"title": "...", "start": ms}, ...] — titled topic sections designed
                   by the model (see SKILL.md); the transcript is organized by these,
                   NOT by raw paragraph timestamps
  keyframes/*.jpg  fr_<sec>.jpg / kf_<sec>.jpg (timestamp-encoded), or legacy
                   uf_<n>.jpg (sequential, one every 180 s)

Output modes:
  default          writes --out inside WORKDIR, relative image links — use when
                   transcript.md lives next to the keyframes.
  --outdir DIR     writes --out into DIR and copies the referenced keyframes into
                   DIR/keyframes/ — final deliverable next to the video, when you
                   want to keep the raw frames too.
  --outdir DIR --embed-images
                   writes --out into DIR with screenshots base64-embedded as data
                   URIs — fully self-contained single file, nothing else copied.
                   Use when WORKDIR is a throwaway /tmp scratch dir.

The document: metadata header, short TOC of section titles, and one
"## <n>. <title> [start–end]" block per section containing the punctuated
paragraphs and the keyframes whose timestamps fall inside that section.
"""
import argparse, base64, bisect, glob, json, os, re, shutil, sys


def fmt(sec: float) -> str:
    sec = int(round(sec))
    return f"{sec//3600:02d}:{sec%3600//60:02d}:{sec%60:02d}"


def fmt_compact(sec: float) -> str:
    sec = int(round(sec))
    if sec >= 3600:
        return f"{sec//3600}:{sec%3600//60:02d}:{sec%60:02d}"
    return f"{sec//60:02d}:{sec%60:02d}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workdir", required=True)
    ap.add_argument("--title", required=True)
    ap.add_argument("--course", default="")
    ap.add_argument("--creator", default="")
    ap.add_argument("--creator-label", default="作者")
    ap.add_argument("--source-url", default="")
    ap.add_argument("--sections", default="sections.json")
    ap.add_argument("--transcript", default="punctuated.json")
    ap.add_argument("--keyframes", default="keyframes")
    ap.add_argument("--out", default="transcript.md")
    ap.add_argument("--outdir", default="",
                    help="write the final markdown here (deliverable location, e.g. "
                         "next to the video) instead of inside workdir")
    ap.add_argument("--embed-images", action="store_true",
                    help="embed screenshots as base64 data URIs instead of file links "
                         "(self-contained output; use with --outdir when workdir is a "
                         "throwaway /tmp scratch dir)")
    a = ap.parse_args()
    os.chdir(a.workdir)

    with open(a.transcript, encoding="utf-8") as f:
        segments = json.load(f)
    paras = [{"start": s["start"] / 1000, "end": s["end"] / 1000,
              "text": s["text"].strip()} for s in segments if s["text"].strip()]
    if not paras:
        raise SystemExit("no non-empty segments found in " + a.transcript)

    with open(a.sections, encoding="utf-8") as f:
        raw_secs = json.load(f)
    secs = [{"title": str(s["title"]).strip(), "start": float(s["start"]) / 1000}
            for s in raw_secs if str(s.get("title", "")).strip()]
    if not secs:
        raise SystemExit(f"no titled sections found in {a.sections} — design the topic "
                         "sections first (see SKILL.md), then re-run")
    secs.sort(key=lambda s: s["start"])
    if secs[0]["start"] > paras[0]["start"] + 2:
        raise SystemExit(f"first section starts at {fmt(secs[0]['start'])} but the first "
                         f"paragraph starts at {fmt(paras[0]['start'])}; the first section "
                         "must start at or before the first paragraph")

    # Assign each paragraph to the last section whose start it falls in (2 s tolerance
    # so starts copied with light rounding still land on the intended section).
    starts = [s["start"] for s in secs]
    sec_paras = [[] for _ in secs]
    for pa in paras:
        sec_paras[bisect.bisect_right(starts, pa["start"] + 2) - 1].append(pa)
    empty = [i for i, sp in enumerate(sec_paras) if not sp]
    if empty:
        for i in empty:
            print(f"empty section {i + 1} ({secs[i]['title']!r} at "
                  f"{fmt(secs[i]['start'])}) — no paragraph starts there; merge it or "
                  "fix its start time", file=sys.stderr)
        raise SystemExit(f"{len(empty)} section(s) contain no paragraphs — sections.json "
                         "looks misaligned with the transcript")

    shots = []
    for p in glob.glob(f"{a.keyframes}/*.jpg") + glob.glob(f"{a.keyframes}/*.png"):
        # timestamp-encoded names: fr_<sec> (extract_frames.py) or kf_<sec>
        m = re.search(r"(?:fr|kf)_(\d+)", p)
        if m:
            shots.append((int(m.group(1)), p))
        else:  # legacy sequential uf_<n>.jpg, one every 180 s starting at 0
            m = re.search(r"uf_(\d+)", p)
            if m:
                shots.append(((int(m.group(1)) - 1) * 180, p))
    shots.sort()
    dedup, last = [], -999  # drop frames within 45 s of a kept one
    for t, p in shots:
        if t - last >= 45:
            dedup.append((t, p)); last = t
    shots = dedup

    shot_by_para, orphans = {}, []
    for t, p in shots:
        for sp in sec_paras:
            hit = next((pa for pa in sp if pa["start"] - 5 <= t <= pa["end"] + 5), None)
            if hit is not None:
                shot_by_para.setdefault(id(hit), []).append((t, p))
                break
        else:
            orphans.append((t, p))
    for t, p in orphans:  # attach to nearest paragraph so no frame is lost
        best = min((pa for sp in sec_paras for pa in sp),
                   key=lambda pa: abs(pa["start"] - t))
        shot_by_para.setdefault(id(best), []).append((t, p))

    # keyframe link for each shot, relative to where the markdown will live
    used = {p for v in shot_by_para.values() for _, p in v}
    embed = a.embed_images
    link = {}
    for t, p in shots:
        if p not in used:
            continue
        if embed:
            data = base64.b64encode(open(p, "rb").read()).decode()
            mime = "image/png" if p.endswith(".png") else "image/jpeg"
            link[p] = f"data:{mime};base64,{data}"
        else:
            link[p] = f"{a.keyframes}/{os.path.basename(p)}"

    if a.outdir:
        os.makedirs(a.outdir, exist_ok=True)
        out_path = os.path.join(a.outdir, a.out)
        if not embed:  # deliver referenced keyframes alongside the markdown
            dst = os.path.join(a.outdir, a.keyframes)
            os.makedirs(dst, exist_ok=True)
            for p in used:
                shutil.copy2(p, os.path.join(dst, os.path.basename(p)))
    else:
        out_path = a.out

    L = [f"# {a.title} — 字幕稿", ""]
    meta = []
    if a.course: meta.append(f"**课程**: {a.course}")
    if a.creator: meta.append(f"**{a.creator_label}**: {a.creator}")
    if a.source_url: meta.append(f"**来源**: {a.source_url}")
    meta.append(f"**时长**: {fmt(paras[-1]['end'])}")
    L.append("> " + "  \n> ".join(meta)); L.append("")
    L.append("*文字稿为自动转写并加标点，可能有少量识别错误；截图为关键帧采样。*"); L.append("")

    L.append("## 目录")
    for i, sp in enumerate(sec_paras):
        L.append(f"{i + 1}. [{secs[i]['title']}](#sec-{i + 1}) "
                 f"`[{fmt_compact(sp[0]['start'])} – {fmt_compact(sp[-1]['end'])}]`")
    L.append("")

    for i, sp in enumerate(sec_paras):
        L.append(f'<a id="sec-{i + 1}"></a>')
        L.append(f"## {i + 1}. {secs[i]['title']} "
                 f"[{fmt_compact(sp[0]['start'])} – {fmt_compact(sp[-1]['end'])}]")
        L.append("")
        for pa in sp:
            for t, p in sorted(shot_by_para.get(id(pa), [])):
                L.append(f"![{fmt(t)}]({link[p]})")
                L.append(f"*截图 @ {fmt(t)}*")
                L.append("")
            L.append(pa["text"]); L.append("")

    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(L))
    print(f"wrote {out_path}: {len(secs)} titled sections, {len(paras)} paragraphs, "
          f"{len(used)} screenshots{' (embedded)' if embed else ''}")


if __name__ == "__main__":
    main()
