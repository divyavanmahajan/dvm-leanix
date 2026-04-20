# lean-ix — SAP LeanIX GraphQL Proxy

A local proxy server that:
- Connects to your **already-logged-in** browser via Playwright CDP
- Extracts the active Bearer token from LeanIX network requests
- Exposes a local GraphQL endpoint that proxies to LeanIX
- Serves a **GraphiQL** UI for interactive exploration

---

## Prerequisites

- Python 3.14+ and [uv](https://docs.astral.sh/uv/)
- Google Chrome or Microsoft Edge installed
- Playwright browsers installed (one-time step)

```powershell
# One-time: install Playwright browser binaries
uv run playwright install chromium
```

---

## Quick Start

### Step 1 — Launch a debug Edge/Chrome instance

> **If Edge or Chrome is already running**, you cannot simply add `--remote-debugging-port` to a
> new shortcut — the flag is silently ignored and the port never opens. You must launch a
> **separate isolated instance** using a different `--user-data-dir`.

```powershell
# Edge (recommended — already installed on Windows)
Start-Process "C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe" `
  "--remote-debugging-port=9222 --user-data-dir=C:\Temp\edge-debug --no-first-run --no-default-browser-check"

# Chrome
Start-Process "C:\Program Files\Google\Chrome\Application\chrome.exe" `
  "--remote-debugging-port=9222 --user-data-dir=C:\Temp\chrome-debug --no-first-run"
```

Verify the debug port is active before continuing:

```powershell
Invoke-RestMethod http://localhost:9222/json/version
```

You should see a JSON response with browser version info. If you get a connection error, the
browser did not start with the debug port — check that you closed all existing windows of
that browser first, or that the `--user-data-dir` path differs from your normal profile.

Log in to LeanIX in that browser window before proceeding.

### Step 2 — Start the proxy

```powershell
cd lean-ix
dvm-leanix
```

You will be prompted for the LeanIX workspace URL if not provided via `--url`.  
The tool connects to Chrome, captures the Bearer token, then starts the local server.

### Step 3 — Open GraphiQL

Navigate to: **<http://localhost:8765/graphql>**

---

## CLI Options

```
dvm-leanix [OPTIONS]

Options:
  --url URL           LeanIX workspace base URL
                      (default: https://eu-10.leanix.net/YourInstance)
  --port PORT         Local port (default: 8765)
  --connect CDP_URL   Chrome DevTools Protocol endpoint (default: http://localhost:9222)
  --token TOKEN       Use this Bearer token directly — skips browser extraction
  --api-token KEY     LeanIX Technical User API key — exchanges for Bearer token via OAuth2
                      (no browser needed; also read from env var LEANIX_API_TOKEN)
  --ca-bundle PATH    PEM CA bundle (corporate SSL proxy fix)
  --no-verify-ssl     Disable SSL verification entirely (insecure)
```

### Examples

```powershell
# Use default URL, prompted if missing
dvm-leanix

# Specify workspace URL explicitly
dvm-leanix --url https://eu-10.leanix.net/MyOtherWorkspace

# Use Technical User API key (no browser needed)
dvm-leanix --api-token "your-api-key-here"

# API key via environment variable
$env:LEANIX_API_TOKEN = "your-api-key-here"
dvm-leanix

# Corporate SSL proxy — point at exported CA bundle
dvm-leanix --ca-bundle "$env:USERPROFILE\.lean-ix\corporate-ca.pem"

# Disable SSL verification (quick test only)
dvm-leanix --no-verify-ssl

# Use a known Bearer token (no browser needed)
dvm-leanix --token "eyJhbGci..."

# Different port
dvm-leanix --port 9000
```

---

## Endpoints

| Method | Path       | Description                         |
|--------|------------|-------------------------------------|
| GET    | `/`        | Redirects to `/graphql`             |
| GET    | `/graphql` | GraphiQL interactive UI             |
| POST   | `/graphql` | GraphQL proxy to LeanIX             |
| GET    | `/health`  | Health check + upstream URL         |
| GET    | `/token`   | Show masked current Bearer token    |
| POST   | `/token`   | Replace Bearer token at runtime     |

### Replace token at runtime

If the token expires, refresh it without restarting:

```powershell
Invoke-RestMethod -Uri http://localhost:8765/token -Method POST `
  -ContentType "application/json" `
  -Body '{"token": "eyJhbGci..."}'
```

---

---

## Technical User Credentials (No Browser Required)

For automated scripts, CI pipelines, or headless environments you can authenticate with a **Technical User API key** instead of a browser session.

### Create a Technical User

1. In your LeanIX workspace go to **Administration → Technical Users**
2. Click **Create Technical User**, set a name, assign roles
3. Copy the generated **API key** (shown only once)

### Start the proxy with an API key

```powershell
# Pass directly
dvm-leanix --api-token "your-api-key-here"

# Or export as environment variable
$env:LEANIX_API_TOKEN = "your-api-key-here"
dvm-leanix
```

The proxy exchanges the key for a Bearer token via OAuth2 at startup and automatically re-exchanges it whenever the token expires — no browser interaction needed.

### How it works

```
API key
  │
  │  POST /services/mtm/v1/oauth2/token
  │  auth: ("apitoken", API_KEY)
  │  body: grant_type=client_credentials
  ▼
Bearer access_token
  │
  ▼
server.py (FastAPI on localhost:8765)
  │  POST /graphql  { query, variables }
  │  Authorization: Bearer <token>
  ▼
https://eu-10.leanix.net/services/pathfinder/v1/graphql
```

---

## SAP LeanIX GraphQL API

Full reference: **[docs/graphql-api.md](docs/graphql-api.md)**

The SAP LeanIX GraphQL API conforms to the [October 2021 GraphQL specification](https://spec.graphql.org/October2021/).

### Key concepts

| Concept | Detail |
|---|---|
| **Fact Sheets** | Core entities: Application, ITComponent, BusinessCapability, Process, Interface, DataObject, Provider, TechnicalStack, UserGroup, Project |
| **Queries** | Read data — `factSheet(id)`, `allFactSheets(filter, sort, first, after)` |
| **Mutations** | Write data — `createFactSheet`, `updateFactSheet`, `archiveFactSheet` |
| **Pagination** | Relay cursor-based (`first` + `after` with `pageInfo.endCursor`) |
| **Filtering** | `facetFilters` with `OR` / `AND` / `NOR` operators on facet keys |

### GraphQL endpoint

```
POST https://{HOST}/services/pathfinder/v1/graphql
Authorization: Bearer {access_token}
Content-Type: application/json
```

### Quick example

```graphql
{
  allFactSheets(factSheetType: Application, first: 10) {
    totalCount
    pageInfo { hasNextPage endCursor }
    edges {
      node { id name displayName lxState completion { percentage } }
    }
  }
}
```

### Error handling

GraphQL always returns HTTP 200. Check the `errors` array in the response body:

```json
{
  "data": null,
  "errors": [{ "message": "No fact sheet found with id '...'" }]
}
```

See **[docs/graphql-api.md](docs/graphql-api.md)** for filtering, pagination, mutations, the REST migration guide, and best practices.

---

## Architecture

```
Browser (logged in)
    │
    │  Chrome DevTools Protocol (port 9222)
    ▼
token.py  ── intercepts Authorization header ──► Bearer token
    │
    ▼
server.py (FastAPI on localhost:8765)
    │  POST /graphql  { query, variables }
    │
    │  Authorization: Bearer <token>
    ▼
https://eu-10.leanix.net/services/pathfinder/v1/graphql
```

---

## Troubleshooting

**"Could not connect to browser at http://localhost:9222"**  
→ If Edge/Chrome is already running, the `--remote-debugging-port` flag is **silently ignored** by any new window you open — it only works on a fresh browser process. Launch a separate isolated instance with its own profile:

```powershell
Start-Process "C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe" `
  "--remote-debugging-port=9222 --user-data-dir=C:\Temp\edge-debug --no-first-run --no-default-browser-check"
```

Then verify the port is open before retrying `lean-ix`:
```powershell
Invoke-RestMethod http://localhost:9222/json/version   # must return JSON
```

**"Timed out waiting for a Bearer token"**  
→ No LeanIX API calls were detected. Make sure you are logged in and try navigating within LeanIX to trigger an API request.

**`SSL: CERTIFICATE_VERIFY_FAILED` — self-signed certificate in chain**  
→ Your corporate network uses SSL inspection (a man-in-the-middle proxy that replaces certificates with ones signed by an internal CA). Python's SSL stack rejects these because the corporate root CA is not in its trust bundle.

**Option 1 — Export the corporate CA and point lean-ix at it (recommended)**

```powershell
# Export all trusted root CAs from the Windows certificate store to a PEM file
$certs = Get-ChildItem -Path Cert:\LocalMachine\Root
$pem = $certs | ForEach-Object { "-----BEGIN CERTIFICATE-----`n" + [Convert]::ToBase64String($_.RawData, 'InsertLineBreaks') + "`n-----END CERTIFICATE-----" }
$pem | Set-Content -Path "$env:USERPROFILE\.lean-ix\corporate-ca.pem" -Encoding ascii
```

Then run lean-ix with the bundle:
```powershell
dvm-leanix --ca-bundle "$env:USERPROFILE\.lean-ix\corporate-ca.pem"
```

**Option 2 — Disable verification entirely (quick test only, not recommended)**

```powershell
dvm-leanix --no-verify-ssl
```

**Token expired mid-session**  
→ Navigate in the LeanIX browser tab (which triggers a token refresh) then POST the new token to `/token`, or restart `lean-ix`.
