# Handoff

Updated: 2026-07-23

## Current state

The HZDR fork has the canonical event model, durable Kafka/ASAPO spools,
identity-first reconciliation, operator review UI, debounced builder trigger,
SciCat registration hook, and local acceptance path implemented on `main`.
The documented deployment URL is
[https://fwkt-damnit.fz-rossendorf.de/](https://fwkt-damnit.fz-rossendorf.de/),
but it was not probed during this local cleanup pass; treat earlier live claims
as historical deployment evidence, not a fresh availability check.

The July Kafka pilot remains deliberately separate from ASAPO/LaserData. The
pilot campaign is `Pilot_Verification_07.2026`, with canonical topics
`draco.trigger` and `planet.watchdog.events`.

## Verified locally on 2026-07-23

- DAMNIT API suite: `346 passed, 5 skipped`.
- HZDR-focused API subset: `245 passed, 4 skipped, 102 deselected`.
- Combo contract board: metadata, repository structure, and docs checks passed.
- The live-broker `-DockerTests` gate was not run.
- No production service, Kafka broker, SciCat instance, or deployment endpoint
  was contacted.

The sibling-suite counts in [testing.md](testing.md) identify their own evidence
dates. Only DAMNIT and DAQ File Watchdog were rerun during this cleanup pass.

## Open gates

1. Configure the deployment Kafka broker and enable the Kafka spool consumer.
2. Run broker-backed restart/replay tests and capture deduplication counts.
3. Run the shotcounter deployment-broker smoke test with `KafkaEnabled=1`, then
   merge `feature/hzdr-canonical-trigger-event` to `main`.
4. Point DAQ File Watchdog at the pilot broker/topic and capture a real pilot
   event through DAMNIT.
5. Enable and verify the builder trigger for the pilot campaign.
6. Verify deployed SciCat PID back-population and unchanged-artifact replay
   suppression. This is not a Kafka transport prerequisite.
7. Keep the ASAPO sidecar/direct-adapter rollout deferred until LaserData,
   compatible packages, and broker credentials are available.

All four documents in `hzdr/docs/plans/` remain active because they contain at
least one of these open or external gates.

## Safe local commands

From the DAMNIT repository root on Windows:

```powershell
.\hzdr\scripts\test-pilot-package.ps1 -NoCoverage
.\hzdr\scripts\hzdr-launch.ps1 -ValidateOnly
```

Run the broker-backed gate only against an approved local or deployment broker:

```powershell
.\hzdr\scripts\test-pilot-package.ps1 -NoCoverage -DockerTests -Broker localhost:9092
```

From `api`, the broker-free acceptance path is:

```powershell
uv run python scripts\hzdr-local-acceptance.py
```

Do not infer deployment health from the deterministic local tests. Record the
broker address, campaign slug, event counts, replay counts, and service restart
result when the live gate is eventually run.
