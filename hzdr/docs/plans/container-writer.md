# Container writer (campaign output, phase 3)

Design note, 2026-10-04. Phase 3 of HZDR_combo `planning/NEXUS_OUTPUT_PLAN.md`
("Container writer"); the repository-level summary is
[campaign-output-plan.md](campaign-output-plan.md). It settles how DAMNIT turns
the events of one shot into `shots/<YYYYMMDD>_<shot_number>.nxs`, held to the
reference fixture's manifest (`api/tests/fixtures/hzdr-reference/`). Phase 4
links the containers from the master, phase 5 validates them; neither is here.

## 1. Product row or join: **join `source_events` by `event_id`**

The converter reads the published master's `/entry/source_events` table
(`event_id`, `shot_key`, `payload_ref_json`, `metadata_json`) and its
`/entry/shots` table (`shot_key`, `fired_at`, LabFrog columns). Every fact a
pack needs is already there: `metadata.instrument.{id,format,timing_role}`,
`metadata.acquisition.{time,time_source}`, and `payload_ref.members` with each
member's `sha256`. A `/entry/data_products` row of kind `file` keeps its
`metadata_json.event_id`, which is the join key back to the same row.

Why: `data_products` stays a flat table of references, nothing is stored twice,
and the bridge profile stays `hzdr-canonical-shot-v5`. The master is also the
one place where matching, campaign resolution and review rulings have already
decided which shot an event belongs to, so the worker never re-runs matching
and cannot disagree with the master it will be linked from.

The worker opens the master read-only, reads both tables, and closes it before
converting anything, so a long conversion never holds the master open (on
Windows an open handle would block the builder's atomic rename).

## 2. Grouping events into acquisitions

Per shot (`shot_key`) and instrument (`metadata.instrument.id`), the files of
all its events (`payload_ref.members`, or `payload_ref.path` alone) are grouped
by the **acquisition key** shot-aligner's `claim()` gives each file name
(section 3). Files with the same key are one acquisition:

- a camera's PNG and its CSV sidecar share a stem, so they are one acquisition
  (watchdog already sends them as one event with two members);
- a recording named by its conditions (`20cm_6kv_00001.tif`, `_00002.tif`; the
  `conditions_and_frame` pattern) has the label as its key, so the per-frame
  events planet-watchdog sends for one cadence group (`frames_per_shot`) become
  one acquisition and one `[frames, y, x]` stack, as shot-aligner writes it;
- set-and-ordinal frames (`set10_00001.tif`) keep their stem as key, so each is
  its own acquisition, again as in shot-aligner;
- a `.rec` comment sidecar falls to its frame's key, whether it arrives as a
  member or as an event of its own.

Two acquisitions of one instrument in one shot become two detectors, the second
named `<detector>_<seq or 0>` (shot-aligner's rule). An event whose
`instrument.format` names no pack is recorded as a problem and skipped; events
without `metadata.instrument` (triggers, LabFrog rows) are not acquisitions.

## 3. `seq`, `label`, `when`: from the file name, no event-schema change

`seq` and `label` come from the vendored pack manifests' `namePatterns` (the
same regexes `claim()` uses; suffix stripping as each pack's `_stem`). `when`
is `metadata.acquisition.time` of the acquisition's earliest event, and
`time_source` comes beside it. `hzdr-event-v1` is unchanged.

One simplification: shot-aligner's recording claim also requires a quantity
token and refuses a set label, because there it decides *whether a file is an
acquisition at all*. A producer has already decided that here, so a name no
pattern fits is still converted, with its stem as key and no `seq`.

## 4. Container layout

`<campaign folder>/shots/<YYYYMMDD>_<shot_number:06d>.nxs` (date and number
parsed from `shot_key`; `unknown` when the shot has no date). Digits,
underscore and `.nxs` only, so the name is legal on Windows and the `Z:` share,
and it does not change when a ruling moves the shot to another campaign. The
`shot_key` is stored as an attribute on the root and on `/entry`.

One `NXentry`. Group names come from the vendored, unchanged
`nxwrite.container_groups`, fed from the vendored NDS catalogue
(`hzdr_packs/vendor/hzdr-draco-0.1.0.json`, pinned by the packs sync):
`family` is shot-aligner's `group`, `instrument_name` its `instrumentName`,
`detector_name` its `detectorName`. So M1_Spec_Fib_Cer lands at
`/entry/Reflected_light_spectroscopy/_515_Reflected_Light_Spectrometer`,
exactly where the manifest has it. Each detector also gets shot-aligner's
`local_name` (the catalogue's `display_name`), `timing_role` and the
`file_metadata` `NXnote`. `file_path` there is the path the event recorded,
not this host's mount of it. `recorded_offset_removed` is 0.0 with a
description saying DAMNIT fits no offset (the contract lists the field).

## 5. Mapping rows: **out of phase 3**

shot-aligner's `mappings.apply_to` (reviewed NDS rows linking pack output to
agreed paths) is about 1,600 lines with its catalogue and profile checks, and
20 of the 40 mappings still carry ids awaiting the maintainer. It is a tracked
follow-up. These manifest contract nodes are therefore not produced:

- `/entry/collection_M1_Spec_Fib_Cer` and its five links (`black_level`,
  `chip_size_x`, `chip_size_y`, `gamma`, `image_file`);
- in `/entry/Reflected_light_spectroscopy/_515_Reflected_Light_Spectrometer`:
  `description` and `frame_start_number`;
- the aliases those two create: `fabrication/model` is then not a link to
  `description`, and `raw_data/sequence_number` not a link to
  `frame_start_number` (both nodes are still written, by the pack).

The exit test lists exactly these and fails if any other contract node is
missing or different.

## 6. Worker

`api/scripts/hzdr-container-worker.py` is a separate process; the conversion
lives in `damnit_api.metadata.hzdr_containers`.

- **Started by the builder trigger** when
  `DW_API_HZDR_BUILDER__CONTAINERS_ENABLED=true` (default off): once before the
  master build, so already published shots convert while it runs, and once after
  a successful build, so new shots convert without waiting for another event.
  The trigger does not wait for it.
- **Its own lock per campaign**: `shots/.convert.lock`, the same PID-stamped
  `single_writer_lock`, never the master's `<campaign>.nxs.lock`. A worker that
  finds the lock held writes `shots/.convert.pending` and exits; the holder
  re-reads the master and makes another pass while that marker exists or the
  master changed since its pass began, and checks the marker once more after
  releasing the lock, so no request is lost.
- **Atomic and resumable**: each container is written to `<name>.nxs.tmp` and
  renamed into place. Its input fingerprint is stored as a root attribute and
  in `shots/.build-manifest.json` (written atomically, flushed every few
  seconds and at the end). A container is skipped when the manifest, or failing
  that its own attribute, holds the same fingerprint. A crash mid-date leaves
  finished containers in place; the next run adopts them and converts the rest.
  Stale `.nxs.tmp` files are removed at the start of a pass, under the lock.
- **Fingerprint**: the members' recorded paths and `sha256` (stat only when a
  producer sent none), the acquisition facts above, the shot fields written,
  the catalogue's SHA-256, and a digest of the conversion code (packs, vendored
  files and this module). A code change therefore rebuilds everything, as the
  plan requires.
- **Memory** is bounded by one frame: the packs stream (phase 2b), and the
  writer holds only the shot's event metadata.
- **Missing or unreadable files**: a detector whose pack wrote nothing
  plottable is removed (and its `NXinstrument` if left empty), and the reason
  goes into `/entry/conversion_problems` (`NXnote`). Partial problems (a
  missing CSV beside a readable frame) keep the detector and are recorded the
  same way. A container whose files were *missing* records them in the
  manifest and is retried once one of them appears; an unreadable file is not
  retried until its bytes (its `sha256`) change.
- **Path map**: raws are read through `DW_API_METADATA__PATH_MAP`
  (`shared/hzdr_paths.py`), or `--path-map`.
- **Not here**: the master's links (phase 4), garbage collection of containers
  the master no longer names (phase 4, after publish), validation (phase 5).

## 7. Shot-level metadata (minimal)

`/entry`: `title` (`<campaign> <date> shot <number>`), `start_time` (the
shot's `fired_at` from `/entry/shots`, the trigger or earliest event time),
`experiment_identifier`, `entry_identifier` (the container stem), attributes
`shot_key`, `shot_number`. Where `/entry/shots` has a LabFrog row,
`/entry/labfrog_shot` (`NXcollection`) carries `record_id`, `date_time` and
`local_count`. Root: `NX_class=NXroot`, `default=entry`, the container profile
`hzdr-shot-container-v1`, the fingerprint.

Deferred: the LabFrog shot parameters with registry units (shot-aligner's
`shot_parameters`), the target sample and laser groups (they stay in the
master), backgrounds (`<campaign>_backgrounds.nxs`), and an entry-level plot
(decision 6 keeps only each detector's default).
