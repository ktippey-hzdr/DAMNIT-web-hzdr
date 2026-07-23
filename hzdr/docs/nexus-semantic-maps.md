# NeXus Semantic Maps — `laser.*`, `vacuum.*`, `diagnostic.*`

Updated: 2026-07-18

The versioned definition of the non-target semantic maps the NeXus bridge
writer implements: how the registry namespaces `metadata.laser.*`,
`metadata.vacuum.*`, and `metadata.diagnostic.*` land in the canonical
campaign NeXus file, using **standard NeXus classes** (`NXsource`, `NXbeam`,
`NXdata`, `NXenvironment`, `NXdetector`) — no HZDR-local class is defined
here, unlike the target map's `NXhzdr_target`.

This document is the laser/vacuum/diagnostic companion to
[nxhzdr-target-profile.md](nxhzdr-target-profile.md) (the `metadata.target.*`
→ `/entry/sample` map). Together the two documents are the normative prose
reference for the `NXhzdr_target` application definition
(`hzdr/nxdl/NXhzdr_target.nxdl.xml`), which since profile **v0.6** covers all
groups described here in addition to the sample group.

**Versioning:** this document does not carry its own version number. Any
semantic change to the maps below (field added/removed/retyped, unit changed,
placement moved) bumps the shared profile version — `HZDR_TARGET_PROFILE_VERSION`
in `api/src/damnit_api/metadata/hzdr_nexus.py`, the NXDL enumeration, and the
version/history in [nxhzdr-target-profile.md §4](nxhzdr-target-profile.md#4-versioning-rule)
— exactly as for the target map. The canonical units cited below are fixed by
the metadata key registry (`METADATA_KEY_REGISTRY` in
`api/src/damnit_api/metadata/hzdr_event.py`; human-readable copy in
[CLAUDE.md](../../CLAUDE.md)); the writer stamps them as `@units` at write
time and never hardcodes a unit string.

Related: [standards-alignment.md §3.3/§3.5–3.7](standards-alignment.md#33-laser-parameters)
(HELPMI cross-walk and the group/class mapping these maps implement),
[target-ontology.md §5](target-ontology.md#5-units-convention) (bare-key units
convention, family-wide since 2026-07-02).

---

## 1. Campaign-level vs per-shot

Laser, target, and vacuum blocks have **campaign-level snapshot groups**
from the first shot carrying each block. A later non-empty difference logs one
warning (`_first_shot_laser` / `_first_shot_vacuum` /
`_first_shot_target` in `hzdr_nexus.py`). Since profile v0.8, target is
also preserved losslessly per shot as sorted JSON in
`/entry/shots/target_metadata_json`; `/entry/sample` remains the standard
`NXsample` snapshot. Laser and vacuum differences remain warning-only.
Two other families are inherently per-shot and are written as full
shot-indexed series:

- every **numeric** `metadata.laser.<key>` → the `shot_series` `NXdata` group
  (§2.3) — energy jitter is data, not noise;
- every `metadata.diagnostic.<key>` → one `NXdetector` series group per key
  (§4.1).

## 2. Laser map: `metadata.laser.*` → `/entry/instrument/laser`

Writer: `write_nexus_laser_group()` (snapshot) and
`write_nexus_laser_shot_series()` (series) in
`api/src/damnit_api/metadata/hzdr_nexus.py`. Fields absent or `null` are
skipped entirely — never written as empty datasets.

### 2.1 `NXsource` group (`/entry/instrument/laser`)

| Source | NeXus path (under `/entry/instrument/laser`) | Canonical unit | Upstream NeXus field? |
| --- | --- | --- | --- |
| constant | `type` = `"Laser"` | — | Standard `NXsource.type` |
| constant | `probe` = `"visible light"` | — | Standard `NXsource.probe` enum value |
| `laser.system` | `name` | — (string) | Standard `NXsource.name` |
| `laser.repetition_rate` | `frequency`, `@units` | Hz | Standard `NXsource.frequency` |
| `laser.pulse_energy` | `pulse_energy`, `@units` | J | Standard `NXsource.pulse_energy` |

### 2.2 Nested `NXbeam` group (`/entry/instrument/laser/beam`)

The beam group is nested **inside** the source group — a documented deviation
from the more common sibling placement; see
[nxhzdr-target-profile.md §5](nxhzdr-target-profile.md#5-known-deviations).

| Source | NeXus path (under `.../laser/beam`) | Canonical unit | Upstream NeXus field? |
| --- | --- | --- | --- |
| `laser.pulse_energy` | `incident_energy`, `@units` | J | Standard `NXbeam.incident_energy` |
| `laser.pulse_duration` | `pulse_duration`, `@units` | fs | Standard `NXbeam.pulse_duration`; DRACO's typical value is about 30 fs, interpreted as intensity FWHM |
| `laser.wavelength` | `incident_wavelength`, `@units` | nm | Standard `NXbeam.incident_wavelength` |
| `laser.polarization` | `incident_polarization` | — (string enum) | Standard field name (`NXbeam.incident_polarization` is formally a vector); DRACO's signed label is `p`, meaning the electric field lies in the plane of incidence |
| `laser.beam_pos_x` / `beam_pos_y` | `beam_position_x` / `beam_position_y`, `@units` | mm | **Profile extension** — no standard `NXbeam` field |
| `laser.beam_waist_x` / `beam_waist_y` | `beam_waist_x_1e2_radius` / `beam_waist_y_1e2_radius`, `@units` | um | Facility meaning is the focal-spot radius at 1/e² intensity, normally symmetric for DRACO (about 1.5–2.25 um radius from a 3–4.5 um diameter). The explicit HZDR profile fields are not interchangeable with generic `NXbeam.extent`; writer and NXDL implement them since profile v0.9. |
| `laser.contrast_ratio` | `contrast_ratio`, `@units` | — (dimensionless) | **Profile extension** — no standard `NXbeam` field |

These meanings were signed for the Semantic Test Baseline on 2026-07-23.
DRACO commonly operates with p-polarization at oblique incidence (often about
45 degrees) for TNSA experiments. The signed NDS v0.2 profile is the intent
authority. Since profile v0.9, `write_nexus_laser_group()` and
`NXhzdr_target` use the explicit 1/e²-radius dataset names and do not emit the
old generic `extent_x`/`extent_y` aliases.

### 2.3 Per-shot series (`/entry/instrument/laser/shot_series`, `NXdata`)

Every numeric `metadata.laser.<key>` seen on **any** shot becomes a
shot-indexed dataset aligned with the canonical `/entry/shots` table, `NaN`
where a shot lacks the key, `@units` from the registry. The group carries
`signal` (`pulse_energy` when present, else the first key alphabetically),
`axes = "shot_index"`, `auxiliary_signals` for the rest, and a `shot_index`
axis dataset. String-valued keys (`system`, `polarization`) stay
campaign-level in the parent `NXsource` group.

## 3. Vacuum map: `metadata.vacuum.*` → `/entry/sample/environment`

Writer: `write_nexus_vacuum_group()`. The chamber vacuum describes the sample
surroundings, so it lands under `NXsample` as an `NXenvironment` group — the
canonical placement the nexus-design-studio catalog assigns the class. The
group is a campaign-level snapshot (§1). When the sample group does not exist
yet (vacuum-only campaign), the writer stamps the `NXhzdr_target` profile
marker attributes on `/entry/sample` so the file stays certifiable.

| Source | NeXus path (under `/entry/sample/environment`) | Canonical unit | Upstream NeXus field? |
| --- | --- | --- | --- |
| constant | `description` = `"target chamber vacuum"` | — | Standard `NXenvironment.description` |
| `vacuum.chamber_pressure` | `chamber_pressure`, `@units` | mbar | **Profile extension** — pressure fields live on `NXsensor` upstream; HZDR writes the scalar directly |
| `vacuum.pre_shot_pressure` | `pre_shot_pressure`, `@units` | mbar | **Profile extension** |
| `vacuum.rga_dominant_species` | `rga_dominant_species` | — (string) | **Profile extension** |

## 4. Diagnostic map: `metadata.diagnostic.*` and data products → `NXdetector`

`NXdetector` is the nexus-design-studio ruling for the `diagnostic.*` registry
namespace; multiple `NXdetector` groups under `NXinstrument` is standard
NeXus. Two writers emit detector groups, distinguished by their
`damnit_source` marker attribute (which also protects preserved LabFrog
groups from being overwritten — a name collision with a non-DAMNIT group is
skipped with a warning, never clobbered):

### 4.1 Per-diagnostic series (`/entry/instrument/<key>`, `damnit_source="metadata.diagnostic"`)

Writer: `write_nexus_diagnostic_groups()`. One group per
`metadata.diagnostic.<key>`, each with a shot-indexed `data` dataset aligned
with `/entry/shots` (`NaN` where a numeric key is absent on a shot, `""` for
string series), a `coordinates = "/entry/shots/shot_index"` attribute, and
`@units` from the registry entry `diagnostic.<key>`.

**The `diagnostic.*` namespace is registry-governed (2026-07-18).** Registered
keys:

| Registry key | Canonical unit | Meaning |
| --- | --- | --- |
| `diagnostic.xray_counts` | counts | Integrated X-ray detector counts for the shot |
| `diagnostic.detector_signal_mean` | — (arbitrary/dimensionless) | Mean detector signal level |
| `diagnostic.alignment_score` | — (dimensionless, 0–1) | Automated alignment quality score |

A new diagnostic scalar must be added to `METADATA_KEY_REGISTRY` (and the
CLAUDE.md registry table) **before** a producer emits it. An unregistered key
is still written — the namespace stays open — but without `@units`, and
`lint_metadata_keys()` warns about it. The pre-namespace flat spellings
(`metadata.xray_counts` etc.) are folded into `metadata.diagnostic.*` by the
writer and flagged as legacy by the linter (`LEGACY_KEY_MAP`).

### 4.2 Per-product-kind references (`/entry/instrument/detector_<kind>`, `damnit_source="data_products"`)

Writer: `write_nexus_detector_groups()`. `/entry/data_products` stays the flat
transport table; per data-product `kind` a `detector_<kind>` group carries
`product_ids` / `shot_keys` / `file_paths` / `dataset_names` reference
datasets back to the rows, plus a `detector_type` attribute when the kind maps
to a known tag:

| Product kind | `detector_type` |
| --- | --- |
| `streak_camera` | `STREAK` |
| `proton_spectrometer` | `POS` |
| `thomson_parabola` | `THOMSON` |
| `frog` | `FROG` |
| `scintillator` | `SCINT` |

Generic transport kinds (`hdf5_dataset`, `file`) get no detector group — they
say how a product arrived, not what recorded it. An unknown kind still gets
its group, just without a `detector_type` claim.

## 5. Campaign time bounds (`/entry/start_time`, `/entry/end_time`)

Writer: `_write_campaign_time_bounds()` (since profile v0.6). Standard
`NXentry` fields derived from the earliest/latest parseable shot `fired_at`,
refreshed on every rebuild as the campaign grows. The datasets carry
`damnit_source="shots"`; an existing dataset **without** that marker (e.g.
written by a future LabFrog projection) is preserved, mirroring the group
claim rule in §4.

## 6. Certification

`nds validate <file.nxs> --pynxtools --definitions hzdr/nxdl` certifies a
bridge file against the `NXhzdr_target` application definition declared in
`/entry/definition`. Since v0.6 that covers every group in this document; all
content fields are optional (the writer skips absent metadata), so
certification checks structure, enumerations, and the profile marker
attributes rather than presence.

## Status

| Item | Status |
| --- | --- |
| Laser snapshot (`NXsource` + nested `NXbeam`) | ✅ implemented 2026-07-02 |
| Per-shot laser series (`shot_series` `NXdata`) | ✅ implemented |
| Vacuum snapshot (`NXenvironment` under sample) | ✅ implemented |
| Per-diagnostic `NXdetector` series | ✅ implemented |
| Per-product-kind `NXdetector` reference groups | ✅ implemented |
| `diagnostic.*` registry governance (keys + linter warning) | ✅ 2026-07-18 |
| `start_time` / `end_time` entry fields | ✅ 2026-07-18 (profile v0.6) |
| NXDL coverage of all groups above | ✅ 2026-07-18 (profile v0.6) |
| Semantic map doc for these namespaces | ✅ this doc (2026-07-18) |
