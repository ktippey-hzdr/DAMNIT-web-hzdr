"""Read one frame exactly as OpenCV's ``IMREAD_UNCHANGED`` would, through Pillow.

shot-aligner's reader, ``img_csv.read_image``, is ``cv2.imread(path,
cv2.IMREAD_UNCHANGED)``. DAMNIT has no OpenCV and adds none: Pillow is already
installed (matplotlib needs it) and reads every format the packs claim -- PNG,
TIFF, BMP -- without converting the pixel values. What differs is only the
presentation, and that is undone here:

* 16-bit greyscale comes back as ``uint16`` in native byte order (Pillow keeps
  a big-endian TIFF big-endian);
* colour is reordered to OpenCV's BGR / BGRA;
* a palette image is expanded to BGR (BGRA with transparency), as libpng
  expands it for OpenCV, and a bilevel image becomes ``uint8`` 0/255;
* a grey + alpha image becomes BGRA, its grey repeated in three channels.

**One deliberate refusal.** Pillow cannot hold more than 8 bits per channel in
a colour image and would silently truncate a 16-bit RGB frame, where OpenCV
keeps all 16. Such a frame is reported unreadable (``None``) rather than
written wrong. None of the campaign's formats is one; a pack that meets one
reports the file as unreadable, which is the honest answer here.

Like ``read_image``, a file that does not decode returns ``None``. Only the
one frame is held in memory.
"""

from __future__ import annotations

import struct
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from pathlib import Path

# PNG colour types that carry more than one channel.
_PNG_COLOUR = {2, 3, 4, 6}
# TIFF tags.
_BITS_PER_SAMPLE = 258
_SAMPLES_PER_PIXEL = 277


def _deep_colour_png(path: Path) -> bool:
    """A PNG with more than 8 bits per channel and more than one channel."""
    with path.open("rb") as stream:
        head = stream.read(26)
    if head[:8] != b"\x89PNG\r\n\x1a\n" or head[12:16] != b"IHDR":
        return False
    bit_depth, colour_type = struct.unpack(">BB", head[24:26])
    return bit_depth > 8 and colour_type in _PNG_COLOUR


def _deep_colour_tiff(image) -> bool:
    tags = getattr(image, "tag_v2", None)
    if tags is None:
        return False
    samples = int(tags.get(_SAMPLES_PER_PIXEL, 1) or 1)
    bits = tags.get(_BITS_PER_SAMPLE, (8,))
    bits = bits if isinstance(bits, tuple) else (bits,)
    return samples > 1 and max(int(b) for b in bits) > 8


def _as_opencv(image) -> np.ndarray | None:
    """Pillow's decoded frame in OpenCV's layout and dtype."""
    mode = image.mode
    if mode == "P":
        image = image.convert("RGBA" if "transparency" in image.info else "RGB")
        mode = image.mode
    elif mode == "PA":
        image = image.convert("RGBA")
        mode = "RGBA"
    data = np.asarray(image)
    if mode == "1":
        return data.astype(np.uint8) * np.uint8(255)
    if mode == "RGB":
        return np.ascontiguousarray(data[..., ::-1])
    if mode == "RGBA":
        return np.ascontiguousarray(data[..., [2, 1, 0, 3]])
    if mode == "LA":
        grey, alpha = data[..., 0], data[..., 1]
        return np.ascontiguousarray(np.stack([grey, grey, grey, alpha], axis=-1))
    if mode in {"L", "I", "F"} or mode.startswith("I;16"):
        native = data.dtype.newbyteorder("=")
        return data.astype(native, copy=False) if data.dtype != native else data
    return None


def read_image(path: Path) -> np.ndarray | None:
    """One frame, or ``None`` when it cannot be read as OpenCV would read it."""
    from PIL import Image

    try:
        if path.suffix.lower() == ".png" and _deep_colour_png(path):
            return None
        with Image.open(path) as image:
            if _deep_colour_tiff(image):
                return None
            image.load()
            return _as_opencv(image)
    except (OSError, ValueError, SyntaxError, Image.DecompressionBombError):
        return None
