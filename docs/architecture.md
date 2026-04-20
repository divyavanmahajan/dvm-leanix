# Architecture

## Overview

```
┌─────────────────────────────────────────────────────────────────────┐
│                          Developer Machine                          │
│                                                                     │
│  ┌───────────────────────┐        ┌─────────────────────────────┐  │
│  │  Chrome / Edge        │        │  lean-ix proxy              │  │
│  │  (logged in to        │◄──CDP──│  (FastAPI on :8765)         │  │
│  │   LeanIX)             │        │                             │  │
│  │                       │        │  GET  /graphql  → GraphiQL  │  │
│  │  port 9222 (debug)    │        │  POST /graphql  → proxy     │  │
│  └───────────────────────┘        │  POST /token    → update    │  │
│                                   │  POST /token/refresh        │  │
│  ┌───────────────────────┐        └────────────┬────────────────┘  │
│  │  GraphiQL (browser)   │────POST /graphql────►│                  │
│  │  http://localhost:8765│                      │                  │
│  └───────────────────────┘                      │                  │
│                                                 │ Bearer token     │
│  ┌───────────────────────┐                      │ in header        │
│  │  ~/.lean-ix/          │◄──save/load──────────┘                  │
│  │    tokens.json        │                                         │
│  └───────────────────────┘                                         │
│                                                                     │
│  ┌───────────────────────┐                                         │
│  │  lean-ix download     │──POST http://localhost:8765/graphql──►  │
│  │  (CLI)                │  (introspect schema + paginate)         │
│  └───────────────────────┘                                         │
│                                                                     │
│  ┌───────────────────────┐                                         │
│  │  lean-ix diagnose     │──direct HTTPS to LeanIX──────────────► │
│  │  (SSL diagnostics)    │  (DNS → TCP → TLS → httpx checks)      │
│  └───────────────────────┘                                         │
└─────────────────────────────────────────────────┬───────────────────┘
                                                  │ HTTPS
                                                  ▼
                              ┌────────────────────────────────────┐
                              │  SAP LeanIX                        │
                              │  https://eu-10.leanix.net          │
                              │    /services/pathfinder/v1/graphql │
                              └────────────────────────────────────┘
```

---

## Module breakdown

### `main.py` — CLI entrypoint

Responsibilities:
- Parse CLI arguments using subparsers: `serve` (default), `diagnose`, `download`
- Shared SSL flags (`--ca-bundle`, `--no-verify-ssl`, `--legacy-ssl`) injected into every subcommand via `_add_shared()`
- `_resolve_ssl()` converts flags into the correct `ssl_verify` value (`bool | str | SSLContext`)
- Prompt for workspace URL if not supplied
- Orchestrate startup: load token → extract if missing → start server
- Route `diagnose` → `diagnose.run_diagnostics()`, `download` → `download.run_download()`

### `token.py` — Playwright CDP token extractor + OAuth2 exchange

Responsibilities:
- **Browser path**: Connect to an existing browser via `playwright.chromium.connect_over_cdp(cdp_url)`
  - **Fast path**: scan `localStorage` / `sessionStorage` for known token keys
  - **Slow path**: attach `request` event listeners across all pages; wait for a LeanIX API request and capture the `Authorization: Bearer …` header
  - If no LeanIX page is open, navigate to the workspace URL to trigger API calls
- **API key path**: `get_token_from_api_key(api_key, leanix_base, ssl_verify)` — POST to `/services/mtm/v1/oauth2/token` with `auth=("apitoken", key)` and `grant_type=client_credentials`, returns the Bearer access token. No browser required.

### `persistence.py` — Token store

Responsibilities:
- Read/write `~/.lean-ix/tokens.json` — a JSON object mapping workspace URL → token string
- `save_token(url, token)` — persist after successful extraction
- `load_token(url)` → `str | None` — load on startup
- `clear_token(url)` — remove when a token is confirmed invalid

### `server.py` — FastAPI proxy

Responsibilities:
- Hold the current Bearer token in a thread-safe mutable state
- Accept GraphQL requests and forward them to the LeanIX upstream
- Detect `401 Unauthorized` responses and trigger `_try_refresh_token()`
  - If `api_key` is set: re-exchange via OAuth2 (`get_token_from_api_key`) — no browser needed
  - Else if `cdp_url` is set: re-extract from browser via CDP
  - Else: return `TOKEN_EXPIRED` error with hint
- `refreshing` flag prevents concurrent refresh storms — only one refresh attempt runs at a time
- Serve the GraphiQL UI HTML at `GET /graphql`
- Expose management endpoints: `/token`, `/token/refresh`, `/health`

### `graphiql.py` — GraphiQL HTML

Static HTML string embedding React 18 + GraphiQL 3 from CDN (unpkg).
No build step required. The fetch URL is set to `window.location.origin + "/graphql"`.
Includes a topbar link to the LeanIX GraphQL API docs at https://help.sap.com/docs/leanix/ea/graphql-api

### `diagnose.py` — SSL connectivity diagnostics

Responsibilities:
- Step through connectivity checks: DNS → TCP port 443 → raw TLS → TLS with system CA → TLS with legacy mode → httpx GET
- Print `[OK]` / `[FAIL]` / `[WARN]` status for each check
- Detect "Missing Authority Key Identifier" in the cert chain (root cause for proxy failures)
- Export the corporate CA chain to a PEM file for use with `--ca-bundle`
- Print a summary with the exact recommended fix command

### `download.py` — FactSheet downloader

Responsibilities:
- `introspect_type(proxy_url, type_name)` — query `__type(name: "TypeName")` to get all fields and their GraphQL kinds
- `build_query(type_name, type_fields, base_fields)` — build a paginated `allFactSheets` query with an inline fragment `... on TypeName { ... }` for type-specific scalar/enum fields
- `fetch_all(...)` — cursor-based pagination (100 records/page); on the first page, detect permission-denied field errors, exclude them, rebuild the query, and restart from page 1
- `write_json()` / `write_csv()` — output formatters; CSV uses `_flatten()` to expand nested objects to dotted keys
- `run_download()` — full orchestration: resolve type → introspect → validate base fields → build query → paginate → write output

---

## Token flow (startup)

```
main()
  │
  ├─ args.token provided? ────────────────────────────────────────► use_token()
  │
  ├─ api_key (--api-token / LEANIX_API_TOKEN env)?
  │    └─ YES → token.get_token_from_api_key(api_key, url)
  │               ├─ success ──────────────────────────────────────► use_token()
  │               └─ failure → exit 1
  │
  ├─ persistence.load_token(url)
  │    ├─ found  ──────────────────────────────────────────────────► use_token()
  │    └─ not found
  │         └─ token.extract_token(url, cdp_url)
  │              ├─ connect_over_cdp(cdp_url)
  │              ├─ scan localStorage/sessionStorage
  │              │    ├─ found  ──────────────────────────────────► use_token()
  │              │    └─ not found
  │              │         └─ listen for network requests
  │              │              └─ capture Bearer header ──────────► use_token()
  │              └─ (timeout → RuntimeError → exit 1)
  │
  └─ persistence.save_token(url, token)
       └─ build_app(url, token, cdp_url, api_key=api_key) → uvicorn.run()
```

## Token flow (expiry / 401)

```
POST /graphql
  │
  ├─ forward to LeanIX with current token
  │
  ├─ response.status == 401 ?
  │    └─ YES
  │         ├─ persistence.clear_token(url)
  │         ├─ api_key configured?
  │         │    ├─ YES → token.get_token_from_api_key(api_key, url)
  │         │    │         ├─ success → save + retry request
  │         │    │         └─ failure → 401 with { "expired": true, "hint": "..." }
  │         │    └─ NO
  │         │         ├─ cdp_url configured?
  │         │         │    ├─ YES → token.extract_token(url, cdp_url)
  │         │         │    │         ├─ success → save + retry request
  │         │         │    │         └─ failure → 401 with { "expired": true, "hint": "..." }
  │         │         │    └─ NO  → 401 with { "expired": true, "hint": "POST /token/refresh" }
  │
  └─ return response to caller
```

---

## Download flow

```
lean-ix download --type Application --output apps.json
  │
  ├─ introspect_type(proxy, "Application")    → 91 fields (60 scalar/enum)
  ├─ introspect_type(proxy, "BaseFactSheet")  → validate base fields against schema
  │
  ├─ build_query("Application", type_fields, base_fields)
  │    └─ generates paginated allFactSheets query with ... on Application { ... }
  │
  ├─ fetch_all(proxy, query, "Application", subtypes=[])
  │    ├─ page 1 → _gql() → detect permission-denied errors
  │    │    └─ denied fields found?
  │    │         ├─ YES → exclude fields, rebuild query, restart pagination
  │    │         └─ NO  → accumulate records
  │    ├─ page 2..N → accumulate records (100/page)
  │    └─ pageInfo.hasNextPage == false → done
  │
  └─ write_json(records, apps.json)   (or write_csv)
```

---

## SSL resolution

```
_resolve_ssl(args)
  │
  ├─ --no-verify-ssl  → False                  (skip all TLS verification)
  ├─ --ca-bundle PATH → str | SSLContext        (custom PEM bundle)
  ├─ --legacy-ssl     → SSLContext              (ctx.verify_flags &= ~VERIFY_X509_STRICT)
  ├─ REQUESTS_CA_BUNDLE / SSL_CERT_FILE env var → str
  └─ default          → True                   (system CA / certifi)
```

`--legacy-ssl` is needed on corporate network because the Prisma SSL inspection proxy issues certificates missing the `Authority Key Identifier` extension. Python 3.13+ enforces `VERIFY_X509_STRICT` which rejects these.

---

## Data flow for a GraphQL query

```
Client (GraphiQL / curl / code)
  POST /graphql
  Body: { "query": "{ allFactSheets { ... } }" }
          │
          ▼
  FastAPI graphql_proxy()
    1. Read body bytes
    2. Parse JSON
    3. Add "Authorization: Bearer <token>" header
    4. httpx.AsyncClient.post(upstream_url, json=body, headers=headers)
          │
          ▼
  LeanIX  POST /services/pathfinder/v1/graphql
          │
          ▼
  Response (JSON) ──────────────────────────────► client
```

---

## Security considerations

- The proxy runs on `127.0.0.1` only — not exposed to the network.
- `~/.lean-ix/tokens.json` is created with `0600` permissions on POSIX systems.
  On Windows, the file is in the user's home directory.
- Tokens are never logged or exposed in full; masked previews are shown.
- CDP is only used locally and only when a `--connect` endpoint is provided.
