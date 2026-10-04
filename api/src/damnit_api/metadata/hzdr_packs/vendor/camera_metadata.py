"""Typed views of known camera settings, without guessing missing units.

The original CSV dictionary remains the source evidence. Conversion supplies
additional fields; it never replaces that dictionary or changes image pixels.
"""

import math
import re


SETTINGS = {
    "Exposure": ("exposure", {"s", "ms", "us", "µs"}),
    "Gain": ("gain", {"dB"}),
    "Black Level Offset": ("black_level_offset", {""}),
    "Gamma": ("gamma", {""}),
    "Chip Size X": ("chip_size_x", {"mm", "µm", "um"}),
    "Chip Size Y": ("chip_size_y", {"mm", "µm", "um"}),
}
NUMBER_WITH_UNIT = re.compile(
    r"^([+-]?(?:\d+(?:[.,]\d*)?|[.,]\d+)(?:[eE][+-]?\d+)?)\s*(.*?)$"
)


def typed_settings(metadata: dict) -> tuple[dict, list[str]]:
    """Return validated numeric settings and problems for unconverted values."""
    typed, problems = {}, []
    for key, (name, allowed_units) in SETTINGS.items():
        source = metadata.get(key, "")
        if not source:
            continue
        match = NUMBER_WITH_UNIT.fullmatch(source.strip())
        if match:
            value = float(match[1].replace(",", "."))
            unit = match[2]
            if math.isfinite(value) and unit in allowed_units:
                typed[name] = {"value": value, "units": unit,
                               "source_key": key, "source_text": source}
                continue
        problems.append(f"camera setting {key!r} retained as text: {source!r}")
    return typed, problems


# --- the vendor analysis block ---------------------------------------------
#
# `img_csv.read_csv_metadata_file` skips the first twelve lines of a camera
# CSV, which is where the acquisition tool writes its own measurements. Most of
# those rows are empty in this campaign, but the Peak Profile fit on the Focus
# mini cameras is a real per-shot focal-spot measurement, and it is the one
# thing in the sidecar that cannot be recovered from the stored image later:
# the fit's ROI is an operator-drawn polygon whose vertices the CSV never
# records. See governed-keys.md.

ANALYSIS_TOOLS = (
    "Profile", "Position", "Peak", "Centroid", "Peak Profile",
    "Statistics", "Histogram", "Noise Reduction", "Offset", "Contrast",
)

# Vendor label -> (dataset name, expected unit). Deliberately partial.
#
# The `µ horiz`/`µ vert` columns are left out on purpose. µ is the Gaussian
# centre, not a width: µ horiz agrees with `Pos x` in 170 of this campaign's 211
# fits, which makes it a position in the same ROI frame whose meaning `Pos x`
# itself is not settled in (governed-keys.md), and µ vert is frozen at 1542.938
# on every Focus mini 800 frame, so it is not a centre either. Both stay in
# original_metadata as text and get no dataset.
FIT_FIELDS = {
    "FWHM horiz": ("fwhm_horiz", "mm"),
    "FWHM vert": ("fwhm_vert", "mm"),
    "1/e\u00b2 horiz": ("width_1e2_horiz", "mm"),
    "1/e\u00b2 vert": ("width_1e2_vert", "mm"),
    "Amplitude horiz": ("amplitude_horiz", "a.u."),
    "Amplitude vert": ("amplitude_vert", "a.u."),
    "Offset horiz": ("offset_horiz", "a.u."),
    "Offset vert": ("offset_vert", "a.u."),
}
FIT_PARAMETERS = {
    "ROI": "roi",
    "Peak Determination Method": "peak_determination_method",
    "Averaging": "averaging",
}


# How far the two axes' amplitudes may disagree before the fit is called
# degenerate. Measured over the 211 fits in the December 2025 campaign, the
# population is bimodal: median agreement is 1.05 and the 90th percentile is
# already 31.7, so a threshold anywhere between 2 and 10 selects within four of
# the same set and the exact value is not load-bearing. Five is the middle of
# that indifferent band.
AXIS_DISAGREEMENT = 5.0


def fit_quality(values: dict) -> dict:
    """Whether a beam-profile fit is worth believing, and why not when it is not.

    The vendor exports a failed fit exactly like a good one -- there is no
    status column -- so about one in six of this campaign's fits is degenerate
    and nothing in the file says so. A consumer that averages the amplitude
    column without checking gets a meaningless number.

    Two tests, both of which a working fit passes trivially:

    * **an amplitude at or below zero.** A Gaussian fitted to a peak has a
      positive height above its baseline; zero means the fitter found nothing.
      Five of 211 frames.
    * **the two axes disagreeing.** Horizontal and vertical are independent
      1-D cuts through the same peak, so their amplitudes should be close --
      median agreement is 1.05 across the campaign. Twenty-nine frames exceed
      ``AXIS_DISAGREEMENT``, which means one of the two cuts failed.

    Returns ``{"degenerate", "reason", "axis_ratio"}``, or ``{}`` when there is
    no amplitude pair to judge. It never drops or alters a value: the numbers
    are written as exported and this is recorded beside them.
    """
    horizontal = (values.get("amplitude_horiz") or {}).get("value")
    vertical = (values.get("amplitude_vert") or {}).get("value")
    if horizontal is None or vertical is None:
        return {}

    reasons = []
    if min(horizontal, vertical) <= 0:
        reasons.append("an amplitude at or below zero: the fitter found no peak")
    ratio = None
    if min(horizontal, vertical) > 0:
        ratio = max(horizontal, vertical) / min(horizontal, vertical)
        if ratio > AXIS_DISAGREEMENT:
            reasons.append(
                f"the horizontal and vertical amplitudes disagree by {ratio:.0f}x, "
                f"where a working fit agrees to about 1.05; one of the two 1-D "
                f"cuts failed")
    return {
        "degenerate": bool(reasons),
        "reason": "; ".join(reasons),
        "axis_ratio": ratio,
    }


def analysis_blocks(path) -> dict:
    """Parse the analysis-tool block at the top of a camera CSV.

    Returns ``{tool: {field: (text, unit)}}``, holding only rows that carry a
    value. A file whose first cell is not ``Tool`` returns ``{}`` rather than
    guessing at a layout this has not seen.
    """
    import csv as _csv

    blocks: dict = {}
    with open(path, encoding="cp1252", newline="") as source:
        reader = _csv.reader(source, delimiter=";")
        header = next(reader, None)
        if not header or header[0].strip() != "Tool":
            return {}
        for row in reader:
            if not row:
                continue
            tool = row[0].strip()
            if tool not in ANALYSIS_TOOLS:
                continue
            rest, fields = row[1:], {}
            for i in range(0, len(rest) - 2, 3):
                name, text, unit = (rest[i].strip(), rest[i + 1].strip(),
                                    rest[i + 2].strip())
                if name and text:
                    fields[name] = (text, unit)
            if fields:
                blocks.setdefault(tool, fields)
    return blocks




# --- the Statistics tool ----------------------------------------------------
#
# The second analysis tool this campaign actually ran: 186 of the 2468 sidecars
# carry it, against 361 for Peak Profile and 2420 for Contrast. Unlike the beam
# fit, everything here *can* be recomputed from the stored frame -- except that
# it is computed over the operator's ROI, whose vertices the CSV never records,
# so "over what" is exactly the part that would be lost. That is why the ROI
# parameter is kept beside the numbers rather than dropped as furniture.
#
# Dataset names are the profile's own semantics without its roi_ prefix, since
# the group already says what they are over. beam_profiler.yaml routes them to
# /entry/process/results/roi_sum, roi_max, roi_mean, roi_std_dev and
# roi_pixel_count.
STATISTICS_FIELDS = {
    "Sum": ("sum", "a.u."),
    "Max": ("max", "a.u."),
    "Mean": ("mean", "a.u."),
    "Std Dev": ("std_dev", "a.u."),
    "Number of Pixels": ("pixel_count", "Pixel"),
}
STATISTICS_PARAMETERS = {
    "ROI": "roi",
}


def roi_statistics(blocks: dict) -> tuple[dict, dict, list[str]]:
    """Split a Statistics block into typed values and its ROI parameter.

    The same shape and the same refusals as ``beam_profile_fit``: a value whose
    unit is not the one expected is reported and left as text rather than
    converted, because a silent guess about a unit is the kind of error that
    survives every later check.
    """
    return _typed_block(blocks.get("Statistics") or {}, "ROI statistics",
                        STATISTICS_FIELDS, STATISTICS_PARAMETERS)


def _typed_block(fields: dict, label: str, wanted: dict,
                 parameters: dict) -> tuple[dict, dict, list[str]]:
    """One vendor analysis block as typed values, text parameters and problems."""
    values, problems = {}, []
    for key, (name, expected) in wanted.items():
        if key not in fields:
            continue
        text, unit = fields[key]
        match = NUMBER_WITH_UNIT.fullmatch(text.strip())
        if not match:
            problems.append(f"{label} {key!r} retained as text: {text!r}")
            continue
        value = float(match[1].replace(",", "."))
        unit = unit or match[2] or expected
        if not math.isfinite(value) or unit != expected:
            problems.append(
                f"{label} {key!r} not in {expected}: {text!r} [{unit}]")
            continue
        values[name] = {"value": value, "units": unit,
                        "source_key": key, "source_text": text}
    kept = {parameters[k]: fields[k][0] for k in parameters if k in fields}
    return values, kept, problems


def beam_profile_fit(blocks: dict) -> tuple[dict, dict, list[str]]:
    """Split a Peak Profile block into typed fit values and fit parameters.

    Returns ``({dataset: {value, units, source_key, source_text}}, {parameter:
    text}, problems)``. An empty first element means this acquisition carries no
    fitted widths -- the position-only case, which most cameras write.
    """
    return _typed_block(blocks.get("Peak Profile") or {}, "beam-profile fit",
                        FIT_FIELDS, FIT_PARAMETERS)
