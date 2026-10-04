# Reference fixture: one shot, three packs

The container DAMNIT's builder has to reproduce (HZDR_combo
`planning/NEXUS_OUTPUT_PLAN.md`, phase 1). Written by
`shot_aligner/scripts/make_reference_fixture.py` and checked by
`tests/test_reference_fixture.py`. DAMNIT-web-hzdr vendors this folder
(`api/tests/fixtures/hzdr-reference/`, kept in step by its
`hzdr/scripts/sync-hzdr-reference.{ps1,sh}`; `SOURCE.json` there pins the
commit it was copied from). Every file here is compared byte for byte, so
`.gitattributes` marks the folder `-text`.

| Path | What | Origin |
| --- | --- | --- |
| `raw/M1_Spec_Fib_Cer/` | camera PNG + CSV (`camera_png_csv`) | real, `polina/2025_12_01/` shot 2, 15:59:04 |
| `raw/Reflected 515 spectrometer/` | Irr8 spectrum (`spectrometer_irr8`) | real, same shot |
| `raw/Probe135/` | two-frame TIFF recording (`sequence_frames`) | synthetic: no sample is committed |
| `events.jsonl` | the `hzdr-event-v1` events planet-watchdog would send | paths recorded as `/bigdata/HPLexp/reference-fixture/...` |
| `manifest.json` | every link name in the container `build_shot` writes | regenerated from `raw/`, values left out |

## What the manifest records

Every link name in the file, not every object: a dataset hard-linked under two
names (`raw_data/image` and `data/image`) is listed under both. Names that
reach one object are grouped; the lexicographically first is canonical and
each other one carries `"link": "<canonical path>"`. Soft and external links
would be recorded as `softlink`/`externallink` with their target (this
container has none). Per node: `NX_class`, and for datasets shape, dtype and
units; where present, `signal`, `axes`, `default` and `nds_definition` (the
application definition a mapping claims for a detector). No values.

## What `build_shot` is fed

The alignment `report.run` would write for these files: `records.scan` over
`raw/`, the recording stamped by `conditions.stamp`, each acquisition
serialised by `report._serialise_event`. Only the grouping into one event
and its link to shotsheet row 1042 are given, because there is no workbook.
So the camera has `seq` 2 and the spectrum none (their names say so), both
with a `filename` clock.

**Probe135 is a conditions recording.** `20cm_6kv_00001.tif` follows the
`recording` naming (`sequence_frames.py`, 2025_02_24): a label of conditions
and a frame number within one recording of one shot. `claim()` gives it no
`seq` and no clock, so its two frames are one acquisition (`key`
`Probe135|20cm_6kv`), stacked as one `[2, 4, 5]` image. With no `.rec`
beside it, `conditions.stamp` dates it by the first frame's mtime
(`timeSource: mtime`), as it would on real data.

The configuration agrees with this: `config/instruments.json` declares
Probe135 `timing: sequence`, `framesPerShot: 2`, and the DRACO catalogue
`attribution: cadence`, `frames_per_shot: 2`. The cadence numbers
`setN_NNNNN.tif` ordinals and does not apply to a conditions recording in
shot-aligner. On the watchdog side it does apply, and a two-frame recording
is exactly one cadence group, so both sides put the two frames on one shot.

## The events

The shape of planet-watchdog's `kafka_output.build_hzdr_event` for a rule
that opts in to attribution, replicated rather than imported:

- `experiment_id` is `unassigned` (decision D1); the configured campaign is
  `metadata.watch.configured_experiment_id`.
- `kind` is `watchdog.<watch_name>`, not the pack. **The pack is
  `metadata.instrument.format`**, and that is what a consumer routes on.
- `event_id` is `watchdog-<24 hex>`, from the identity hash watchdog uses.
- `payload_ref` is the dumped `HZDRPayloadRef`: `path`, `uri`, `filename`,
  `sha256`, `size_bytes`, `zmq_topic` (the attached trigger's topic,
  `Draco01`), unset fields as `null`. Only a rule with
  `group_by_stem` adds `members`, sorted by path, so the camera's CSV comes
  before its PNG; the primary file (`payload_ref.path`) is the measurement
  file (`_original.png`), not `members[0]`.
- One event per acquisition the rule emits: the camera's PNG + CSV are one
  event (grouped by stem, waiting for `.csv`); the spectrum is one; each
  Probe135 frame is its own event, since the frame numbers make the stems
  differ. Frame 1 is attributed by trigger window (`attribution.method`
  `cadence`, with a `delta_s`); frame 2 inherits it with `delta_s` `null`.
- `acquisition.time_source` is `first_seen`, the only clock a live watcher
  has; `metadata` also carries `producer`, `shot_number_provenance` and the
  trigger's `zmq_data`.

The catalogue leaves `latency_max_s` unset for all three and has no
`ordinal_regex` for Probe135, so the generator assumes watch rules that
supply them (`INSTRUMENTS[*]["rule"]`).

The catalogue lists the Reflected 515 spectrometer as `producer:
"unassigned"`: no live producer parses Irr8 yet. Its event is the one a
watch rule would send once one does.

## Which part of the manifest is the contract

For DAMNIT, the contract is the instrument data: the `NXinstrument` families,
their `NXdetector`s and what is under them. These are not part of it:

- `/entry/alignment`: shot-aligner's clock and link evidence. The live flow
  keeps attribution in `/entry/source_events`.
- `/entry/build_provenance`, `/entry/program_name`, `/entry/user`: who built
  it, with what.
- `/entry/shot_info`, `/entry/shot_parameters`, `/entry/shotsheet_provenance`:
  the workbook row. DAMNIT takes it from LabFrog.
- `/entry/title`, `/entry/start_time`, `/entry/entry_identifier`,
  `/entry/collection_identifier`: shot-aligner's naming of the shot. The title
  reads the date's project from this checkout's indexes (`Vanlife` here).
  The December beamtime's `experiment_description` and
  `experiment_documentation` are kept out of the build entirely.
- `/entry/data` and the entry's `default`: the entry-level plot. Decision 6
  keeps only each detector's default plot.
- The `NXsubentry` groups (`/entry/Reflected_515_Spectrometer`,
  `/entry/_515_Reflected_Light_Spectrometer`) and the `nds_definition`
  attributes: application-definition claims by the mappings, until phase 2
  reconciles the mappings (decision 5; `NXxrd_pan` for a camera goes).

## Known gaps, recorded on purpose

`build_shot` reports these in `/entry/alignment/problems`. They are true of
this input, and they are left as they are:

- The real M1 CSV lacks three `peak_profile` parameters its mapping names.
- The synthetic Probe135 recording has no `.rec` sidecar, so the 11 rows its
  mapping reads from it are empty.
- Probe135's mapping claims `NXoptical_spectroscopy` for a camera
  (`nds_definition` on `/entry/Probe_135_deg/pco_Camera`), and the M1
  camera's claims `NXxrd_pan` (on its detector and its `NXsubentry`). Both
  are mapping errors the plan's phase 2 fixes.

## Changing it

```sh
uv run shot_aligner/scripts/make_reference_fixture.py          # manifest from raw/
uv run shot_aligner/scripts/make_reference_fixture.py --events # events too, raw/ untouched
uv run shot_aligner/scripts/make_reference_fixture.py --raws   # raw/ and events too
```

A manifest change is a change to what containers contain. Re-vendor it into
DAMNIT in the same breath (`hzdr/scripts/sync-hzdr-reference.sh --apply`
there), and re-pin `SOURCE.json` to the commit on `main` once this merges.
