"""The writing helpers a diagnostic pack and the container builder both need.

These lived in :mod:`nexus_build` and are here so that a pack under
``diagnostics/`` can use them without importing the module that imports *it*.
Nothing about them changed in the move, and ``nexus_build`` re-exports every
name, so ``from shotalign.nexus_build import _safe`` still resolves.

What belongs here: transforms and name rules that are true of any diagnostic --
how a label becomes a NeXus group, how a frame is shrunk for display, how a
plot asks to be opened, where a staged copy of a file is. What does not:
anything that knows a file *format*.
That is a pack's, and keeping the line there is what stops this module growing
back into the pile it was split out of.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from functools import lru_cache

import numpy as np


def container_groups(entry: dict, instrument: str) -> tuple[str, str]:
    """The two group names one diagnostic is written under: machine, detector.

    ``entry/<machine>/<detector>``, the way NeXus models an instrument with
    several detectors and the way Polina's containers were built: one
    `Diffraction Screen` NXinstrument holding the 400, 515 and 800 nm cameras
    as three NXdetectors. It used to be one NXinstrument per diagnostic
    folder, with the machine surviving only as a `diagnostic_group` label no
    NeXus reader interprets.

    With a declared family (the campaign config's ``group``) the machine is
    the family and the detector is the diagnostic's ``instrumentName``, which
    is unique per diagnostic -- ``detectorName`` is not ("Diffraction Camera"
    three times), so it becomes the detector's ``description``. Without one,
    the diagnostic is its own machine and keeps the layout it always had,
    ``entry/<instrumentName>/<detectorName>``: nothing says what it belongs
    to, and inventing a family would be a claim about the beamline.

    Both the writer and the mapping rows derive paths from here, so they
    cannot disagree.
    """
    name = entry.get("instrumentName") or instrument
    if entry.get("group"):
        return _safe(entry["group"]), _safe(name)
    return _safe(name), _safe(entry.get("detectorName") or "detector")


def _safe(name: str) -> str:
    """A NeXus-safe group name that still reads like the operator's label.

    NeXus names are letters, digits and underscore, starting with a letter or an
    underscore, at most 63 characters -- stricter than HDF5, which would take
    almost anything. Two operator labels fall outside it: a hyphen ("Off-Axis
    Parabola Imaging 400 nm") is not a name character, and "515 Reflected Light
    Spectrometer" starts with a digit. Both are kept readable rather than
    renamed -- the hyphen becomes an underscore and a leading digit gains the
    underscore NeXus allows -- and the operator's own wording stays intact in
    the group's ``name`` field, which is a value and may contain anything.
    """
    cleaned = "".join(c if c.isalnum() and c.isascii() or c == "_" else "_"
                      for c in name.strip())
    while "__" in cleaned:
        cleaned = cleaned.replace("__", "_")
    cleaned = cleaned.strip("_")
    if not cleaned:
        return "unnamed"
    if cleaned[0].isdigit():
        cleaned = "_" + cleaned
    return cleaned[:63]


# ---- the display transform -------------------------------------------
#
# One definition, three callers: ``display_copy`` below, which writes what goes
# into a container, and the review app's two preview endpoints. It was written
# out three times before this and the copies had begun to differ -- the app
# resampled by mean where the container resampled by maximum -- so a person
# comparing a source frame against the frame built from it was comparing two
# transforms as well as two files. Range, scale and colour are now one
# definition; only the last resampling step still differs, because a container
# takes whatever whole blocks land on and a page needs an exact width.

PERCENTILES = (0.5, 99.9)
GAMMA = 0.45
SCALES = ("linear", "gamma", "log")

# The perceptually uniform maps, under their matplotlib names. Every one is
# built into OpenCV, which is already a runtime dependency; matplotlib is not
# -- it arrives only with silx in the optional ``viewer`` group, which CI does
# not install -- so reaching for it here would break the tested environment to
# get colours we already have. "gray" is the absence of a map, not one of them.
COLORMAPS = ("gray", "viridis", "plasma", "inferno", "magma", "cividis",
             "turbo")


def display_range(image, vmin=None, vmax=None, percentiles=PERCENTILES):
    """The low and high counts a frame is drawn between.

    A value given is used as it is: a hand-set range is a decision and is not
    second-guessed, including one that clips. Where none is given the
    percentiles decide, which is what makes an unattended thumbnail show
    something -- these frames put their signal at the bottom of a 16-bit range,
    and min/max autoscaling hands the whole scale to one hot pixel.
    """
    low = float(np.percentile(image, percentiles[0])) if vmin is None else float(vmin)
    high = float(np.percentile(image, percentiles[1])) if vmax is None else float(vmax)
    if high <= low:
        high = low + 1.0
    return low, high


def stretch(image, low: float, high: float, scale: str = "gamma"):
    """A frame mapped onto 0-255 on one of three scales.

    ``linear`` draws the measurement as it is. ``gamma`` lifts the low end the
    way sRGB encoding does; it is the default because on a focal-spot frame
    only a few percent of pixels carry signal and a linear stretch still reads
    as black. ``log`` is for what a gamma cannot open up -- four orders of
    magnitude between a line and its background.

    The log rule is stated rather than fudged, because these arrays reach zero
    and the dark-subtracted ones go negative, and the logarithm of those is not
    a number. Counts are taken *above the low end* and one is added:

        log10(1 + x - low) / log10(1 + high - low)

    defined for every input, monotonic, ``low`` maps to 0 and ``high`` to 255.
    It is therefore **not** the logarithm of the counts, and it moves with the
    range -- a reader who needs the measurement has ``raw_data``.

    An unknown scale falls back to the gamma rather than raising: this is a
    display path, and refusing to draw is worse than drawing the default.
    """
    span = high - low
    above = np.clip(image.astype(np.float32) - low, 0, span)
    if scale == "log":
        unit = np.log10(1.0 + above) / np.log10(1.0 + span)
    elif scale == "linear":
        unit = above / span
    else:
        unit = np.power(above / span, GAMMA)
    return (np.clip(unit, 0, 1) * 255).astype(np.uint8)


def colorize(gray, cmap: str = "gray"):
    """A greyscale frame through a colormap, as BGR -- or untouched for "gray".

    OpenCV's tables are the matplotlib ones under the same names, so "viridis"
    here and ``viridis`` in a paper figure are the same colours. An unknown
    name is left grey rather than guessed at, and the caller is told which map
    was used so the picture can say so.

    Colour is a *viewing* choice and stays out of the containers: the stored
    display copy is greyscale with ``interpretation="image"``, which is three
    times smaller and lets every viewer apply its own map.
    """
    if cmap == "gray" or cmap not in COLORMAPS:
        return gray
    import cv2

    return cv2.applyColorMap(gray, getattr(cv2, f"COLORMAP_{cmap.upper()}"))


def render(image, *, vmin=None, vmax=None, percentiles=PERCENTILES,
           scale: str = "gamma", cmap: str = "gray", width: int | None = None):
    """A frame as a picture: range, scale, size, colour. The whole transform.

    Order matters and is not the obvious one. The frame is reduced to 8 bits
    *before* it is downscaled, so blocks are compared on the scale they will be
    shown on, and the colormap is applied *after*, so a block is never reduced
    channel by channel.

    Returns the range it was drawn between along with the array, because every
    caller has to be able to say what that range was. A picture whose scale is
    not reported is not evidence.
    """
    low, high = display_range(image, vmin, vmax, percentiles)
    out = stretch(image, low, high, scale)
    if width is not None:
        out = fit(out, width)
    return colorize(out, cmap), low, high


def display_copy(image, width: int = 1024) -> tuple:
    """An 8-bit, contrast-stretched, downscaled copy of a frame, for a container.

    These cameras write 16-bit PNGs whose signal sits at the bottom of the
    range -- a full-energy Lanex frame has a mean of 11 and a 99.9th percentile
    of 48 out of 65535 -- so a viewer drawing the stored values on a linear
    scale shows black, and says the file is broken when it is not. It is a
    **display artefact**, written beside the measurement and labelled as such,
    never in place of it.

    The defaults are deliberately the plain ones -- percentile range, gamma,
    greyscale. What is written into a container is not the place to record a
    reviewer's choice of colours; the review app is where a range is explored,
    and ``raw_data`` is what it is explored from.

    It reduces by whole blocks and takes whatever width that lands on, where
    the app's previews are fitted to an exact one. Nothing is laying this out
    in a column, and a container built today should hold the same array as one
    built before any of this was configurable.
    """
    out, low, high = render(image)
    return downscale(out, width), low, high


def thin(values, points: int):
    """Shrink a curve to about ``points`` samples, keeping each block's peak."""
    factor = max(1, int(np.ceil(len(values) / points)))
    if factor == 1:
        return values
    usable = (len(values) // factor) * factor
    return values[:usable].reshape(-1, factor).max(axis=1)


def reduce_blocks(image, factor: int):
    """Shrink by whole pixel blocks, keeping each block's brightest pixel.

    Maximum rather than mean: on a focal spot only a few percent of pixels
    carry signal, and averaging them away is how a real feature disappears
    from a thumbnail.
    """
    if factor <= 1:
        return image
    rows = (image.shape[-2] // factor) * factor
    cols = (image.shape[-1] // factor) * factor
    return image[..., :rows, :cols].reshape(
        *image.shape[:-2], rows // factor, factor, cols // factor, factor
    ).max(axis=(-3, -1))


def downscale(image, width: int):
    """Shrink by whole-pixel blocks to *at most* ``width``."""
    return reduce_blocks(image, max(1, int(np.ceil(image.shape[-1] / width))))


def _edges(length: int, count: int):
    """Where ``count`` bins start, spread as evenly as whole pixels allow."""
    return np.arange(count) * length // count


def pool_to(image, rows: int, cols: int):
    """Reduce to exactly ``rows`` x ``cols``, keeping each bin's brightest pixel.

    ``reduce_blocks`` can only divide, so it reaches 968 or 484 but never the
    640 a page asked for. This takes the maximum over bins that need not
    divide: bin edges at ``i * length // count``, which differ in width by at
    most one pixel. Same rule, arbitrary target.
    """
    out = image
    if rows < out.shape[-2]:
        out = np.maximum.reduceat(out, _edges(out.shape[-2], rows), axis=-2)
    if cols < out.shape[-1]:
        out = np.maximum.reduceat(out, _edges(out.shape[-1], cols), axis=-1)
    return out


def fit(image, width: int):
    """Shrink to exactly ``width``, still keeping each bin's brightest pixel.

    Why an exact width and not whole blocks: the review app sizes every
    thumbnail to its column with CSS ``width: 100%``, so a frame that
    undershoots is scaled back up by the browser and arrives blurred.

    Why not simply resample: an area resample -- averaging -- is what
    ``reduce_blocks`` exists to avoid. It was tried here and measured, and it
    does exactly the damage the block maximum was guarding against. On a 900 px
    field with a single 255 pixel, asked for at 640, whole blocks cannot
    divide, so the whole reduction falls to the average and the peak comes out
    at 181. This keeps it at 255. A focal spot is a few pixels of a frame, and
    a strip of thumbnails that loses it is not showing the shot.

    A frame already at or under the width is returned untouched. Nothing here
    ever enlarges -- a preview of a small frame is that frame.
    """
    if image.shape[-1] <= width:
        return image
    height = max(1, round(image.shape[-2] * width / image.shape[-1]))
    return pool_to(image, height, width)


# ---- how a container asks to be opened -------------------------------
#
# silx reads ``SILX_style`` off an NXdata group as a JSON object and applies
# ``signal_scale_type`` as the signal's scale: ``io/nxdata/parse.py`` parses it
# and ``gui/data/DataViews.py`` applies it -- as the colormap's normalisation
# for an image, as the y axis for a curve. It carries no colormap name, so the
# palette stays a viewer setting whatever is written here.
#
# It is written where it is *warranted*, never everywhere. A spectrometer's
# measurement earns it: four orders of magnitude between the line and the
# background is a property of the measurement, not a preference, and a viewer
# opening it on a linear scale draws a spike on an empty axis. A camera frame
# does not, and a hint on every dataset would be a default dressed up as a
# statement about the data -- so a pack that declares nothing gets nothing, and
# the attribute's presence stays a statement rather than a setting.
#
# Never on a display copy. ``display/preview`` is already stretched between its
# percentiles, and a log scale on top of that composes two transforms and
# reports neither.

SILX_STYLE = "SILX_style"

# silx's own vocabulary, from ``ScaleType`` in ``silx/io/nxdata/_utils.py``.
# Deliberately **not** ``SCALES`` above: that is the review app's display
# transform, which offers a gamma silx has no name for and has no use for an
# asinh. Two lists because they are two vocabularies, and collapsing them would
# let "gamma" reach a container attribute -- where silx logs and ignores it,
# which is the one failure nobody would see.
SCALE_TYPES = ("linear", "log", "asinh")


def set_scale_hint(group, scale: str | None) -> None:
    """Ask a viewer to open this NXdata on ``scale``. No scale writes nothing.

    An unrecognised scale also writes nothing rather than raising. A manifest
    cannot get one this far -- the pack registry refuses to load a pack whose
    declared scale is not one silx knows, which is where a typo should be
    reported -- so this is the belt: writing a value silx will log and discard
    is worse than writing no attribute at all, because the container then
    carries a claim that does nothing.
    """
    if not scale or scale not in SCALE_TYPES:
        return
    group.attrs[SILX_STYLE] = json.dumps({"signal_scale_type": scale})


def scale_hint(group) -> str | None:
    """The scale an NXdata asks for, or None where it asks for nothing.

    Exists so the hint can be *carried* as well as written: the entry-level
    ``entry/data`` a container opens on is a link to a detector's plot, and a
    reader that resolves the default chain lands there rather than on the
    detector, so a hint left only on the detector would be a hint nothing
    reads. Reading it back rather than re-deriving it keeps one answer.

    Anything unparseable reads as no hint, which is what silx itself does with
    it. A malformed attribute is not this function's to report.
    """
    raw = group.attrs.get(SILX_STYLE)
    if raw is None:
        return None
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", "replace")
    try:
        style = json.loads(str(raw))
    except ValueError:
        return None
    scale = style.get("signal_scale_type") if isinstance(style, dict) else None
    return scale if scale in SCALE_TYPES else None


# ---- how a file inside the data root is written down ------------------
#
# These live here, with `source_of`, rather than in `records` where the index
# is built: a diagnostic pack resolves recorded paths and imports this module,
# and `records` imports `diagnostics`, so the rule has to sit in the leaf that
# neither of them can cycle through.

@lru_cache(maxsize=16)
def _resolved_root(root: str) -> Path:
    return Path(root).resolve()


def recorded(path, root) -> str:
    """A file inside the data root, as an index writes it: relative, with "/".

    ``os.path.relpath`` spells the separator natively, so an index written on
    Windows used to carry ``BAM\\shot.png``. Read back on Linux that is not
    two path components -- it is one file name containing a backslash, which
    does not exist -- so the index resolved to nothing and every frame was
    reported missing. This repository is developed on Windows and deployed on
    Linux, and an index is a file people copy between them.

    One spelling, and it is the one both systems accept: Windows takes ``/``
    in every path API.
    """
    try:
        relative = Path(path).relative_to(root)
    except ValueError:
        # Windows may report files on a mapped drive by their UNC target while
        # the configured root still says Z:. Resolve the root to the same
        # spelling before deciding the file lies outside it.
        target = _resolved_root(str(root))
        try:
            relative = Path(path).relative_to(target)
        except ValueError:
            # A companion folder's file (S2): a date read from two folders
            # records the second one's files through `..`, which `under` joins
            # back to the same file. Same share only, which is the only case --
            # companions are siblings. Tried textually before anything is
            # resolved: `resolve()` is a round trip per file on sshfs, and
            # 15,424 of them took minutes. A file the text cannot place (a
            # different spelling of the same mount) is resolved as before.
            for here, there in ((path, root), (path, target)):
                try:
                    step = Path(os.path.relpath(here, there))
                except ValueError:
                    continue
                if step.parts and step.parts[0] == "..":
                    return step.as_posix()
            resolved = Path(path).resolve()
            try:
                relative = resolved.relative_to(target)
            except ValueError:
                return Path(os.path.relpath(resolved, target)).as_posix()
    return relative.as_posix()


def split_recorded(relative: str) -> tuple[str, ...]:
    """A recorded path as its components, whichever separator wrote it.

    Pure, and separate from `under` on purpose: which components a path has is
    the half of this that can be checked on any machine, and the cross-platform
    promise is exactly that half. Everything below it is a filesystem question.
    """
    return tuple(part for part in relative.replace("\\", "/").split("/") if part)


def under(root: Path, relative: str) -> Path:
    """A recorded path joined to a root, however its separators are spelled.

    New indexes carry ``/`` and this is then an ordinary join on both systems.
    The backslash spelling is still accepted because indexes written before
    `recorded` existed are on disk and reading one does not rewrite it.

    The literal join is tried **first**, and that ordering is the whole
    safeguard: a backslash is a legal character in a POSIX file name, so a
    file really called ``odd\\name.png`` must keep winning over the guess that
    it meant two components. Only when nothing is there is the path re-split.
    On Windows the question does not arise -- a backslash *is* the separator,
    so the literal join already found the file.
    """
    direct = Path(root) / relative
    if "\\" not in relative or direct.exists():
        return direct
    return Path(root).joinpath(*split_recorded(relative))


def recorded_name(relative: str) -> str:
    """The bare file name of a recorded path, however it is spelled.

    ``Path(relative).name`` cannot do this: on Linux it answers the whole of
    ``BAM\\shot.png``, which would then be written into a container as the
    name of a sidecar file.
    """
    parts = split_recorded(relative)
    return parts[-1] if parts else ""


def source_of(root: Path, cache: Path | None, relative: str) -> Path:
    """The staged copy of a file if there is one, otherwise the original.

    Alignment always reads the whole date from wherever it lives; only the
    build reads pixels, and only the shots someone chose. Staging those few
    locally and taking them from here is what makes a mounted data root
    workable -- and it is transparent: the same index describes both copies,
    and a missing staged file simply falls back to the source.
    """
    if cache is not None and under(cache, relative).is_file():
        return under(cache, relative)
    return under(root, relative)
