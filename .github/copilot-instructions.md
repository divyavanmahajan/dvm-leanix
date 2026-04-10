# Copilot Instructions

## Project Overview

A local GraphQL proxy for SAP LeanIX. It connects to an already-logged-in browser via Playwright CDP, extracts the active Bearer token, and exposes a FastAPI server (`localhost:8765`) that proxies requests to LeanIX and serves a GraphiQL UI.

## Commands

```powershell
# Install dependencies
uv sync

# Run the proxy server
dvm-leanix               # serve (default subcommand)
dvm-leanix serve         # explicit

# Other subcommands
dvm-leanix diagnose      # SSL connectivity diagnostics
dvm-leanix download --type Application --output apps.json

# Add a dependency
uv add some-package          # auto-updates pyproject.toml and uv.lock
```

There is no test suite and no linter configured.

## Architecture

```
src/lean_ix/
├── main.py        # CLI entrypoint — subparsers: serve, diagnose, download
├── token.py       # Playwright CDP token extractor (async; sync wrapper via asyncio.run)
├── persistence.py # Token store: ~/.lean-ix/tokens.json (url → token)
├── server.py      # FastAPI app factory: build_app(url, token, cdp_url, ssl_verify)
├── graphiql.py    # Self-contained GraphiQL HTML string (CDN, no build step)
├── diagnose.py    # SSL/TLS diagnostics (DNS → TCP → TLS → httpx)
└── download.py    # FactSheet downloader with cursor-based pagination
```

**Startup token flow:** `persistence.load_token()` → if missing, `token.extract_token()` (scans localStorage/sessionStorage first, then listens for network requests) → `persistence.save_token()` → `build_app()` → `uvicorn.run()`.

**Token expiry flow:** `POST /graphql` → 401 from LeanIX → `persistence.clear_token()` → if `cdp_url` set, re-extract and retry; otherwise return `TOKEN_EXPIRED` error with hint.

**Download flow:** introspect schema → build paginated `allFactSheets` query with inline fragment `... on TypeName { scalar/enum fields }` → cursor-paginate (100/page) → on first page, detect and exclude permission-denied fields, then restart pagination.

## Key Conventions

- **All Python commands use `uv run`** — not `python` or `pip` directly.
- **`ssl_verify`** is `bool | str | SSLContext` throughout. `_resolve_ssl(args)` in `main.py` is the single place that converts CLI flags + env vars into this value. Pass it through; don't reconstruct it elsewhere.
- **`build_app()` is a factory** — the FastAPI app is not a module-level singleton. Token state is held in a `threading.Lock`-protected dict inside the closure. The `refreshing` flag prevents concurrent 401 refresh storms.
- **`download.py` only includes `SCALAR` and `ENUM` fields** in auto-generated queries. `_leaf_kind()` recursively unwraps `NON_NULL`/`LIST` wrappers to find the base kind; `OBJECT`/`INTERFACE` fields (connections/relations) are excluded.
- **`graphiql.py` is a plain string** — no template engine, no build. To upgrade GraphiQL, change the version pins in the `<script>` and `<link>` tags.
- **Token storage keys to check** when LeanIX changes auth: `access_token`, `token`, `id_token`, `leanix_token` in `_find_token_in_storage()` inside `token.py`.
- **`--legacy-ssl`** disables `VERIFY_X509_STRICT` (needed on Volvo's network where the Prisma SSL inspection proxy issues certs missing `Authority Key Identifier`, rejected by Python 3.13+).
- **Subtype filtering** in `download.py` is client-side on the `category` field — it is not a server-side filter.
