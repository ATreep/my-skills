---
name: video-transcript
description: Create a readable transcript of any video — a local video file OR a Bilibili URL/BV number — with timestamped, punctuated text and key-frame screenshots in one markdown document. Use whenever the user asks to transcribe, subtitle, or make a readable notes/summary document from a video referenced by file path, full Bilibili URL, or bare BV id (e.g. "BV1pb8o6yE8f"). For Bilibili inputs, first loads the bilibili-video-download skill to fetch the video.
---

# Video → Readable Transcript with Key Frames

Produce a single markdown document: full transcript in the source language,
punctuated and organized into a few **titled topic sections** (not one section per
timestamp), with key-frame screenshots embedded next to the text they illustrate.
Verified end-to-end on a 100-minute Mandarin course lecture (720P MP4) on a heavily
firewalled network (2026-09).

**File placement**: keep the video and the final `transcript.md` together in the
deliverable directory (e.g. `L2_提示词工程/`); create **all intermediates in a /tmp
scratch dir** (`WORKDIR=$(mktemp -d /tmp/vt_XXXX)` — audio.wav, keyframes,
punctuated.json, sections.json). Only the final markdown goes to the deliverable
dir. With `--outdir ... --embed-images` the screenshots are base64-embedded into the
markdown, so the single file is fully self-contained and the scratch dir can be
deleted right after verification.

## Pipeline overview

1. **Get the video** (skip if given a local file)
2. **Set up tools** (ffmpeg, funasr, torchaudio — with known pitfalls)
3. **Extract key frames** (`scripts/extract_frames.py` → WORKDIR)
4. **Transcribe + punctuate** (SenseVoiceSmall via funasr → WORKDIR)
5. **Design topic sections** — read the transcript, write `WORKDIR/sections.json`
6. **Assemble markdown** (`scripts/assemble_transcript.py` → deliverable dir)
7. **Verify** (sections render, duration matches, text reaches the end; delete WORKDIR)

WORKDIR below always means the /tmp scratch dir; the deliverable dir is where the
video lives. Use `scripts/` under this skill's base directory for steps 3 and 6. Do
the transcription inline (step 4) — it needs environment-specific handling.

## Step 1: Get the video

If the user gave a local video path, use it directly. If they gave a Bilibili
URL or a bare BV number (e.g. `BV1pb8o6yE8f`, accept it with or without the
`BV` prefix and strip query params like `vd_source`), load and follow the
**bilibili-video-download** skill to download the video first, then continue
with the local file. Note the video title/creator from that step — they go
into the document header. Prefer 720P; anonymous download tops out there and
is fine for transcription.

## Step 2: Tool setup (read the pitfalls, don't rediscover them)

```bash
python3 -c "import imageio_ffmpeg" || pip3 install -q imageio-ffmpeg
python3 -c "import funasr"        || pip3 install -q funasr modelscope
python3 -c "import torchaudio"    || pip3 install -q torchaudio
```

Hard-won environment notes:

- **ffmpeg**: Homebrew's ghcr download may fail (`HTTP/2 PROTOCOL_ERROR`) on
  filtered networks. The pip package `imageio-ffmpeg` ships a static binary —
  use `imageio_ffmpeg.get_ffmpeg_exe()`. **funasr-adjacent tools (whisper
  variants) shell out to `ffmpeg` on PATH**, so symlink it:
  `ln -sf "$(python3 -c 'import imageio_ffmpeg; print(imageio_ffmpeg.get_ffmpeg_exe())')" ~/.local/bin/ffmpeg`
- **torchaudio must match the installed torch version** (e.g. torch 2.5.1 →
  `pip3 install torchaudio==2.5.1`) or funasr fails with "needs one fbank
  backend".
- **scipy**: if `import scipy` dies with a dlopen `__thread_bss` error,
  `pip3 install -U scipy` fixes it.
- **huggingface.co may be blocked** (SSL EOF on API calls; `hf-mirror.com` is
  unreliable too). `huggingface.co/*/resolve/...` file downloads and
  `modelscope.cn` usually still work. Never block on hub API calls — prefer
  ModelScope-hosted models (funasr uses them by default via `iic/...` ids).

## Step 3: Extract key frames

```bash
python3 <skill_dir>/scripts/extract_frames.py VIDEO.mp4 WORKDIR --uniform-interval 90
```

Scene-change detection (threshold 0.25) plus a uniform sample every 180 s,
deduplicated within 45 s, renamed to `keyframes/fr_<second>.jpg`. The uniform
safety net matters: slide decks often change without triggering scene
detection (e.g. a fixed classroom camera). For slide-dense videos, lower
`--uniform-interval` to 90 or `--scene-threshold` to 0.15.

## Step 4: Transcribe and punctuate

Chinese (and most Chinese-mixed) content: use SenseVoiceSmall through funasr —
better Mandarin accuracy than Whisper large-v3-turbo and it integrates a
punctuation model. Run in the background; expect roughly real-time speed on
CPU (a 100-min lecture took ~9 min; download of the ~1 GB of models happens on
first run and can take a few minutes).

```bash
python3 - <<'EOF'
import json
from funasr import AutoModel
model = AutoModel(
    model='iic/SenseVoiceSmall',
    vad_model='fsmn-vad',
    vad_kwargs={'max_single_segment_time': 30000},
    punc_model='ct-punc',
    device='cpu',
    disable_update=True,
)
res = model.generate(input='WORKDIR/audio.wav', batch_size_s=60, hotword='')
si = res[0]['sentence_info']
groups, cur = [], None
for s in si:
    t = s['text'].strip()
    if not t: continue
    if cur and s['start'] - cur['end'] <= 1500 and s['end'] - cur['start'] < 90000:
        cur['end'] = s['end']; cur['parts'].append(t)
    else:
        if cur: groups.append(cur)
        cur = {'start': s['start'], 'end': s['end'], 'parts': [t]}
if cur: groups.append(cur)
punc = AutoModel(model='ct-punc', disable_update=True, device='cpu')
out = []
for g in groups:
    text = punc.generate(input=''.join(g['parts']))[0]['text']
    out.append({'start': g['start'], 'end': g['end'], 'text': text})
json.dump(out, open('WORKDIR/punctuated.json', 'w'), ensure_ascii=False)
print('paragraphs:', len(out))
EOF
```

(Extract audio first: `ffmpeg -i VIDEO.mp4 -vn -ac 1 -ar 16000 WORKDIR/audio.wav`.)

Why the two-stage punctuation: `generate(..., sentence_timestamp=True)` returns
timestamped sentences **without** punctuation (it warns "punctuation timestamps
could not be aligned, falling back to VAD segments"), while the plain `text`
field has punctuation but no timestamps. Grouping VAD sentences by gap (≤1.5 s)
into ~90 s paragraphs and punctuating each group gives both. Timestamps are
milliseconds in the output.

**Long-video pitfall (hit on a 98-min lecture)**: `model.generate(input=audio.wav)`
on the full file can return only `{'key', 'text'}` — no `sentence_info`, no
timestamps, despite transcribing perfectly. When `res[0]` lacks `sentence_info`,
don't re-run the same call; switch to **chunked inference**: run `fsmn-vad`
standalone over the full audio, merge its `value` segments (≤1.5 s gaps, <90 s
spans) into groups, slice `audio.wav` per group with soundfile, and run the
SenseVoice model per slice — `sentence_info` timestamps are slice-relative, so
add the group's global offset. A group that still returns no `sentence_info`
just contributes its punctuated whole-group text with the group's timestamps.
Save the raw single-pass result to `sensevoice_raw.json` first so you can
compare coverage; the chunked result should end at the same ms and start/end
with the same text.

For **English-only** content, funasr's Chinese models are a poor fit — use
whisper instead (`pip3 install -q mlx-whisper` on Apple Silicon, model
`mlx-community/whisper-large-v3-turbo`), and skip the punctuation step.

## Step 5: Design topic sections

Read the punctuated transcript (skim `punctuated.json`, or pretty-print the first
~80 chars of each paragraph with its start time) and decide where the video changes
topic. Write `WORKDIR/sections.json`: a **short list (aim for 5–15 for an hour-long
video, roughly one section per 5–10 minutes)** of titled topic sections:

```json
[
  {"title": "开场：从 PC 到 AI 的时代背景", "start": 0},
  {"title": "这门课讲什么：生成式软件工程", "start": 441000},
  {"title": "课程政策：免费 token 与通过制考核", "start": 870000}
]
```

Rules of thumb:

- Titles describe the *topic*, not the time ("政策：考核方式" beats "第 12 分钟").
  Write them in the video's language.
- Section boundaries go at topic changes, not sentence gaps — ignore the paragraph
  segmentation when choosing them; a section usually spans many paragraphs.
- First section must start at 0 (or ≤2 s before the first paragraph); timestamps
  are milliseconds, copied from the paragraph where the topic changes.
- Every section needs at least one paragraph starting inside it. If the assembler
  reports an empty section, merge it into a neighbor or nudge its start to the
  next paragraph's start.
- Match the video's actual structure: a lecture with clear parts gets one section
  per part; a monologue video might need only 3–5 sections total.

## Step 6: Assemble the markdown document

```bash
python3 <skill_dir>/scripts/assemble_transcript.py \
  --workdir WORKDIR --title "VIDEO TITLE" --course "COURSE/CONTEXT" \
  --creator "UPLOADER" --source-url "https://www.bilibili.com/video/BV..." \
  --outdir "DELIVERABLE_DIR" --embed-images
```

Reads `WORKDIR/punctuated.json` + `WORKDIR/sections.json`, writes
`DELIVERABLE_DIR/transcript.md`: header with metadata, a short TOC of section titles
with time ranges, then one `## <n>. <title> [start–end]` block per section containing
the punctuated paragraphs and screenshots placed at their timestamps.

Output modes: `--embed-images` base64-embeds the screenshots (single self-contained
file — the right choice when WORKDIR is a throwaway /tmp dir and only transcript.md
should survive). Without it, pass `--outdir` to copy the referenced keyframes next to
the markdown; with neither, the markdown is written inside WORKDIR with relative
image links.

## Step 7: Verify

- Markdown renders: section headings and TOC present; `grep -c '^## ' transcript.md`
  matches the number of sections.
- Last section's end timestamp ≈ the video duration (from step 1 metadata).
- Read the first and last ~20 lines: text should start with real content and end
  when the video ends (not cut off mid-sentence).
- Delete the /tmp WORKDIR (`rm -rf WORKDIR`) — with `--embed-images` the deliverable
  is fully self-contained, nothing in it is needed anymore. Also clean up any other
  temp dirs from the download step.

## Common pitfalls (all hit in practice)

- **Transcription produces no punctuation** → you used raw Whisper/sentence_info
  output; run the ct-punc grouping step (Step 4).
- **Transcript organized into dozens of tiny timestamp sections** → you skipped
  Step 5; write a `sections.json` with a few titled topic sections first.
- **`FileNotFoundError: 'ffmpeg'` from a transcription tool** → symlink the
  imageio-ffmpeg binary onto PATH (Step 2).
- **`load_npz ... must be a zip file`** when loading a local Whisper model →
  mlx-whisper expects the weights file named `weights.safetensors` (plus
  `config.json`) in the model dir, not `model.safetensors`. Rename it.
- **`KeyError: 'sentence_info'` after a long transcription** → the full-file
  generate returned punctuated text but no timestamps; switch to the chunked
  VAD-group inference described in Step 4 (don't re-run the same call).
- **funasr `torchaudio is not installed`** → version-matched torchaudio (Step 2).
- **Verify ends with**: text should start with real content and end when the video
  ends (not cut off mid-sentence); last section's end ≈ video duration.
- **Don't pollute the deliverable directory**: intermediates (audio.wav ~180 MB per
  100 min, keyframes, punctuated.json, sections.json) belong in the /tmp WORKDIR;
  delete it after verification. 360P-quality video (small, slides-readable) →
  screenshots may be unreadable; prefer 720P for slide-dense lectures.
- **Truncated-looking transcripts** → check the audio extraction covered the
  full file (`audio.wav` duration vs video) before blaming the model.
