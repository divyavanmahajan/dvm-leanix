"""
FastAPI-based GraphQL proxy for SAP LeanIX.

Routes
------
GET  /                – redirect to /graphql
GET  /graphql         – GraphiQL UI
POST /graphql         – proxy GraphQL request to LeanIX
GET  /health          – health check
GET  /token           – show masked current token
POST /token           – replace the current token
POST /token/refresh   – re-extract token from connected browser
"""

from __future__ import annotations

import json
import ssl as _ssl_module
import threading
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, RedirectResponse

from .graphiql import GRAPHIQL_HTML

# --------------------------------------------------------------------------- #
# App factory                                                                   #
# --------------------------------------------------------------------------- #

def build_app(
    leanix_base: str,
    initial_token: str,
    cdp_url: str | None = None,
    ssl_verify: bool | str | _ssl_module.SSLContext = True,
    api_key: str | None = None,
) -> FastAPI:
    """
    Create the FastAPI proxy application.

    Args:
        leanix_base:   LeanIX workspace base URL,
                       e.g. "https://eu-10.leanix.net/YourInstance"
        initial_token: Bearer token obtained from the browser session or OAuth2 exchange.
        cdp_url:       Chrome DevTools Protocol endpoint for auto-refresh via browser,
                       e.g. "http://localhost:9222". Pass None to disable browser refresh.
        ssl_verify:    SSL verification mode for upstream requests:
                       True            = verify using system/certifi CA bundle (default)
                       False           = disable verification entirely (insecure)
                       str             = path to a PEM CA bundle file
                       ssl.SSLContext  = pre-configured context (e.g. legacy mode)
        api_key:       LeanIX Technical User API key. When provided, token auto-refresh
                       uses OAuth2 client-credentials instead of the browser CDP.
    """
    host_part = "/".join(leanix_base.split("/")[:3])  # https://eu-10.leanix.net
    graphql_upstream = f"{host_part}/services/pathfinder/v1/graphql"

    # Thread-safe mutable token state
    _lock = threading.Lock()
    _state: dict[str, Any] = {
        "token": initial_token,
        "refreshing": False,   # prevent concurrent refresh storms
    }

    def get_token() -> str:
        with _lock:
            return _state["token"]

    def set_token(token: str) -> None:
        with _lock:
            _state["token"] = token

    # ------------------------------------------------------------------ #

    app = FastAPI(
        title="LeanIX GraphQL Proxy",
        description=(
            "Proxies GraphQL requests to SAP LeanIX using a browser-sourced Bearer token.\n\n"
            f"Upstream: `{graphql_upstream}`"
        ),
        version="0.1.0",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ------------------------------------------------------------------ #
    # Internal helpers                                                     #
    # ------------------------------------------------------------------ #

    async def _try_refresh_token() -> str | None:
        """
        Attempt to re-obtain a Bearer token after a 401 response.
        Preference order: API key OAuth2 → browser CDP → give up.
        Returns the new token on success, None on failure.
        """
        if not api_key and not cdp_url:
            return None

        with _lock:
            if _state["refreshing"]:
                return None  # another coroutine is already refreshing
            _state["refreshing"] = True

        try:
            if api_key:
                print("\n  ⚠  Token expired — refreshing via Technical User API key…")
                from .token import get_token_from_api_key
                ssl_for_refresh: bool | str = (
                    False if ssl_verify is False
                    else ssl_verify if isinstance(ssl_verify, str)
                    else True
                )
                new_token = get_token_from_api_key(api_key, leanix_base, ssl_for_refresh)
            else:
                print("\n  ⚠  Token expired — attempting auto-refresh via browser CDP…")
                from .token import extract_token
                new_token = await extract_token(leanix_base, cdp_url)  # type: ignore[arg-type]

            set_token(new_token)
            from .persistence import clear_token, save_token
            clear_token(leanix_base)
            save_token(leanix_base, new_token)
            print("  ✓  Token refreshed and saved.")
            return new_token
        except Exception as exc:
            print(f"  ✗  Auto-refresh failed: {exc}")
            return None
        finally:
            with _lock:
                _state["refreshing"] = False

    async def _forward(
        client: httpx.AsyncClient,
        body_json: dict[str, Any],
        token: str,
    ) -> httpx.Response:
        return await client.post(
            graphql_upstream,
            json=body_json,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        )

    def _make_client() -> httpx.AsyncClient:
        """Create an httpx client with the configured SSL verification."""
        if ssl_verify is False:
            return httpx.AsyncClient(timeout=60.0, verify=False)
        if isinstance(ssl_verify, str):
            return httpx.AsyncClient(timeout=60.0, verify=ssl_verify)
        if isinstance(ssl_verify, _ssl_module.SSLContext):
            return httpx.AsyncClient(timeout=60.0, verify=ssl_verify)
        return httpx.AsyncClient(timeout=60.0)

    # ------------------------------------------------------------------ #
    # Root / health                                                        #
    # ------------------------------------------------------------------ #

    @app.get("/", include_in_schema=False)
    async def root():
        return RedirectResponse(url="/graphql")

    @app.get("/health")
    async def health():
        ssl_mode = (
            "disabled (insecure)"
            if ssl_verify is False
            else ssl_verify if isinstance(ssl_verify, str)
            else "legacy mode (relaxed X.509)" if isinstance(ssl_verify, _ssl_module.SSLContext)
            else "system CA bundle"
        )
        return {
            "status": "ok",
            "upstream": graphql_upstream,
            "cdp_connected": cdp_url is not None,
            "ssl_verify": ssl_mode,
        }

    # ------------------------------------------------------------------ #
    # Token management                                                     #
    # ------------------------------------------------------------------ #

    @app.get("/token", summary="Show current token (masked)")
    async def show_token():
        tok = get_token()
        masked = tok[:8] + "…" + tok[-4:] if len(tok) > 12 else "****"
        return {"token_preview": masked, "length": len(tok)}

    @app.post("/token", summary="Replace Bearer token manually")
    async def update_token(request: Request):
        """Body: `{"token": "<value>"}`"""
        body = await request.json()
        new_tok = body.get("token", "").strip()
        if not new_tok:
            raise HTTPException(
                status_code=422,
                detail="Field 'token' is required and must not be empty.",
            )
        set_token(new_tok)
        from .persistence import save_token
        save_token(leanix_base, new_tok)
        return {"status": "updated"}

    @app.post("/token/refresh", summary="Re-extract token from connected browser")
    async def refresh_token():
        """
        Trigger a token re-extraction from the browser connected via CDP.
        Requires the proxy to have been started with `--connect`.
        """
        if not cdp_url:
            raise HTTPException(
                status_code=503,
                detail=(
                    "No browser connected. Restart lean-ix with --connect <cdp-url> "
                    "to enable auto-refresh, or POST /token with a new token manually."
                ),
            )
        new_token = await _try_refresh_token()
        if not new_token:
            raise HTTPException(
                status_code=502,
                detail=(
                    "Could not re-extract token from browser. "
                    "Ensure Chrome is running with --remote-debugging-port=9222 "
                    "and you are logged in to LeanIX."
                ),
            )
        return {"status": "refreshed"}

    # ------------------------------------------------------------------ #
    # GraphiQL UI                                                          #
    # ------------------------------------------------------------------ #

    @app.get("/graphql", response_class=HTMLResponse, include_in_schema=False)
    async def graphiql_ui():
        return HTMLResponse(content=GRAPHIQL_HTML)

    # ------------------------------------------------------------------ #
    # GraphQL proxy                                                        #
    # ------------------------------------------------------------------ #

    @app.post("/graphql", summary="Proxy a GraphQL query to LeanIX")
    async def graphql_proxy(request: Request):
        """
        Forward a GraphQL request to the LeanIX upstream.

        On `401 Unauthorized`, the proxy will automatically attempt to
        re-extract a fresh token from the connected browser (if `--connect`
        was provided) and retry the request once.
        """
        body_bytes = await request.body()
        try:
            body_json: dict[str, Any] = json.loads(body_bytes)
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=400, detail=f"Invalid JSON body: {exc}") from exc

        async with _make_client() as client:
            try:
                upstream_resp = await _forward(client, body_json, get_token())
            except httpx.RequestError as exc:
                raise HTTPException(
                    status_code=502,
                    detail=f"Could not reach LeanIX ({graphql_upstream}): {exc}",
                ) from exc

            # ── Token expired? ──────────────────────────────────────────
            if upstream_resp.status_code == 401:
                from .persistence import clear_token
                clear_token(leanix_base)

                new_token = await _try_refresh_token()

                if new_token:
                    # Retry with the fresh token
                    try:
                        upstream_resp = await _forward(client, body_json, new_token)
                    except httpx.RequestError as exc:
                        raise HTTPException(status_code=502, detail=str(exc)) from exc
                else:
                    # Return a helpful 401 with actionable message
                    hint = (
                        "POST /token/refresh to re-extract from browser, "
                        "or POST /token with {\"token\": \"<new_token>\"}"
                        if cdp_url
                        else "POST /token with {\"token\": \"<new_token>\"} to update the token"
                    )
                    return Response(
                        content=json.dumps({
                            "errors": [{
                                "message": "LeanIX token expired or invalid.",
                                "extensions": {
                                    "code": "TOKEN_EXPIRED",
                                    "hint": hint,
                                },
                            }]
                        }),
                        status_code=401,
                        media_type="application/json",
                    )

        content_type = upstream_resp.headers.get("content-type", "application/json")
        return Response(
            content=upstream_resp.content,
            status_code=upstream_resp.status_code,
            media_type=content_type,
        )

    return app
