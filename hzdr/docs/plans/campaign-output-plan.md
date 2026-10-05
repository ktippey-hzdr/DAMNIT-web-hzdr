# Campaign output: master plus linked shot containers

Plan, 2026-10-04. The cross-repository plan, with its phases, exit checks and
open decisions, is `planning/NEXUS_OUTPUT_PLAN.md` in HZDR_combo. This note
lists only what it means for this repository. Where the two disagree, the combo
plan wins.

## Target

The builder's end product becomes a NeXus-valid folder per campaign:

```text
<campaign>/<campaign>.nxs                      master: today's canonical file, same name,
                                               + root links to shots/, indexed in /entry/shot_containers
<campaign>/shots/<YYYYMMDD>_<shot_number>.nxs  one NXentry per shot, the converted data
<campaign>/<campaign>_backgrounds.nxs          backgrounds the detectors link to
```

shot-aligner produces this kind of output today, offline. It is the interim
answer and the reference the builder is tested against.

Containers are not named by `shot_key`. It contains colons, which Windows and
the `Z:` share refuse, and its campaign part changes when a shot is moved.

## What changes here

Phase 3, the container writer, is designed in
[container-writer.md](container-writer.md): the converter joins
`/entry/source_events` by `event_id` (the bridge profile is unchanged), and a
separate worker writes the containers under its own lock.

- **Conversion code arrives in `metadata/`:** readers, diagnostic packs,
  instrument mappings and the NeXus writing helpers come from shot-aligner,
  under `hzdr_` names and with a sync check (combo decision 1).
- **`data_products` must carry what a pack needs:** `instrument.id`,
  `instrument.format`, `timing_role` and `payload_ref.members` (the sidecars
  and their `sha256`). Today `build_event_data_products` keeps only `path`, or
  the converter joins on `source_events.event_id`.
- **Conversion runs outside the campaign lock**, in a worker. Containers are
  idempotent and renamed into place. The lock is taken only to publish the
  master and catalog, as today. A missing or unreadable file drops that
  detector and records why; it never fails the build.
- **The API follows links:** `hzdr_sources` uses `visititems`, which does not
  traverse external links.
- **SciCat registers the folder:** containers listed, plus a manifest hash.
- **Garbage collection after publish:** a container the new master no longer
  links is removed.
- **The canonical file becomes the master:** its tables are unchanged, and it
  gains links to the containers. A bridge-profile bump is due if the master
  layout changes beyond added links.
- **Incremental builds:** a per-shot input fingerprint and a build manifest, so
  an unchanged shot is not rewritten.
- **Validation:** `pynxtools`/NDS validation of master and containers in CI
  and after each build, plus a read-back that counts dangling links.

## Invariants (from CLAUDE.md, extended to a folder)

- The PID lock covers the whole campaign folder.
- Containers are written to temp files and renamed into place first. The
  master is renamed last and is the only entry point, so it never links to a
  container that is not there yet.
- The lock is held only for publishing, never for conversion, which takes
  minutes for a date.

## Related branches

- `feat/hzdr-path-map`: needed regardless, because the builder reads raw files
  from the bigdata mount. It can merge on its own.
- `feat/hzdr-external-links`: **held, not for merging as is.** It links
  producer-written HDF5 files. Its target checks and its write-before-publish
  ordering are reused in the master-and-links phase.
