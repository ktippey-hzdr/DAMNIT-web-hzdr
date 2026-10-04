# External links from the campaign NeXus file to bulk HDF5 files

Status: implemented on `feat/hzdr-external-links` (2026-10-04).

## Problem

A producer event names the bulk file it describes in `payload_ref`
(`hdf5_path`, `filepath`, `path`, ... plus an optional `dataset_path`).
`build_event_data_products()` turns that into a row of `/entry/data_products`,
which is a table of strings. A reader holding the campaign file therefore has
to read a path string, translate it to its own mount of the bigdata share and
open the second file itself. shot-aligner's master files do better: they carry
real HDF5 external links (`h5py.ExternalLink(member, "/entry")`), so
`silx view` or `h5py` follows them from one file. This plan gives the campaign
file the same thing for the bulk files events point at.

## Decisions

### (a) Which rows get a link

Only a row whose target is an HDF5 file in its own right:

- it is an event reference row (its `path` is some file other than the
  campaign file itself; inline `values` datasets and LabFrog-derived rows
  already live in the campaign file and get nothing), **and**
- the recorded path ends in `.h5`, `.hdf5`, `.nxs` or `.nx5` (case-insensitive),
  or the row names a `dataset_path`, **and**
- the path is a file path, not a URL (`scheme://...`; a Windows drive letter
  is a path).

An HDF5 external link can only target an HDF5 file, so every other row (TIFF,
CSV, `s3://`, SciCat PIDs, ...) stays exactly what it is today: a string
reference row. Building HDF5 containers around non-HDF5 bulk data is out of
scope.

### (b) Where the links live

`/entry/data_product_links` (`NXcollection`, `damnit_source="data_products"`),
a sibling of `/entry/data_products`. Each member is named by the row's
`product_index` (`"0"`, `"12"`, ...) and is an external link to the target
file's `dataset_path` (the file root `/` when the row names no dataset).

- `/entry/data_products` stays a flat table of equal-length columns. The
  openPMD preflight and any table reader decide axis membership by group and
  count rows by `product_index`; a subgroup inside the table would be the
  first non-column member and every such reader would have to learn to skip it.
- Naming by `product_index` keeps the link a view of one *row*, so a reader
  reaches the shot by joining that row's `shot_key` — never by position. A
  per-shot grouping (`/entry/<shot_key>/...`) was rejected: it would make a
  second shot axis outside `/entry/shots`, which CLAUDE.md reserves as the only
  shot-indexed group.
- The group is rewritten as a whole on every build (like the table), so a
  `product_index` never names a stale link.

Each candidate row also records what happened in its existing `metadata_json`
column, under `link`:

```json
{"status": "linked", "name": "/entry/data_product_links/12",
 "target_file": "../../hzdr/experiments/x.nxs", "target_path": "/entry/data"}
```

`status` is one of `linked`, `missing_target`, `missing_dataset`,
`unreadable`, `not_hdf5`, `not_local_path`. Rows that are not reference rows
get no `link` key.

### (c) Link target: relative, computed on the builder's mount

The campaign file is published to the bigdata share and read on other machines
that mount the same share somewhere else (`/bigdata`, `Z:/bigdata`,
`/home/tippey/mnt/bigdata` on the server). An absolute link written on the
server would name the server's mount and dangle everywhere else.

So the builder maps the recorded target onto its own mount with
`DW_API_METADATA__PATH_MAP` (`map_path`), and writes the link filename
**relative to the campaign file's directory**, as shot-aligner does. HDF5
resolves a relative external link against the directory of the file holding it,
so the link works on every host that mounts the share, whatever the mount point.
The temp file is written beside the output, so the link resolves identically
before and after the atomic rename.

When no relative path exists (the two files share no directory but the
filesystem root, or sit on different Windows drives) the link uses the
absolute mapped path instead; that only happens when a bulk file lives outside
the share, where no portable form exists. One rule, no new setting.

### (d) Missing targets: skip and record, never fail

The builder checks the mapped target before linking: it must exist, open as
HDF5 and contain the `dataset_path`. If not, no link is written and the row's
`link.status` says why (`missing_target`, `unreadable`, `missing_dataset`).
A published file then never contains a dangling link (some tools print errors
when they meet one), and the reason is visible in the catalog. Bulk files that
arrive after their event are linked by the next build, which the next event
triggers anyway. Any unexpected error while probing or linking a row is logged
and recorded as `unreadable`; the build never fails because of a bulk file.

Each target file is opened at most once per build, read-only.

### (e) Bridge profile stays `hzdr-canonical-shot-v5`

CLAUDE.md asks for a bump when the bridge *table layout* changes (columns added
to or removed from the shot or source-events groups). This change adds no
column to any table: `/entry/data_products` keeps its columns, the status rides
in the existing `metadata_json`, and the links are an additive view in a new
group — the same shape as the `/entry/instrument/<instrument.id>` index groups,
which were added without a bump. A reader that does not know the group is not
affected. If a later change promotes the link status to its own column, that
is the moment for v6.

## Configuration

No new setting. The builder reads the existing `DW_API_METADATA__PATH_MAP`
(the same one the API uses to open `hdf5_path`), and its CLI takes
`--path-map "from=to,..."` to override it for a standalone run. The
auto-trigger subprocess inherits the API's settings, as it does for
`DW_API_HZDR_LASER__*`. On fwkt-webapps:

```
DW_API_METADATA__PATH_MAP=/bigdata=/home/tippey/mnt/bigdata,Z:/bigdata=/home/tippey/mnt/bigdata
```

## Invariants kept

- The link group is written inside the temp file, before `temp_path.replace()`;
  the publish stays atomic and under the single-writer PID lock.
- Nothing stored is rewritten: the row's `path` column still holds the path the
  producer recorded.
- `visititems` (dataset listing in `hzdr_sources.py`) does not follow external
  links, so listing a shot never opens bulk files.

## Out of scope

- Wrapping non-HDF5 bulk files in HDF5 containers.
- Links from the shot table; joins go through `/entry/data_products`.
- Re-checking links after publication (a moved bulk file dangles until the next
  build; shot-aligner's `examine.py` is the model for a later checker).
