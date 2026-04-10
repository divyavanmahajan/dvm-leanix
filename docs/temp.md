# lean-ix: Code Walkthrough

*2026-03-14T13:40:07Z by Showboat 0.6.1*
<!-- showboat-id: 2861a828-a16b-4086-8854-374693d44cba -->

lean-ix is a local GraphQL proxy for SAP LeanIX that extracts a Bearer token from your logged-in browser via Playwright CDP, persists it to disk, and serves a GraphiQL UI — all from a single CLI command. This walkthrough steps through each module in execution order.

## 1. CLI Entrypoint — `main.py`

Everything starts here. The CLI is registered as the `lean-ix` script in `pyproject.toml`. Let's see what flags are available.

```powershell
dvm-leanix --help
```

```output
usage: lean-ix [-h] [--url URL] [--port PORT] [--connect CDP_URL]
               [--token TOKEN] [--no-save]

SAP LeanIX GraphQL proxy with GraphiQL UI

options:
  -h, --help         show this help message and exit
  --url URL          LeanIX workspace base URL (default: https://eu-
                     10.leanix.net/VolvoInformationTechnologyABSandbox)
  --port PORT        Port for the proxy server (default: 8765)
  --connect CDP_URL  Connect to an existing browser via Chrome DevTools
                     Protocol. Start Chrome with --remote-debugging-port=9222.
                     (default: http://localhost:9222)
  --token TOKEN      Use this Bearer token directly (skips browser extraction)
  --no-save          Do not save the token to ~/.lean-ix/tokens.json

lean-ix � SAP LeanIX GraphQL proxy CLI

Usage
-----
    dvm-leanix [OPTIONS]

Options
-------
    --url       LeanIX workspace base URL
                (default: https://eu-10.leanix.net/VolvoInformationTechnologyABSandbox)
    --port      Port to listen on (default: 8765)
    --connect   Chrome DevTools Protocol endpoint to connect to an existing
                browser session (default: http://localhost:9222)
    --token     Skip browser extraction and use this Bearer token directly
    --no-save   Do not persist the token to ~/.lean-ix/tokens.json
```

**Startup flow:** the CLI resolves the workspace URL (prompting if omitted), then tries token sources in order: `--token` flag → saved token in `~/.lean-ix/tokens.json` → live extraction from browser via CDP. The resolved token and CDP URL are then passed into the server.

```powershell
uv run python -c "
from lean_ix.main import parse_args

# Simulate: user passes --url and --connect
ns = parse_args(['--url', 'https://eu-10.leanix.net/VolvoInformationTechnologyABSandbox', '--connect', 'http://localhost:9222'])
print('url     :', ns.url)
print('port    :', ns.port)
print('cdp_url :', ns.cdp_url)
print('token   :', ns.token)
print('no_save :', ns.no_save)
"
```

```output
url     : https://eu-10.leanix.net/VolvoInformationTechnologyABSandbox
port    : 8765
cdp_url : http://localhost:9222
token   : None
no_save : False
```

## 2. Token Persistence — `persistence.py`

Before hitting the browser, `main.py` checks `~/.lean-ix/tokens.json` for a previously saved token. This avoids re-launching Playwright on every run. Tokens are keyed by workspace URL so multiple workspaces coexist.

```powershell
uv run python -c "
from lean_ix.persistence import save_token, load_token, clear_token
import json, pathlib

URL = 'https://eu-10.leanix.net/VolvoInformationTechnologyABSandbox'

# Save a token
save_token(URL, 'eyJEXAMPLE.demo.token')
print('saved  ->', load_token(URL))

# A second workspace can coexist
save_token('https://eu-10.leanix.net/OtherWorkspace', 'eyJOTHER.token')

# Show the on-disk structure (masked)
store = json.loads(pathlib.Path.home().joinpath('.lean-ix','tokens.json').read_text())
for k, v in store.items():
    print(f'  {k}  =>  {v[:12]}...')

# Clean up demo entries
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

When no saved token exists, `token.py` connects to a Chrome/Edge window that the user has already opened with `--remote-debugging-port=9222` and is logged in to LeanIX. It uses Playwright's `connect_over_cdp` so it takes over an *existing* session rather than launching a new browser.

Two strategies run in order:

1. **Fast path** — scan `localStorage` and `sessionStorage` on any open LeanIX tab for known token keys.  
2. **Slow path** — attach a `request` event listener across all pages; if no LeanIX tab is open, navigate to the workspace URL to trigger API calls, then capture the `Authorization: Bearer …` header from the first matching request.

```powershell
uv run python -c "
import inspect, lean_ix.token as t

# Show the public surface of the module
members = [(n, type(v).__name__) for n, v in inspect.getmembers(t) if not n.startswith('_')]
for name, kind in members:
    print(f'{name:<30} {kind}')
"
```

```output
Browser                        type
BrowserContext                 type
CDP_URL                        str
Optional                       _SpecialForm
annotations                    _Feature
async_playwright               function
asyncio                        module
extract_token                  function
get_token_sync                 function
re                             module
```

```powershell
uv run python -c "
import inspect, lean_ix.token as t

# Show the docstring and signature of the main entry point
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

`build_app()` wires the FastAPI application. The upstream GraphQL URL is derived from the workspace base URL by replacing the path with `/services/pathfinder/v1/graphql`. The token is held in a `threading.Lock`-protected dict so it can be swapped at runtime without restarting.

```powershell
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

**Token expiry handling:** when the upstream returns `401`, the proxy clears the stale saved token, attempts to re-extract a fresh one from the connected browser (if `--connect` was supplied), and retries the request transparently. If re-extraction also fails, the caller receives a structured GraphQL error body with an actionable hint.

```powershell
uv run python -c "
import json

# Simulate the error body a client receives when the token is expired
# and the browser is not connected for auto-refresh
error_body = {
    'errors': [{
        'message': 'LeanIX token expired or invalid.',
        'extensions': {
            'code': 'TOKEN_EXPIRED',
            'hint': 'POST /token/refresh to re-extract from browser, or POST /token with {\\	oken\\: \\<new_token>\\}'
        }
    }]
}
print(json.dumps(error_body, indent=2))
"
```

```output
{
  "errors": [
    {
      "message": "LeanIX token expired or invalid.",
      "extensions": {
        "code": "TOKEN_EXPIRED",
        "hint": "POST /token/refresh to re-extract from browser, or POST /token with {\\\token\\: \\<new_token>\\}"
      }
    }
  ]
}
```

## 5. GraphiQL UI — `graphiql.py`

The interactive IDE is served as a single static HTML string — no npm, no build pipeline. It loads React 18 and GraphiQL 3 from the unpkg CDN and points its fetch URL at `window.location.origin + '/graphql'`, so it automatically talks to the local proxy.

```powershell
uv run python C:\Temp\graphiql_inspect.py
```

```output
Stylesheets:
  https://unpkg.com/graphiql@3/graphiql.min.css
Scripts:
  https://unpkg.com/react@18/umd/react.production.min.js
  https://unpkg.com/react-dom@18/umd/react-dom.production.min.js
  https://unpkg.com/graphiql@3/graphiql.min.js
GraphQL fetch target: window.location.origin + "/graphql"
Total HTML size: 1,080 bytes
```

## 6. End-to-end wiring

Here is how all modules connect at startup and during a live request.

```powershell
uv run python C:\Temp\wiring.py
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

Note: routes sharing a path (e.g. `GET /token` and `POST /token`) are collapsed to one entry in the dict above — all 11 routes are registered. The startup sequence confirms the full pipeline: CLI parse, persistence check, token resolution, server construction.

## 7. Verify

Run `showboat verify` at any time to re-execute every code block and confirm the outputs still match — proving the walkthrough remains accurate as the code evolves.

To re-run all code blocks and confirm outputs still match, run:

To re-run all code blocks and confirm outputs still match, run `uvx showboat verify docs/walkthrough.md` from the project root. The verify command replays every exec block and diffs the output against what is stored here.
