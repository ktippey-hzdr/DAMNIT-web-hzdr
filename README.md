# DAMNIT Web

Monorepo containing projects required to serve DAMNIT data over the web.

**This is the HZDR fork** of
[European-XFEL/DAMNIT-web](https://github.com/European-XFEL/DAMNIT-web). It
adds source integration for LabFrog, LaserData/ASAPO, DAQ File Watchdog, and
Kafka, and builds canonical per-campaign NeXus files. Everything
HZDR-specific lives under [hzdr/](hzdr/) or in `hzdr`-prefixed modules; the
rest of the tree tracks upstream.

## Standard launch: configured API and frontend

Prerequisites: [uv](https://docs.astral.sh/uv/) and Node >= 24 (via nvm).

Install dependencies and git hooks:

    ./scripts/setup-dev.sh

### Run the configured API (spool mode)

This is the regular service launch. It reads `api/.env` and, for the lifetime
of the API process, runs any Kafka/ASAPO spool consumers and the builder enabled
there. Start from `api/.env.production.example` when configuring those HZDR
services; the smaller `.env.example` is enough for the upstream-style API.

Auth mode is the usual setup. Copy the env template, fill in the credentials,
then start the server:

Windows PowerShell:

    Copy-Item api\.env.example api\.env
    Set-Location api
    uv run -m damnit_api.main

Linux/macOS:

    cp api/.env.example api/.env
    cd api
    uv run -m damnit_api.main

The API serves at http://localhost:8000.

Local mode needs no credentials. Point it at a local DAMNIT directory
(one with runs.sqlite, context.py and extracted_data); auth is disabled:

Windows PowerShell:

    Set-Location api
    uv run -m damnit_api.main --path C:\path\to\damnit\dir

Linux/macOS:

    cd api
    uv run -m damnit_api.main --path /path/to/damnit/dir

### Run the frontend

Copy the env template and set VITE_API to your API (defaults to
http://localhost:8000):

Windows PowerShell:

    Copy-Item frontend\apps\app\.env.example frontend\apps\app\.env.local

Linux/macOS:

    cp frontend/apps/app/.env.example frontend/apps/app/.env.local

Then start the dev server from the frontend directory:

Windows PowerShell:

    Set-Location frontend
    pnpm run dev:app

Linux/macOS:

    cd frontend
    pnpm run dev:app

The app serves at http://localhost:5173/app/. If pnpm is not found, run
`nvm use 24` first.

## HZDR emulator launch

This separate all-in-one development command generates a sample source catalog
from the bundled event packages, then starts the API with that emulator catalog
and the frontend. It is an orientation/UI mode, not the configured spool
ingestion launch above:

    # Windows
    .\hzdr\scripts\hzdr-launch.ps1 -InitConfig
    .\hzdr\scripts\hzdr-launch.ps1

    # Linux
    bash hzdr/scripts/hzdr-launch.sh --init-config
    bash hzdr/scripts/hzdr-launch.sh

Open `http://127.0.0.1:5173/home` or the flow monitor at
`http://127.0.0.1:5173/flow-monitor`.

## HZDR documentation

- [Documentation index](hzdr/docs/README.md)
- [Screenshots of the UI](hzdr/docs/screenshots.md)
- [System overview](hzdr/docs/system-overview.md)
- [Architecture and identity rules](hzdr/docs/architecture.md)
- [Local development and verification](hzdr/docs/guides/local-development.md)
- [Current handoff notes](hzdr/docs/status/handoff.md)

API-specific documentation remains in [`api/docs`](api/docs), and frontend
development commands remain in [`frontend/README.md`](frontend/README.md).

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for how checks run and how to
contribute.
