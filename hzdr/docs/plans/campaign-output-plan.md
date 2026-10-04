# Campaign output: master plus linked shot containers

Plan, 2026-10-04. The cross-repository plan, with its phases, exit checks and
open decisions, is `planning/NEXUS_OUTPUT_PLAN.md` in HZDR_combo. This note
lists only what it means for this repository. Where the two disagree, the combo
plan wins.

## Target

The builder's end product becomes a NeXus-valid folder per campaign:

```text
<campaign>/<campaign>_master.nxs   today's canonical file + one ExternalLink per shot
<campaign>/shots/<shot_key>.nxs    one NXentry per shot, the converted data
```

shot-aligner produces this kind of output today, offline. It is the interim
answer and the reference the builder is tested against.

## What changes here

- **Conversion code arrives in `metadata/`:** readers, diagnostic packs,
  instrument mappings and the NeXus writing helpers come from shot-aligner,
  under `hzdr_` names and with a sync check (combo decision 1).
- **New builder step:** each shot's `data_products` rows that carry an
  `instrument.id` are converted into `NXdetector` groups of that shot's
  container. A missing or unreadable file drops that detector and records why;
  it never fails the build.
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

## Related branches

- `feat/hzdr-path-map`: needed regardless, because the builder reads raw files
  from the bigdata mount. It can merge on its own.
- `feat/hzdr-external-links`: **held, not for merging as is.** It links
  producer-written HDF5 files. Its target checks and its write-before-publish
  ordering are reused in the master-and-links phase.
