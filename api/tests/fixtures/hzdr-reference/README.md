# Reference fixture: one shot, three packs

The container DAMNIT's builder has to reproduce (HZDR_combo
`planning/NEXUS_OUTPUT_PLAN.md`, phase 1). Written by
`shot_aligner/scripts/make_reference_fixture.py` and checked by
`tests/test_reference_fixture.py`. DAMNIT-web-hzdr vendors this folder.

| Path | What | Origin |
| --- | --- | --- |
| `raw/M1_Spec_Fib_Cer/` | camera PNG + CSV (`camera_png_csv`) | real, `polina/2025_12_01/` shot 2, 15:59:04 |
| `raw/Reflected 515 spectrometer/` | Irr8 spectrum (`spectrometer_irr8`) | real, same shot |
| `raw/Probe135/` | three-frame TIFF recording (`sequence_frames`) | synthetic: no sample is committed |
| `events.jsonl` | the `hzdr-event-v1` events planet-watchdog would send | paths recorded as `/bigdata/HPLexp/reference-fixture/...` |
| `manifest.json` | every group and dataset `build_shot` writes: `NX_class`, shape, dtype, units | regenerated from `raw/`, values left out |

## Which part of the manifest is the contract

For DAMNIT, the contract is the instrument data: the `NXinstrument` families,
their `NXdetector`s, and the per-instrument `NXsubentry`s with what is under
them. These are not part of it:

- `/entry/alignment`: shot-aligner's clock and link evidence. The live flow
  keeps attribution in `/entry/source_events`.
- `/entry/build_provenance`, `/entry/program_name`, `/entry/user`: who built
  it, with what.
- `/entry/shot_info`, `/entry/shot_parameters`, `/entry/shotsheet_provenance`:
  the workbook row. DAMNIT takes it from LabFrog.

## Known gaps, recorded on purpose

`build_shot` reports these in `/entry/alignment/problems`. They are true of
this input, and they are left as they are:

- The real M1 CSV lacks three `peak_profile` parameters its mapping names.
- The synthetic Probe135 recording has no `.rec` sidecar, so the 11 rows its
  mapping reads from it are empty.
- Probe135's mapping claims `NXoptical_spectroscopy` for a camera. This is one
  of the mapping errors the plan's phase 2 fixes.

## Changing it

```sh
uv run shot_aligner/scripts/make_reference_fixture.py          # manifest from raw/
uv run shot_aligner/scripts/make_reference_fixture.py --raws   # raw/ and events too
```

A manifest change is a change to what containers contain. Re-vendor it into
DAMNIT in the same breath.
