---
name: image-compress
description: Compress an image file to under a target size (default 1MB) by re-encoding as JPEG at quality 95 with 4:4:4 chroma via Pillow. Use whenever the user asks to compress, shrink, or reduce an image file's size.
---

# Compress Images to a Target Size

## Overview

Produce a high-quality JPEG at **quality 95, 4:4:4 chroma (subsampling=0),
optimize=True** with Pillow. Verified result: a 1.6MB 1254×1254 PNG compressed
to 477KB with visually negligible loss — comfortably under a 1MB target.

Use Pillow, not macOS `sips`: sips' JPEG quality flags are unreliable and
produced inconsistent output in testing (256 and 512 quality settings gave
byte-identical files; separate `formatOptions` flags were ignored).

## Workflow

### Step 1: Inspect the input

```bash
python3 -c "
from PIL import Image
img = Image.open('<input>')
print('mode:', img.mode, 'size:', img.size)
"
```

Notes:
- `RGBA`/`P`/`LA` modes mean the image has transparency. JPEG cannot store
  alpha — flattening onto white (or another background the user chooses) is
  a visible change. Tell the user before doing it, or ask.
- `RGB` (or `L`) is safe to convert directly.

### Step 2: Encode as JPEG quality 95

```python
from PIL import Image

img = Image.open("<input>")
if img.mode in ("RGBA", "P", "LA"):
    img = img.convert("RGB")   # flattens transparency; see Step 1
elif img.mode != "RGB":
    img = img.convert("RGB")
img.save("<output>.jpg", quality=95, subsampling=0, optimize=True)
```

- `subsampling=0` keeps full chroma resolution — avoids color fringing on
  edges/text/line art.
- `optimize=True` enables Huffman table optimization (smaller file, same
  quality, no speed concern for one image).
- Name the output from the input stem (e.g. `avatar.png` → `avatar.jpg` or
  `avatar-compressed.jpg`); never overwrite the original unless asked.

### Step 3: Verify size and quality

```bash
ls -lh <output>.jpg
```

- If under target (default 1MB): done. Report the before/after sizes.
- Optional objective quality check against the original (needs both readable
  by Pillow; skip if the original had alpha since modes will differ):

```python
import math
import numpy as np
from PIL import Image

a = np.asarray(Image.open("<input>").convert("RGB"), dtype=np.float64)
b = np.asarray(Image.open("<output>.jpg"), dtype=np.float64)
mse = ((a - b) ** 2).mean()
print(f"PSNR: {10 * math.log10(255**2 / mse):.1f} dB" if mse > 0 else "identical")
```

30+ dB is visually near-identical; q95/4:4:4 typically lands well above that.

### Step 4: Fallbacks if still over target

In order, each step re-checks size:

1. **quality 90, subsampling=0** — still near-lossless (the same test image
   hit 367KB at q90 vs 488KB at q95).
2. **quality 85, subsampling=2** — visibly fine for photos, much smaller.
3. **Resize** (last resort, changes resolution — confirm with user first):

   ```python
   img = img.resize((int(img.width * s), int(img.height * s)))
   ```

   Solve for scale roughly proportional to the size overshoot; JPEG size
   scales about linearly with pixel count.

## Common pitfalls

- **Using `sips`** — its quality flags don't map reliably to actual JPEG
  quality; use Pillow for predictable, controllable output.
- **Forgetting `.convert("RGB")`** — Pillow raises `OSError: cannot write
  mode RGBA as JPEG` on transparent PNGs.
- **Default subsampling (4:2:0)** — silent color degradation on sharp edges
  and text; always pass `subsampling=0` unless the fallback tier says
  otherwise.
- **Overwriting the original** — keep the source file intact; write a new
  `.jpg` alongside it.
- **Trust but verify** — always `ls -lh` the output before reporting
  success; quality 95 can still exceed the target on very large/detailed
  images (e.g. 4000×4000 photos), which is what Step 4 is for.
