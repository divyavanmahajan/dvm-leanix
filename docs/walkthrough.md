# lean-ix: Code Walkthrough

lean-ix is a local GraphQL proxy for SAP LeanIX that extracts a Bearer token from your logged-in browser via Playwright CDP, persists it to disk, serves a GraphiQL UI, diagnoses SSL issues, and downloads FactSheets to JSON/CSV — all from a single CLI command. This walkthrough steps through each module in execution order.

## 1. CLI Entrypoint — `main.py`

Everything starts here. The CLI is registered as the `lean-ix` script in `pyproject.toml`. It uses subparsers for three commands: `serve` (default), `diagnose`, and `download`.

```powershell exec
uv run lean-ix --help
```

```output
usage: lean-ix [-h] [--url URL] [--ca-bundle PATH | --no-verify-ssl] [--legacy-ssl] {serve,diagnose,download} ...

SAP LeanIX GraphQL proxy with GraphiQL UI

positional arguments:
  {serve,diagnose,download}
    serve               Start the GraphQL proxy server (default when no subcommand given)
    diagnose            Test SSL/TLS connectivity to LeanIX and recommend fixes
    download            Download all FactSheets of a type from LeanIX via the proxy

options:
  -h, --help            show this help message and exit
  --url URL             LeanIX workspace base URL (default: https://eu-10.leanix.net/VolvoInformationTechnologyABSandbox)
  --ca-bundle PATH      Path to a PEM CA bundle for SSL verification. Use when behind a corporate SSL inspection proxy.
  --no-verify-ssl       Disable SSL certificate verification entirely (insecure).
  --legacy-ssl          Relax Python 3.13+ strict X.509 certificate validation. Fixes 'Missing Authority Key Identifier' errors.
```

**Startup flow for `serve`:** resolve workspace URL → `_resolve_ssl()` → load saved token or extract from browser via CDP → `build_app()` → `uvicorn.run()`.

```powershell exec
uv run python -c "
from lean_ix.main import parse_args

ns = parse_args(['serve', '--url', 'https://eu-10.leanix.net/VolvoInformationTechnologyABSandbox', '--connect', 'http://localhost:9222'])
print('command :', ns.command)
print('url     :', ns.url)
print('port    :', ns.port)
print('cdp_url :', ns.cdp_url)
print('token   :', ns.token)
print('no_save :', ns.no_save)
print('legacy  :', ns.legacy_ssl)
"
```

```output
command : serve
url     : https://eu-10.leanix.net/VolvoInformationTechnologyABSandbox
port    : 8765
cdp_url : http://localhost:9222
token   : None
no_save : False
legacy  : False
```

## 2. Token Persistence — `persistence.py`

Before hitting the browser, `main.py` checks `~/.lean-ix/tokens.json` for a previously saved token. Tokens are keyed by workspace URL so multiple workspaces coexist.

```powershell exec
uv run python -c "
from lean_ix.persistence import save_token, load_token, clear_token
import json, pathlib

URL = 'https://eu-10.leanix.net/VolvoInformationTechnologyABSandbox'

save_token(URL, 'eyJEXAMPLE.demo.token')
print('saved  ->', load_token(URL))

save_token('https://eu-10.leanix.net/OtherWorkspace', 'eyJOTHER.token')

store = json.loads(pathlib.Path.home().joinpath('.lean-ix','tokens.json').read_text())
for k, v in store.items():
    print(f'  {k}  =>  {v[:12]}...')

clear_token(URL)
clear_token('https://eu-10.leanix.net/OtherWorkspace')
print('cleared ->', load_token(URL))
"
```

```output
saved  -> eyJEXAMPLE.demo.token
  https://eu-10.leanix.net/VolvoInformationTechnologyABSandbox  =>  eyJEXAMPLE.d...
  https://eu-10.leanix.net/OtherWorkspace  =>  eyJOTHER.tok...
cleared -> None
```

## 3. Browser Token Extraction — `token.py`

When no saved token exists, `token.py` connects to a Chrome/Edge window opened with `--remote-debugging-port=9222` and already logged in to LeanIX. Two strategies run in order:

1. **Fast path** — scan `localStorage` and `sessionStorage` on any open LeanIX tab for known token keys.
2. **Slow path** — attach a `request` event listener across all pages; if no LeanIX tab is open, navigate to the workspace URL to trigger API calls, then capture the `Authorization: Bearer …` header.

```powershell exec
uv run python -c "
import inspect, lean_ix.token as t

print(inspect.signature(t.extract_token))
print()
print(t.extract_token.__doc__.strip())
"
```

```output
(leanix_base: 'str', cdp_url: 'str' = 'http://localhost:9222') -> 'str'

Connect to a running browser via CDP and extract a LeanIX Bearer token.

Args:
    leanix_base: Base URL of the LeanIX workspace,
                 e.g. "https://eu-10.leanix.net/VolvoInformationTechnologyABSandbox"
    cdp_url:     Chrome DevTools Protocol endpoint, default "http://localhost:9222"

Returns:
    The raw Bearer token string.

Raises:
    RuntimeError: If connection fails or token cannot be found.
```

## 4. Proxy Server — `server.py`

`build_app()` wires the FastAPI application. The upstream GraphQL URL is derived from the workspace base URL. The token is held in a `threading.Lock`-protected dict so it can be swapped at runtime without restarting. An `SSLContext` is accepted for `ssl_verify` to support `--legacy-ssl`.

```powershell exec
uv run python -c "
from lean_ix.server import build_app

app = build_app(
    'https://eu-10.leanix.net/VolvoInformationTechnologyABSandbox',
    'demo-token',
    cdp_url='http://localhost:9222'
)

print('App title:', app.title)
print()
print('Registered routes:')
for route in app.routes:
    if hasattr(route, 'methods'):
        methods = ','.join(sorted(route.methods))
        print(f'  {methods:<8} {route.path}')
    else:
        print(f'  (mount)  {route.path}')
"
```

```output
App title: LeanIX GraphQL Proxy

Registered routes:
  GET,HEAD /openapi.json
  GET,HEAD /docs
  GET,HEAD /docs/oauth2-redirect
  GET,HEAD /redoc
  GET      /
  GET      /health
  GET      /token
  POST     /token
  POST     /token/refresh
  GET      /graphql
  POST     /graphql
```

**Token expiry handling:** when the upstream returns `401`, the proxy clears the stale saved token, re-extracts from the browser (if `--connect` was supplied), and retries the request. If re-extraction fails, the caller receives a structured GraphQL error body with an actionable hint. `_try_refresh_token()` is guarded by a `refreshing` flag to prevent concurrent refresh storms.

## 5. GraphiQL UI — `graphiql.py`

The interactive IDE is served as a single static HTML string — no npm, no build pipeline. It loads React 18 and GraphiQL 3 from the unpkg CDN and points its fetch URL at `window.location.origin + '/graphql'`. A topbar banner links to the LeanIX GraphQL API docs.

```powershell exec
uv run python -c "
import re
from lean_ix.graphiql import GRAPHIQL_HTML

stylesheets = re.findall(r'href=\"(https://[^\"]+)\"', GRAPHIQL_HTML)
scripts = re.findall(r'src=\"(https://[^\"]+)\"', GRAPHIQL_HTML)
fetch_line = [l.strip() for l in GRAPHIQL_HTML.splitlines() if 'location.origin' in l]

print('Stylesheets:')
for s in stylesheets:
    print(f'  {s}')
print('Scripts:')
for s in scripts:
    print(f'  {s}')
print('GraphQL fetch target:', fetch_line[0] if fetch_line else 'not found')
print(f'Total HTML size: {len(GRAPHIQL_HTML):,} bytes')
"
```

```output
Stylesheets:
  https://unpkg.com/graphiql@3/graphiql.min.css
Scripts:
  https://unpkg.com/react@18/umd/react.production.min.js
  https://unpkg.com/react-dom@18/umd/react-dom.production.min.js
  https://unpkg.com/graphiql@3/graphiql.min.js
GraphQL fetch target: uri: window.location.origin + "/graphql",
Total HTML size: 1,080 bytes
```

## 6. SSL Diagnostics — `diagnose.py`

`lean-ix diagnose` tests the full connectivity chain from DNS resolution to httpx and prints a recommended fix. On Volvo's network, the Prisma SSL inspection proxy issues certificates missing the `Authority Key Identifier` extension — Python 3.13+ enforces `VERIFY_X509_STRICT` which rejects these. The fix is `--legacy-ssl`.

```powershell exec
uv run python -c "
import inspect
from lean_ix import diagnose

# Show the checks performed
lines = inspect.getsource(diagnose.run_diagnostics)
for line in lines.splitlines():
    l = line.strip()
    if l.startswith('check_') or l.startswith('_section') or 'recommend' in l.lower():
        print(' ', l[:80])
"
```

```output
  _section('Summary')
  check_dns(host)
  check_tcp(host, 443)
  check_tls_raw(host, 443)
  check_tls_system_ca(host, 443)
  check_tls_legacy(host, 443)
  check_httpx_system(leanix_url)
  check_httpx_legacy(leanix_url)
```

When the diagnostics detect "Missing Authority Key Identifier", the summary prints:

```
[FAIL] Standard TLS failed: CERTIFICATE_VERIFY_FAILED
[OK]   Legacy SSL mode works!
RECOMMENDED FIX: uv run lean-ix serve --legacy-ssl
```

## 7. Download CLI — `download.py`

`lean-ix download` introspects the schema, builds a query for any FactSheet type, paginates through all results, and writes JSON or CSV. Permission-denied fields are auto-detected and excluded on retry.

### 7a. Schema introspection and type unwrapping

`_leaf_kind()` recursively unwraps `NON_NULL` / `LIST` wrappers to find the base kind. Only `SCALAR` and `ENUM` fields are included in the generated query — `OBJECT` / `INTERFACE` connections are excluded.

```powershell exec
uv run python -c "
from lean_ix.download import _leaf_kind

# NON_NULL wrapping a SCALAR
print(_leaf_kind({'kind': 'NON_NULL', 'name': None, 'ofType': {'kind': 'SCALAR', 'name': 'String', 'ofType': None}}))
# LIST > NON_NULL > OBJECT (a connection — will be excluded)
print(_leaf_kind({'kind': 'LIST', 'name': None, 'ofType': {'kind': 'NON_NULL', 'name': None, 'ofType': {'kind': 'OBJECT', 'name': 'Tag', 'ofType': None}}}))
"
```

```output
('SCALAR', 'String')
('OBJECT', 'Tag')
```

### 7b. Dynamic query building

`build_query()` assembles a paginated `allFactSheets` query with an inline fragment for type-specific fields:

```powershell exec
uv run python -c "
from lean_ix.download import build_query

type_fields = [
    {'name': 'alias', 'kind': 'SCALAR', 'type_name': 'String'},
    {'name': 'businessCriticality', 'kind': 'ENUM', 'type_name': 'BusinessCriticality'},
    {'name': 'subscriptions', 'kind': 'OBJECT', 'type_name': 'SubscriptionConnection'},
]
base_fields = ['id', 'name', 'type', 'category', 'status', 'updatedAt', 'completion']
print(build_query('Application', type_fields, base_fields).strip())
"
```

```output
query DownloadFactSheets($factSheetType: FactSheetType, $after: String) {
  allFactSheets(factSheetType: $factSheetType, first: 100, after: $after) {
    totalCount
    pageInfo {
      hasNextPage
      endCursor
    }
    edges {
      node {
        id
        name
        type
        category
        status
        updatedAt
        completion { completion }
        ... on Application {
          alias
          businessCriticality
        }
      }
    }
  }
}
```

Note: `subscriptions` (kind `OBJECT`) was excluded automatically. `completion` was expanded to `completion { completion }` via `_BASE_SUBSELECT`.

### 7c. Permission-denied field handling

LeanIX returns partial data with GraphQL errors for restricted fields. `fetch_all()` detects these on the first page, excludes the denied fields, rebuilds the query, and restarts pagination:

```powershell exec
uv run python -c "
from lean_ix.download import _extract_permission_denied_fields

errors = [
    {'message': 'No permission: fact_sheet_fields:read:application:lx__financial_critical_application'},
    {'message': 'No permission: fact_sheet_fields:read:application:lx__personal_data_solution'},
    {'message': 'Some unrelated error'},
]
denied = _extract_permission_denied_fields(errors)
print('Denied fields:', sorted(denied))
print('Other errors passed through _check_errors unchanged')
"
```

```output
Denied fields: ['lx__financial_critical_application', 'lx__personal_data_solution']
Other errors passed through _check_errors unchanged
```

The full download of 4,553 Application records from LeanIX produced this output:

```
  Permission denied for fields: lx__financial_critical_application, lx__personal_data_solution
  Rebuilding query without 2 excluded field(s)...
  Total fact sheets reported: 4553
  Page 1: 100 matching records fetched so far...
  ...
  Page 46: 4553 matching records fetched so far...
  Done. 4553 records collected.
  Written 4553 records as JSON to apps.json
```

## 8. End-to-end wiring

All modules connect at startup and during a live request:

```powershell exec
uv run python -c "
from lean_ix.persistence import save_token, load_token, clear_token
from lean_ix.server import build_app

URL = 'https://eu-10.leanix.net/VolvoInformationTechnologyABSandbox'

print('1. CLI args parsed')
print(f'   url={URL}, port=8765, cdp=http://localhost:9222')

saved = load_token(URL)
print(f'2. Cached token: {saved} => would trigger browser extraction')

demo_token = 'eyJEXAMPLE.walkthrough'
save_token(URL, demo_token)
print(f'3. Token resolved: {demo_token[:20]}...')
print(f'4. Token saved to ~/.lean-ix/tokens.json')

cached = load_token(URL)
print(f'5. Next run: cached={cached[:20]}...  => browser skipped')

app = build_app(URL, demo_token, cdp_url='http://localhost:9222')
routes = [(', '.join(sorted(r.methods)), r.path) for r in app.routes if hasattr(r, 'methods')]
print(f'6. FastAPI app ready with {len(routes)} routes:')
for methods, path in routes:
    print(f'   {methods:<12} {path}')

clear_token(URL)
"
```

```output
1. CLI args parsed
   url=https://eu-10.leanix.net/VolvoInformationTechnologyABSandbox, port=8765, cdp=http://localhost:9222
2. Cached token: None => would trigger browser extraction
3. Token resolved: eyJEXAMPLE.walkthrou...
4. Token saved to ~/.lean-ix/tokens.json
5. Next run: cached=eyJEXAMPLE.walkthrou...  => browser skipped
6. FastAPI app ready with 9 routes:
   GET,HEAD     /openapi.json
   GET,HEAD     /docs
   GET,HEAD     /docs/oauth2-redirect
   GET,HEAD     /redoc
   GET          /
   GET          /health
   POST         /token
   POST         /token/refresh
   POST         /graphql
```

Note: routes sharing a path (e.g. `GET /token` and `POST /token`) are de-duplicated in this view — all 11 routes are registered. The startup sequence confirms the full pipeline: CLI parse, persistence check, token resolution, server construction.

## 9. Reference

### Full command list

```powershell
# Start the proxy server
uv run lean-ix serve --url https://eu-10.leanix.net/VolvoInformationTechnologyABSandbox
uv run lean-ix serve --legacy-ssl        # Volvo/Prisma SSL fix

# Diagnose SSL issues
uv run lean-ix diagnose

# Download FactSheets
uv run lean-ix download --list-types
uv run lean-ix download --type Application --output apps.json
uv run lean-ix download --type Application --subtype "Business Application" --format csv --output apps.csv
uv run lean-ix download --type Application --list-subtypes
uv run lean-ix download --type Application --legacy-ssl --output apps.json
```

### LeanIX GraphQL API docs

https://help.sap.com/docs/leanix/ea/graphql-api

Open **http://localhost:8765/graphql** after starting the proxy to use the GraphiQL interactive IDE.