---
name: bilibili-video-download
description: Download videos from Bilibili (bilibili.com) URLs or BV ids. Use whenever the user asks to download, save, or grab a Bilibili video — including when yt-dlp fails with HTTP Error 412 or other anti-bot blocks on www.bilibili.com pages.
---

# Download Bilibili Videos

## Overview

The standard `yt-dlp` flow fails on many networks because Bilibili's HTML pages
return **HTTP 412 (Precondition Failed)** — an IP-level anti-bot block. The key
insight from a verified successful download (2026-09): **Bilibili's JSON API
endpoints are not blocked even when the web pages are.** So bypass the page
entirely and talk to the API, then download the stream with `curl`.

Quality trade-off: anonymous API access tops out at **720P** (single progressive
MP4, no merging needed). Higher resolutions (1080P/4K, DASH audio+video) require
login cookies. Only attempt the cookie path if the user needs >720P.

## Workflow

### Step 1: Try yt-dlp first (short, cheap)

```bash
yt-dlp -f "bv*+ba/b" --merge-output-format mp4 -o "%(title)s.%(ext)s" "<URL>"
```

If yt-dlp is missing, install with `pip3 install -q yt-dlp`.

If it fails with `HTTP Error 412` (with or without custom `User-Agent`/`Referer`
headers — both were tried and both failed), do NOT keep retrying yt-dlp against
the page. Jump straight to the API path.

Quick confirmation of the block pattern (page blocked, API not):

```bash
curl -s -o /dev/null -w "%{http_code}\n" "https://www.bilibili.com/video/<BVID>"   # 412 = page blocked
curl -s -o /dev/null -w "%{http_code}\n" "https://api.bilibili.com/x/web-interface/view?bvid=<BVID>"  # 200 = API open
```

### Step 2: Get video metadata and cid from the view API

```bash
curl -s "https://api.bilibili.com/x/web-interface/view?bvid=<BVID>" | python3 -c "
import json,sys
d=json.load(sys.stdin)['data']
print('title:', d['title'])
print('duration(s):', d['duration'])
print('pages:', len(d['pages']))
for p in d['pages']: print(' cid:', p['cid'], p['part'])
"
```

- `bvid` is the `BV...` string from the URL (strip query params like `vd_source`).
- Multi-part (分P) videos return several pages; each page has its own `cid`.
  Download each page separately (Step 3 per cid) or confirm with the user which
  parts they want.

### Step 3: Get the stream URL from the playurl API

```bash
curl -s -A "Mozilla/5.0" -e "https://www.bilibili.com" \
  "https://api.bilibili.com/x/player/playurl?bvid=<BVID>&cid=<CID>&qn=64&platform=html5&high_quality=1" \
  | python3 -c "import json,sys; print(json.load(sys.stdin)['data']['durl'][0]['url'])"
```

Key parameters:

- `platform=html5&high_quality=1` — the HTML5 player path; gives a single
  progressive MP4 (no separate audio track to merge, no ffmpeg needed).
- `qn=64` requests 720P. Anonymous access accepts at most 64; the response's
  `accept_quality` / `quality` fields show what was actually granted. If you
  omit `high_quality=1` you may silently get 360P — always check the
  `quality` field in the response and `accept_description`.
- The returned URL is a signed, time-limited CDN link (`deadline` param).
  Use it promptly; re-query if it expires.

### Step 4: Download with curl

The CDN rejects requests without a `Referer` header (403) — always send these:

```bash
curl -sL \
  -A "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36" \
  -e "https://www.bilibili.com" \
  -o "<sanitized-title>.mp4" "<STREAM_URL>"
```

Filenames from video titles can contain characters like `[`, `]`, `/`, spaces —
sanitize them rather than using the raw title.

### Step 5: Verify the file

```bash
file <output>.mp4                       # expect: ISO Media, MP4 Base Media v1
mdls -name kMDItemDurationSeconds -name kMDItemPixelWidth <output>.mp4   # macOS
```

- Duration should match the API's `duration` (within a second or two); width
  1280 confirms 720P. A tiny file or `file` reporting HTML/JSON means the CDN
  rejected the request — re-check the `Referer`/`User-Agent` headers or the
  signed URL's expiry.

## Higher quality (>720P) with login cookies

If the user needs 1080P+ and is logged into bilibili.com in a browser:

```bash
yt-dlp --cookies-from-browser chrome -f "bv*+ba/b" --merge-output-format mp4 \
  -o "%(title)s.%(ext)s" "<URL>"
```

This only works if the HTML pages themselves are reachable (the 412 block may
still apply). Requires ffmpeg for DASH merging. Ask the user which browser
their Bilibili login lives in; do not guess.

## Common pitfalls

- **Retrying yt-dlp on a 412** — the block is per-IP on the web page route;
  header tricks don't fix it. Go to the API.
- **Omitting `-e "https://www.bilibili.com"` on the CDN download** — silent 403.
- **Forgetting `high_quality=1`** — silently downgraded to 360P.
- **Multi-part videos** — one playurl call per cid; `pages` from the view API
  lists all cids.
