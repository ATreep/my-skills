---
name: video-transcript
description: Create a readable transcript of any video — a local video file OR a Bilibili URL/BV number — with timestamped, punctuated, polished text and key-frame screenshots saved as image files in an assets/ subfolder next to the markdown. Use whenever the user asks to transcribe, subtitle, or make a readable notes/summary document from a video referenced by file path, full Bilibili URL, or bare BV id (e.g. "BV1pb8o6yE8f"). For Bilibili inputs, first loads the bilibili-video-download skill to fetch the video.
---

# Video → Readable Transcript with Key Frames

Produce a readable markdown transcript: the full talk in the source language,
punctuated, **polished for readability** (fillers/stutters removed), and organized
into a few **titled topic sections** (not one section per timestamp), with
key-frame screenshots saved as image files in an `assets/` subfolder next to the
markdown and referenced by relative path `assets/xxx.jpg`. **Never base64-embed
images into the markdown.** Verified end-to-end on a 100-minute Mandarin course
lecture (720P MP4) on a heavily firewalled network (2026-09).

**File placement**: keep the video, the final `transcript.md`, and an `assets/`
subfolder together in the deliverable directory (e.g. `L2_提示词工程/`);
create **all intermediates in a /tmp scratch dir** (`WORKDIR=$(mktemp -d
/tmp/vt_XXXX)` — audio.wav, keyframes, punctuated.json, sections.json). Only the
final markdown and the referenced screenshot files in `assets/` go to the
deliverable dir. After `--outdir ... --assets-subdir assets` the markdown quotes
each screenshot by relative path `assets/xxx.jpg`, so the deliverable is the
directory as a whole; the scratch dir can be deleted right after verification.

## Pipeline overview

1. **Get the video** (skip if given a local file)
2. **Set up tools** (ffmpeg, funasr, torchaudio — with known pitfalls)
3. **Extract key frames** (`scripts/extract_frames.py` → WORKDIR)
4. **Transcribe + punctuate** (SenseVoiceSmall via funasr → WORKDIR)
5. **Design topic sections** — read the transcript, write `WORKDIR/sections.json`
6. **Polish the transcript** — remove fillers/stutters, fix ASR garbles (a
   dedicated editing pass over the raw text, in WORKDIR)
7. **Assemble markdown** (`scripts/assemble_transcript.py` → deliverable dir,
   screenshots copied into `assets/`)
8. **Verify** (sections render, images resolve, duration matches; delete WORKDIR)

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

## Step 6: Polish the transcript (mandatory editing pass)

Raw ASR output of a live lecture is full of trivial modal particles, stutters,
and verbal debris that make it painful to read — e.g. one 100-min Mandarin
lecture had ~400 呃, ~250 然后, ~180 对吧, plus stutters like 我我我 and
sentences that trail off into 呃……. After transcription, run a **dedicated
polishing pass over `punctuated.json`** before assembly:

1. Work paragraph by paragraph (keep each paragraph's `start`/`end` timestamps
   untouched — only edit `text`).
2. **Remove** trivial modal particles and filler words when they carry no
   meaning: 呃、嗯、啊、哈、那个、这个 (as pure fillers), 就是这样、对吧、
   好不好、什么的话、OK/ok used as verbal padding, 转折填充 like
   然后呢/呃然后.
3. **Fix stutters and repetitions**: 我我我 → 我; 是一个呃这这是一个 → 这是一个.
   Keep intentional repetition used for emphasis.
4. **Keep** content words, the speaker's actual voice, technical terms,
   rhetorical questions, humor, and sentence-final 吧/呢/啊 when they express
   genuine mood (e.g. 算了吧, 好不好？as a real question to the audience).
   A talk transcript should still sound like spoken language — do not rewrite
   it into formal written prose or drop disfluencies so aggressively that the
   flow becomes stilted.
5. **Fix obvious ASR garbles** where the intended word is clear from context
   (e.g. homophone errors like 通过制/通知制 if the slides/context make it
   unambiguous); leave uncertain spots as-is rather than guessing.
6. Optionally merge/trim empty sentences a paragraph shrinks to nothing —
   delete such paragraphs entirely.

Polishing is LLM editing work, not a regex find-replace: a script stripping
every 啊/吧 would also destroy real questions and mood. Do it yourself in the
response (or via a careful per-paragraph pass), write the result back to
`WORKDIR/punctuated.json` (same schema), and sanity-check: timestamps
unchanged, no paragraph lost or added, filler count (呃/嗯 etc.) near zero,
text still reads as natural spoken language.

## Step 7: Assemble the markdown document

```bash
python3 <skill_dir>/scripts/assemble_transcript.py \
  --workdir WORKDIR --title "VIDEO TITLE" --course "COURSE/CONTEXT" \
  --creator "UPLOADER" --source-url "https://www.bilibili.com/video/BV..." \
  --outdir "DELIVERABLE_DIR" --assets-subdir assets
```

Reads `WORKDIR/punctuated.json` + `WORKDIR/sections.json`, writes
`DELIVERABLE_DIR/transcript.md`: header with metadata, a short TOC of section titles
with time ranges, then one `## <n>. <title> [start–end]` block per section containing
the polished paragraphs and screenshots placed at their timestamps.

Image handling: `--outdir DIR --assets-subdir assets` copies the referenced
keyframes into `DELIVERABLE_DIR/assets/` and quotes them in the markdown by
relative path `assets/xxx.jpg` — this is the required mode; **never base64-embed
images into the markdown** (a 100-min lecture embeds to a 6 MB unreadable file;
image files stay diff-able, viewable, and load instantly). With `--outdir` and no
`--assets-subdir` the images land in `DELIVERABLE_DIR/keyframes/` with
`keyframes/...` links. With neither, the markdown is written inside WORKDIR with
relative image links. `--embed-images` (base64 data URIs) exists only for legacy
compatibility — do not use it.

## Step 8: Verify

- Markdown renders: section headings and TOC present; `grep -c '^## ' transcript.md`
  matches the number of sections.
- **Images resolve**: every `![...]` link points at an existing file under
  `assets/` (relative path from the markdown), and the count matches the
  referenced keyframes; `grep -c 'base64' transcript.md` is 0.
- Last section's end timestamp ≈ the video duration (from step 1 metadata).
- Read the first and last ~20 lines: text should start with real content and end
  when the video ends (not cut off mid-sentence), and should read smoothly —
  no 呃/嗯/我我我 left.
- Delete the /tmp WORKDIR (`rm -rf WORKDIR`) — the deliverable dir holds
  everything needed (markdown + assets). Also clean up any other
  temp dirs from the download step.

## Common pitfalls (all hit in practice)

- **Transcription produces no punctuation** → you used raw Whisper/sentence_info
  output; run the ct-punc grouping step (Step 4).
- **Transcript is unreadable: 呃/嗯/我我我 everywhere** → you skipped Step 6;
  polish the paragraphs before assembly. Don't fix it with a global regex —
  fillers must be judged in context by an editing pass.
- **Transcript organized into dozens of tiny timestamp sections** → you skipped
  Step 5; write a `sections.json` with a few titled topic sections first.
- **Markdown ballooned to megabytes and won't open in editors** → the images
  were base64-embedded; use `--assets-subdir assets` file links instead.
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
