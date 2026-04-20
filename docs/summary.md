# lean-ix — Summary

A local proxy tool that bridges your **already-authenticated browser session** to a developer-friendly GraphQL interface for SAP LeanIX.

---

## What it does

1. **Connects to your browser** — via Chrome DevTools Protocol (Playwright `connect_over_cdp`), attaching to a Chrome/Edge window where you are already logged in to LeanIX.
2. **Or uses a Technical User API key** — exchange a LeanIX API key for a Bearer token via OAuth2 client-credentials, with no browser required. Set `--api-token` or the `LEANIX_API_TOKEN` env var.
3. **Extracts the Bearer token** — by intercepting an outbound LeanIX API request and reading the `Authorization` header. Falls back to checking `localStorage`/`sessionStorage`.
4. **Persists the token** — saves the token to `~/.lean-ix/tokens.json` keyed by workspace URL, so subsequent runs skip the browser-extraction step.
5. **Runs a local proxy server** — FastAPI on `localhost:8765` (configurable) that adds the Bearer token to every forwarded GraphQL request.
6. **Serves GraphiQL** — a full interactive IDE at `GET /graphql` for writing and testing queries against LeanIX.
7. **Handles token expiry** — detects `401 Unauthorized` responses from LeanIX, auto-refreshes via API key (if set) or browser CDP, and prompts the user if re-extraction is needed.
8. **Diagnoses SSL issues** — `lean-ix diagnose` tests DNS → TCP → TLS → httpx connectivity and recommends the exact fix for corporate SSL inspection proxies.
9. **Downloads FactSheets** — `lean-ix download` introspects the schema, builds an optimised query for any FactSheet type, paginates through all results, and writes JSON or CSV. Permission-denied fields are auto-detected and excluded on retry.

---

## Quick start

```powershell
# One-time browser setup — must use --user-data-dir to force a new isolated
# instance (existing Edge/Chrome windows silently ignore --remote-debugging-port)
Start-Process "C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe" `
  "--remote-debugging-port=9222 --user-data-dir=C:\Temp\edge-debug --no-first-run --no-default-browser-check"
# Verify port is open:
Invoke-RestMethod http://localhost:9222/json/version
# → Log in to LeanIX in that window

# Run the proxy
cd lean-ix
dvm-leanix                          # prompts for URL, extracts token from browser
dvm-leanix serve --url https://eu-10.leanix.net/InformationTechnologyABSandbox
dvm-leanix serve --connect http://localhost:9222   # explicit CDP endpoint
dvm-leanix serve --token "eyJ..."   # skip browser, use known Bearer token

# Use Technical User API key (no browser needed)
dvm-leanix serve --api-token "your-api-key"
$env:LEANIX_API_TOKEN = "your-api-key"
dvm-leanix serve

# If you get SSL errors (corporate proxy):
dvm-leanix diagnose                 # diagnose and get recommended fix
dvm-leanix serve --legacy-ssl       # fix for  / Prisma SSL proxy
```

Open **http://localhost:8765/graphql** → GraphiQL UI.

LeanIX GraphQL API docs: **https://help.sap.com/docs/leanix/ea/graphql-api**

---

## Subcommands

| Subcommand | Description |
|------------|-------------|
| `serve` (default) | Start the GraphQL proxy server and GraphiQL UI |
| `diagnose` | Test SSL/TLS connectivity and recommend fixes |
| `download` | Download all FactSheets of a type to JSON or CSV |

---

## Key endpoints (proxy server)

| Method | Path             | Description                            |
|--------|------------------|----------------------------------------|
| GET    | `/graphql`       | GraphiQL interactive UI                |
| POST   | `/graphql`       | GraphQL proxy to LeanIX                |
| GET    | `/health`        | Health check + upstream URL            |
| GET    | `/token`         | Show masked current Bearer token       |
| POST   | `/token`         | Replace token manually                 |
| POST   | `/token/refresh` | Re-extract token from connected browser|

---

## CLI flags

### Shared (all subcommands)

| Flag              | Default                                                      | Description                                      |
|-------------------|--------------------------------------------------------------|--------------------------------------------------|
| `--url`           | `https://eu-10.leanix.net/InformationTechnologyABSandbox` | LeanIX workspace base URL                      |
| `--ca-bundle`     | _(none)_                                                     | Path to PEM CA bundle (corporate proxy)          |
| `--no-verify-ssl` | _(off)_                                                      | Disable SSL verification entirely (insecure)     |
| `--legacy-ssl`    | _(off)_                                                      | Relax Python 3.13+ strict X.509 — fixes  proxy |

### `serve` subcommand

| Flag          | Default                 | Description                                      |
|---------------|-------------------------|--------------------------------------------------|
| `--port`      | `8765`                  | Local server port                                |
| `--connect`   | `http://localhost:9222` | Chrome DevTools Protocol endpoint                |
| `--token`     | _(none)_                | Use this Bearer token directly                   |
| `--api-token` | _(none)_                | LeanIX Technical User API key (also: `LEANIX_API_TOKEN` env var) |
| `--no-save`   | _(off)_                 | Do not persist the token to disk                 |

### `download` subcommand

| Flag              | Default                           | Description                                      |
|-------------------|-----------------------------------|--------------------------------------------------|
| `--type`, `-t`    | _(required)_                      | FactSheet type (e.g. `Application`)              |
| `--subtype`, `-s` | _(none)_                          | Filter by category/subtype (repeatable)          |
| `--proxy`         | `http://localhost:8765/graphql`   | GraphQL proxy URL                                |
| `--output`, `-o`  | stdout                            | Output file path                                 |
| `--format`, `-f`  | `json`                            | Output format: `json` or `csv`                   |
| `--list-types`    | _(off)_                           | List all FactSheet types and exit                |
| `--limit`, `-n`   | _(none)_                          | Stop after N records (for testing)               |

---

## Download examples

```powershell
# List all FactSheet types in the schema
dvm-leanix download --list-types

# Download all Applications as JSON
dvm-leanix download --type Application --output apps.json

# Download only first 10 Applications (for testing)
dvm-leanix download --type Application --limit 10 --output sample.json
dvm-leanix download --type Application -n 10 --output sample.json

# Download only "Business Application" sub-type as CSV
dvm-leanix download --type Application --subtype "Business Application" --format csv --output apps.csv

# Download all ITComponents
dvm-leanix download --type ITComponent --output itcomponents.json

# List available subtypes for Application
dvm-leanix download --type Application --list-subtypes
```

---

## Token lifecycle

```
startup
  │
  ├─► --token provided?  → use it directly
  │
  ├─► --api-token / LEANIX_API_TOKEN set?
  │     └─ YES → OAuth2 exchange → Bearer token (no browser)
  │
  ├─► load ~/.lean-ix/tokens.json
  │     ├─ token found → use it (skip browser)
  │     └─ not found  → extract from browser via CDP
  │
  ├─► save token to disk (unless --no-save)
  │
  │   [during proxying]
  │
  └─► 401 from LeanIX?
        ├─ api_key set  → OAuth2 re-exchange  → retry request
        ├─ CDP available → auto-re-extract    → retry request
        └─ neither       → return 401 with message, prompt user to POST /token/refresh
```

---

## SSL / Corporate proxy

If you are behind a corporate SSL inspection proxy (e.g.  Prisma):

```powershell
# Step 1: diagnose
dvm-leanix diagnose

# Step 2: apply recommended fix (usually --legacy-ssl for )
dvm-leanix serve --legacy-ssl
dvm-leanix download --type Application --legacy-ssl --output apps.json
```

`--legacy-ssl` disables Python 3.13+ strict X.509 validation which rejects certificates missing the `Authority Key Identifier` extension — a common characteristic of corporate MITM proxy certificates.
