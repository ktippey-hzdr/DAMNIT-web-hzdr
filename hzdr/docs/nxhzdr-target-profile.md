# `NXhzdr_target` Profile — v0.5

Updated: 2026-07-17

The versioned definition of the HZDR-local `NXhzdr_target` profile: the
semantic map from `metadata.target.*` to the `/entry/sample` NeXus group, and
the compatibility-attribute contract the writer stamps. Since v0.2 the profile
is also encoded as a real NXDL application definition
(`hzdr/nxdl/NXhzdr_target.nxdl.xml`), declared by the bridge file via
`/entry/definition`, so generated files can be certified with standard NXDL
tooling (`nds validate <file> --pynxtools --definitions hzdr/nxdl`). This
document stays the normative prose reference — see §6 for what's still open.

Related docs: [target-ontology.md §2/§5/§8](target-ontology.md#2-the-schema)
(binding key registry and NeXus mapping this profile is derived from),
[standards-alignment.md §3.4](standards-alignment.md#34-target--sample)
(HELPMI cross-walk) and
[Route 2](standards-alignment.md#route-2-nxsourcenxbeam-and-nxsample-groups-in-the-nexus-bridge-ready)
/ [Route 4](standards-alignment.md#route-4-nexus-ontology-annotation-for-federated-search-higher-effort),
[alignment-implementation-plan.md Phase 5](plans/alignment-implementation-plan.md#phase-5--hzdr-owned-ontology-annotation--openpmd-interoperability-).

## 1. Purpose and naming rationale

HELPMI (the HZDR-led Helmholtz Laser-Plasma Metadata Initiative) finished on
2026-07-02 without publishing an official `NXtarget` NeXus base class. Rather
than wait indefinitely or squat on the unqualified `NXtarget` name — which
looks official and could collide with a future upstream class — HZDR defines
its own local profile: **`NXhzdr_target`**.

**Compatibility-first strategy:** generated files keep `NX_class="NXsample"`
on `/entry/sample` so any standard NeXus/HELPMI tool can still read them. The
HZDR profile is layered on top as attributes, not as a replacement class:
`damnit_nx_class="NXhzdr_target"` and `damnit_nxdl_version` (this document's
version). Since v0.2 a real `NXhzdr_target` NXDL ships alongside this document
and the file declares it via `/entry/definition`; whether profile files should
additionally set `NX_class="NXhzdr_target"` remains an open decision (§6).

## 1.1 Literature and standards basis

The profile is intentionally conservative: it uses official NeXus terms where
they exist, marks HZDR-only semantics explicitly, and keeps the generated file
readable by generic NeXus/HDF5 tooling.

| Design decision | Source basis | Consequence for `NXhzdr_target` |
| --- | --- | --- |
| Use `NXsample` as the compatibility class | The NeXus `NXsample` base class is the standard place for sample information and already defines `name`, `chemical_formula`, `temperature`, `description`, `type`, `thickness`, `physical_form`, sample environment, and incident beam links ([NeXus `NXsample`](https://manual.nexusformat.org/classes/base_classes/NXsample.html)). | `/entry/sample` keeps `NX_class="NXsample"` and maps the overlapping target fields onto standard `NXsample` datasets first. |
| Keep HZDR-specific fields visibly local | The NXDL manual says NeXus class definitions are the standard glossary; extra items may be inserted, but they are not part of the standard unless defined by an NXDL/application definition ([NeXus NXDL](https://manual.nexusformat.org/nxdl.html)). | HZDR extensions use `damnit_*`, `target_ref`, `gas_species`, and `prop_*` attributes under a named local profile instead of pretending they are upstream NeXus fields. |
| Write a prose profile before claiming a formal class | NXDL files are machine-readable definitions that can be validated against XML Schema and used to validate data files ([NeXus NXDL](https://manual.nexusformat.org/nxdl.html)). | v0.1 was a profile document only. v0.2 ships the NXDL (`hzdr/nxdl/NXhzdr_target.nxdl.xml`) with pynxtools-based validation; the `NX_class="NXhzdr_target"` swap is still a separate decision (§6). |
| Avoid the bare name `NXtarget` | Official NeXus class lists define the standard namespace ([base classes](https://manual.nexusformat.org/classes/base_classes/index.html), [application definitions](https://manual.nexusformat.org/classes/applications/index.html)). As of the v2026.01 manual used for this review, `NXsample` exists and no official `NXtarget` base class is documented there. | The local class/profile name is prefixed as `NXhzdr_target` to avoid implying official NIAC status or colliding with a future upstream class. |
| Prefer standard units metadata over unit suffixes in keys | NXDL standardizes common terms including engineering units, and fields carry unit categories such as `NX_LENGTH`, `NX_TEMPERATURE`, and `NX_PRESSURE` ([NeXus NXDL](https://manual.nexusformat.org/nxdl.html), [`NXsample`](https://manual.nexusformat.org/classes/base_classes/NXsample.html)). | Stored metadata keys stay bare (`thickness`, `temperature`, `gas_pressure`); the NeXus writer stamps `@units` at write time. |
| Keep laser and target as adjacent but separate concepts | NeXus already models the radiation source and beam via `NXsource` and `NXbeam`, including laser/source type, frequency, wavelength, pulse energy, beam extent, incident wavelength, and polarization ([`NXsource`](https://manual.nexusformat.org/classes/base_classes/NXsource.html), [`NXbeam`](https://manual.nexusformat.org/classes/base_classes/NXbeam.html)). | `NXhzdr_target` only covers target/sample semantics; laser semantics stay in `/entry/instrument/laser` as `NXsource` + `NXbeam`. |
| Keep bridge collections out of the target profile | `NXcollection` is explicitly unvalidated and intended for arbitrary grouped terms ([`NXcollection`](https://manual.nexusformat.org/classes/base_classes/NXcollection.html)). | The canonical shot/event tables can remain `NXcollection`, but target fields that need durable semantics are promoted into `/entry/sample`. |
| Plan ontology annotation as a later pass | The NeXus Ontology creates machine-readable identifiers for NeXus classes and fields and is designed for annotation/tagging and mappings to other vocabularies ([nexusformat/NeXusOntology](https://github.com/nexusformat/NeXusOntology)). | v0.1 records which fields are standard NeXus fields; URI annotation is deferred until the NXDL/ontology pass. |

Broader RDM alignment supports the same shape. DAPHNE4NFDI emphasizes linked
metadata capture, data catalogues, and reuse workflows for photon/neutron
large-facility data ([DAPHNE4NFDI](https://www.daphne4nfdi.de/)); SciCat positions
itself as a scientific metadata catalogue for findability and sharing
([SciCat](https://www.scicatproject.org/)); and the FAIR principles emphasize
machine-actionable metadata for findability, interoperability, and reuse
([Wilkinson et al. 2016](https://www.nature.com/articles/sdata201618)). The
Plasma-MDS schema is not a laser-plasma target standard, but it supports the same
decomposition into plasma source, medium/target, diagnostics, and resources
([Franke et al. 2020](https://www.nature.com/articles/s41597-020-00771-0)).

## 2. Semantic map: `metadata.target.*` → `/entry/sample`

| `metadata.target` key | NeXus path (under `/entry/sample`) | Canonical unit | HELPMI DDC term (§3.4) | Upstream NeXus field? |
| --- | --- | --- | --- | --- |
| `name` | `name` (dataset) | — (string) | — | Standard `NXsample.name` |
| `type` | `type` (dataset) | — (string enum, target-ontology.md §3) | Target type | Standard `NXsample.type` **field name**; HZDR enum values (`foil`/`gas_jet`/`cluster`/`liquid`/`structured`/`other`), not the upstream sample/can/buffer vocabulary |
| `material` | `material` (dataset) | — (string) | Material | **Profile extension** — free text; no standard `NXsample` field |
| `material` (only when it parses as a formula) | `chemical_formula` (dataset) | — (string) | Material | Standard `NXsample.chemical_formula` |
| `thickness` | `thickness` (dataset), `@units` | nm | Thickness | Standard `NXsample.thickness` |
| `temperature` | `temperature` (dataset), `@units` | °C (`degC`) | Sample temperature | Standard `NXsample.temperature` |
| `diameter` | `diameter` (dataset), `@units` | mm | Diameter | **Profile extension** — no standard `NXsample` field |
| `gas_pressure` | `gas_pressure` (dataset), `@units` | bar | Gas pressure (gas jet) | **Profile extension** — no standard `NXsample` field |
| `substrate_material` | `substrate_material` (dataset) | — (string) | Substrate material | **Profile extension** — no standard `NXsample` field |
| `notes` | `description` (dataset) | — (string) | — | Standard `NXsample.description` |
| `provenance` | `@damnit_provenance` (group attr) | — (string enum: `wiki`/`manual`) | — | **Profile extension** (HZDR attr, not NeXus-standard) |
| `wiki_ref` | `@target_ref` (group attr) | — (string, URL/id) | — | **Profile extension** (HZDR attr) |
| `gas_species` | `@gas_species` (group attr) | — (string) | Gas species (gas jet) | **Profile extension** (HZDR attr) |
| `properties.*` | `@prop_<key>` (group attr, one per key) | as given (open bag, no registry unit) | — | **Profile extension** (HZDR attr) |

`name`, `thickness`, `temperature` are **standard NXsample**
fields — a plain NeXus reader gets useful data with no knowledge of this
profile. `material`, `diameter`, `gas_pressure`, `substrate_material` are
**profile extensions**: still written as plain datasets (so generic NeXus
tooling can still read the value), but there is no upstream `NXsample` field
they map to — they only have meaning under the `NXhzdr_target` semantic layer.

Since v0.4, `material` routes to the free-text `material` extension dataset
instead of `chemical_formula`. Real target-inventory values are mostly **not**
CIF-convention formulas — the curated wiki records carry polymer trade names
("Formvar", "CH formvar"), multi-layer lists ("Si, Cu", "Au + CH (PU)") and
gas mixtures for LWFA gas targets — and `NXsample.chemical_formula` is
specified as a CIF/Hill-notation formula, so writing those values there would
mislabel them for any consumer that trusts the field name. The writer still
derives `chemical_formula` **additionally** when the material value parses as
a plain element-symbol formula (`_is_chemical_formula()` in
`hzdr_nexus.py` — e.g. `Au`, `Cu`, `Si3N4`, `CH`), which covers the pure-foil
inventory; anything that does not parse is written only as `material`.

Since v0.5, `type` — the ontology's required classification
(target-ontology.md §3) — is written as the `type` dataset. The **field name**
is standard `NXsample.type`, but the **value vocabulary** is the HZDR
laser-target enumeration rather than upstream NXsample's
sample/can/buffer list; the NXDL fixes the six accepted values, so a producer
sending an out-of-vocabulary type fails certification (the reconciler's wiki
mapping always lands on the enum, keeping the original wiki text in
`prop_wiki_type`).

Fields absent or `null` in `metadata.target` are skipped entirely — never
written as empty/null datasets or attributes (see `write_nexus_sample()` in
`api/src/damnit_api/metadata/hzdr_nexus.py`).

## 3. Profile attributes spec

Stamped on the `/entry/sample` group by `write_nexus_sample()`:

| Attribute | Value | Required | Notes |
| --- | --- | --- | --- |
| `NX_class` | `"NXsample"` | always | Compatibility class; never changes to `NXhzdr_target` until §6 is resolved |
| `damnit_nx_class` | `"NXhzdr_target"` | always | Marks the group as following this profile |
| `damnit_nxdl_version` | `HZDR_TARGET_PROFILE_VERSION` (currently `"0.5"`) | always | Must match this document's version and the NXDL enumeration (§4) |
| `damnit_provenance` | `"wiki"` \| `"manual"` | if `provenance` present | Curated vs. hand-entered target |
| `target_ref` | string (URL or stable id) | if `wiki_ref` present | Link back to the MediaWiki target record |
| `gas_species` | string (e.g. `"Ar"`, `"N2"`, `"He"`) | if `gas_species` present | Gas-jet / cluster species |
| `prop_*` | one attr per `properties` key, value as given | if `properties` present | Open extras bag (target-ontology.md §4); not unit-registry-backed |

`HZDR_TARGET_PROFILE_VERSION` is the module-level constant in
`api/src/damnit_api/metadata/hzdr_nexus.py` (next to `write_nexus_sample()`);
its value must always equal this document's version number.

Since v0.2, `write_nexus_bridge()` additionally writes the entry-level dataset
`/entry/definition = "NXhzdr_target"` (always). It declares the application
definition encoded in `hzdr/nxdl/NXhzdr_target.nxdl.xml`, which is what NXDL
validators (pynxtools) certify the file against; the NXDL fixes the accepted
`damnit_nxdl_version` value via an enumeration, so a version drift between
writer and NXDL fails certification.

## 4. Versioning rule

Bump `damnit_nxdl_version` / `HZDR_TARGET_PROFILE_VERSION` on **any** change to
the semantic map in §2 or the attribute spec in §3 — a field added/removed/
retyped, a unit changed, or an attribute renamed. The writer constant, this
document's version, and the `damnit_nxdl_version` enumeration in
`hzdr/nxdl/NXhzdr_target.nxdl.xml` must always agree (all three, bumped
together); a mismatch means one of them was updated without the others. The
meta-repo alignment checker's `ontology` group verifies all three. Non-semantic
edits to this document (wording, typo fixes) do not require a bump.

Current version: **0.5** (`target.type` written as the `type` dataset with the
ontology's six-value enumeration fixed in the NXDL, 2026-07-17).

History:

- **0.5** (2026-07-17): `target.type` (target-ontology.md §3, the ontology's
  only *required* classification key) is now written to `/entry/sample/type`.
  Standard `NXsample.type` field name with HZDR enum values
  (`foil`/`gas_jet`/`cluster`/`liquid`/`structured`/`other`); the NXDL
  enumerates them, so certification catches out-of-vocabulary producers.
- **0.4** (2026-07-15): `target.material` now routes to the free-text
  profile-extension dataset `material` instead of `chemical_formula`, because
  real inventory values are mostly trade names ("Formvar"), layer lists
  ("Si, Cu") or gas mixtures rather than CIF formulas. `chemical_formula`
  (standard NXsample) is written **in addition** only when the value parses
  as a plain element-symbol formula (writer check `_is_chemical_formula()`).
- **0.3** (2026-07-13): canonical unit string for `target.temperature`
  changed `"C"` → `"degC"` (UDUNITS/pint-parseable; `"C"` reads as coulomb),
  letting the NXDL keep the standard `NX_TEMPERATURE` units category.
- **0.2** (2026-07-13): `/entry/definition = "NXhzdr_target"` stamped by
  `write_nexus_bridge()`; profile encoded as the NXDL application definition
  `hzdr/nxdl/NXhzdr_target.nxdl.xml`. §2/§3 sample-group semantics unchanged.
- **0.1** (2026-07-02): first drafted version.

## 5. Known deviations

- **`/entry/instrument/laser/beam` nests `NXbeam` inside `NXsource`.**
  `write_nexus_laser_group()` writes `/entry/instrument/laser` as `NXsource`
  and then a `beam` sub-group as `NXbeam` *underneath* it. Upstream NeXus
  convention more commonly places `NXbeam` as a sibling under `NXinstrument`,
  or attaches it under `NXsample` (the beam incident on the sample). HZDR's
  nesting groups the beam with its originating source instead, which reads
  naturally for a single-laser beamline but is not the most common upstream
  pattern. **Accepted as a deviation for now** — the v0.2 NXDL deliberately
  covers only the entry's `definition` and the sample group, so it neither
  enforces nor forbids this placement; revisit if the NXDL's scope ever grows
  to instrument groups.

No other deviations are tracked in v0.3.

## 6. Future work

- ✅ **NXDL formalization — done in v0.2 (2026-07-13).**
  `hzdr/nxdl/NXhzdr_target.nxdl.xml` encodes §2/§3 as an application
  definition (NXentry + optional `sample: NXsample`; all sample fields
  optional because the writer skips absent keys; `damnit_nx_class` /
  `damnit_nxdl_version` required with enumerated values). Validation is
  bundled via nexus-design-studio:
  `nds validate <file.nxs> --pynxtools --definitions hzdr/nxdl` overlays this
  directory onto pynxtools' NeXus definitions and certifies each entry against
  the `/entry/definition` it declares. The open `prop_*` attribute bag is
  modelled as the partial-name attribute `prop_KEY` (type
  `NX_CHAR_OR_NUMBER`). Since v0.3 the registry's canonical temperature unit
  string is `"degC"` (not `"C"`, which unit-aware validators parse as
  coulomb), so `temperature` carries the standard `NX_TEMPERATURE` units
  category.
- **`NX_class="NXhzdr_target"` decision.** The NXDL now ships with validation
  tooling; deciding whether HZDR-profile files should set
  `NX_class="NXhzdr_target"` directly on `/entry/sample` (dropping, or
  keeping alongside, the `NX_class="NXsample"` compatibility value) remains
  open. Tracked
  as [alignment-implementation-plan.md Phase 5](plans/alignment-implementation-plan.md#phase-5--hzdr-owned-ontology-annotation--openpmd-interoperability-).
- ✅ **`type` field — done in v0.5 (2026-07-17).** `metadata.target.type`
  (target-ontology.md §3) is written as the `/entry/sample/type` dataset;
  the NXDL enumerates the six ontology values.
- **NeXus Ontology URIs.** Route 4 (standards-alignment.md) calls for
  annotating covered fields with `nexusformat/NeXusOntology` URIs where they
  exist; §2's "Upstream NeXus field?" column is the starting point for that
  pass.
