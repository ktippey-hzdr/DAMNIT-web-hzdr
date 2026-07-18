# openPMD Simulation Linking (`metadata.simulation`)

Updated: 2026-07-18 · Status: **specified** (schema + normalizer tolerance;
no comparison tooling — see §6)

The binding definition of how an experimental shot references PIC/openPMD
simulation output, implementing
[standards-alignment.md Route 5](standards-alignment.md#route-5-openpmd-interoperability-for-simulation-comparisons)
and [alignment-implementation-plan.md Phase 5 item 3](plans/alignment-implementation-plan.md#phase-5--hzdr-owned-ontology-annotation--openpmd-interoperability-):
**linking/manifest interoperability, not conversion**. The campaign NeXus
file is never converted to openPMD and simulation data is never copied into
DAMNIT; a shot (or data product) carries an explicit *reference* so
comparison tools can join experimental NeXus and simulation output.

Like the target ontology, the link lives inside the free-form `metadata`
object of the [`hzdr-event-v1`](event-schema.md) envelope — **no
transport-schema change and no `hzdr-event-v2` bump** is required.

## 1. Placement

`metadata.simulation` — a reserved namespace alongside `target`, `laser`,
`vacuum`, `run`, and `diagnostic`. All fields are strings/ints/objects; the
namespace is non-numeric, so it has **no rows in the metadata key registry**
(nothing to unit-stamp) and the registry linter ignores it.

The value is **one link object, or an array of link objects** when a shot is
compared against several runs. Readers must tolerate both shapes and
normalize object → `[object]` (mirroring the string→object widening rule of
[target-ontology.md §7](target-ontology.md#7-migration-from-the-legacy-flat-target-string)).

## 2. The link object

| Key | Type | Required | Meaning |
| --- | --- | --- | --- |
| `series_uri` | str | one of these two | URI of the openPMD series (e.g. `file:///bigdata/picongpu/run042/simData_%T.bp`, an object-store URL, or a SciCat PID URL). Standard openPMD `%T` iteration placeholders are passed through verbatim. |
| `path` | str | one of these two | Filesystem path form of the same reference, for local-first workflows without a URI scheme. |
| `iteration` | int \| [int, int] \| null | no | The simulation iteration matched to this shot, or an inclusive `[start, end]` window. `null`/absent means "whole series". |
| `code` | str | recommended | Simulation code name (`"PIConGPU"`, `"Smilei"`, `"WarpX"`, …). |
| `code_version` | str \| null | no | Code version/tag the series was produced with. |
| `checksum` | str \| null | no | Content checksum of the series manifest, prefixed with the algorithm (`"sha256:…"`), for detecting a re-run under the same path. |
| `scicat_pid` | str \| null | no | SciCat PID of the registered simulation dataset, when it has one — the same citable-reference convention as `payload_ref.scicat_pid`. |
| `notes` | str \| null | no | Free operator text ("run with 2× density scan"). |

Rules:

- **At least one of `series_uri` / `path` must be set** for a real link — a
  link with neither is meaningless and should be dropped by producers.
- The object is **open** (extra keys are tolerated and preserved), matching
  how everything inside `metadata` is handled; recurring extra keys should be
  promoted into this table in a later revision of this doc.
- **The link is data-flow-neutral.** Bulk simulation data stays behind the
  reference; embedding simulation arrays in `values` is the same
  producer-side bug as embedding detector images (`hzdr-event-v1` size
  bounds apply unchanged).

## 3. Examples

Single matched iteration:

```jsonc
"metadata": {
  "simulation": {
    "series_uri": "file:///bigdata/picongpu/lwfa-042/simData_%T.bp",
    "iteration": 1200,
    "code": "PIConGPU",
    "code_version": "0.8.0",
    "checksum": "sha256:9f2c…",
    "notes": "best-match density profile"
  }
}
```

Several candidate runs (array form):

```jsonc
"metadata": {
  "simulation": [
    { "path": "/bigdata/picongpu/lwfa-042", "iteration": [1100, 1300], "code": "PIConGPU" },
    { "series_uri": "https://scicat.hzdr.de/…/PID", "scicat_pid": "20.500.12269/…", "code": "Smilei" }
  ]
}
```

## 4. Consumer behavior (current scope)

- `hzdr_nexus._normalize_event()` passes `metadata.simulation` through
  **unchanged** — it is preserved into the merged per-shot metadata and the
  catalog like any other metadata namespace, and the registry linter emits no
  warnings for it. A characterization test
  (`api/tests/test_hzdr_simulation_link.py`) pins this round-trip.
- The shot's merged metadata (and therefore `hzdr_sources.json` and the
  GraphQL/REST surfaces) exposes the link as ordinary metadata; no dedicated
  API endpoint is defined in this revision.

## 5. Future NeXus mapping (not yet written)

When a consumer needs the link inside the canonical NeXus file itself, the
planned shape is one `NXnote` group per link under `/entry/simulation_links`,
carrying the link-object keys as datasets — chosen over `NXcollection` so the
reference is validatable. This is **not** part of profile v0.6; adding it
bumps the `NXhzdr_target` profile version per the
[versioning rule](nxhzdr-target-profile.md#4-versioning-rule).

## 6. Deliberately out of scope

Per the Phase 5 effort ruling (🔴 for tooling): openPMD series reading,
comparison plots, manifest generation, and any conversion of the campaign
NeXus file. These start only when there is a concrete analysis user; this
document exists so the *references* those tools will need are captured
losslessly starting now.
