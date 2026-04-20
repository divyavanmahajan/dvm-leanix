"""
Extract a Bearer token from a logged-in LeanIX browser session via Playwright CDP.

Usage:
    1. Launch Chrome/Edge with remote debugging enabled:
       chrome.exe --remote-debugging-port=9222 --user-data-dir=C:\\Temp\\chrome-debug
    2. Log in to LeanIX in that browser window.
    3. Run this module to extract the active access token.
"""

from __future__ import annotations

import asyncio
import re

import httpx
from playwright.async_api import Browser, BrowserContext, async_playwright

CDP_URL = "http://localhost:9222"
_TOKEN_PATTERN = re.compile(r"^Bearer (.+)$")


async def _find_token_in_storage(context: BrowserContext, leanix_base: str) -> str | None:
    """Try to extract the token from localStorage/sessionStorage on a LeanIX page."""
    for page in context.pages:
        if leanix_base.split("/")[2] not in page.url:  # check hostname
            continue
        for key in ("access_token", "token", "id_token", "leanix_token"):
            val = await page.evaluate(f"localStorage.getItem('{key}')")
            if val:
                return val
            val = await page.evaluate(f"sessionStorage.getItem('{key}')")
            if val:
                return val
    return None


async def _intercept_token(
    context: BrowserContext,
    leanix_host: str,
    timeout: float = 30.0,
) -> str | None:
    """
    Navigate to the LeanIX workspace on an existing page and intercept the
    Authorization header from the first matching outbound request.
    """
    captured: list[str] = []

    def on_request(request):
        if leanix_host in request.url:
            auth = request.headers.get("authorization", "")
            m = _TOKEN_PATTERN.match(auth)
            if m:
                captured.append(m.group(1))

    page = context.pages[0] if context.pages else await context.new_page()
    page.on("request", on_request)

    # Trigger a lightweight request by navigating or reloading
    try:
        await page.reload(timeout=timeout * 1000)
    except Exception:
        pass

    page.remove_listener("request", on_request)

    return captured[0] if captured else None


async def _wait_for_token(
    context: BrowserContext,
    leanix_host: str,
    leanix_base: str,
    timeout: float = 60.0,
) -> str:
    """
    Wait for the user to perform any navigation/interaction that triggers a
    LeanIX API call, then capture the Bearer token from that request.
    """
    captured: asyncio.Queue[str] = asyncio.Queue(maxsize=1)

    def on_request(request):
        if leanix_host in request.url and not captured.full():
            auth = request.headers.get("authorization", "")
            m = _TOKEN_PATTERN.match(auth)
            if m:
                captured.put_nowait(m.group(1))

    # Listen across all existing pages
    for page in context.pages:
        page.on("request", on_request)

    # Also open a new page pointed at LeanIX if none exists there
    leanix_pages = [p for p in context.pages if leanix_host in p.url]
    if not leanix_pages:
        page = await context.new_page()
        page.on("request", on_request)
        print(f"  → Opening {leanix_base} in the connected browser…")
        try:
            await page.goto(leanix_base, timeout=30_000)
        except Exception:
            pass
    else:
        page = leanix_pages[0]
        print(f"  → Found LeanIX page ({page.url}). Reloading to trigger API calls…")
        try:
            await page.reload(timeout=30_000)
        except Exception:
            pass

    try:
        token = await asyncio.wait_for(captured.get(), timeout=timeout)
    except asyncio.TimeoutError:
        raise RuntimeError(
            "Timed out waiting for a Bearer token from the browser.\n"
            "Make sure you are logged in to LeanIX in the connected browser."
        )
    finally:
        for p in context.pages:
            p.remove_listener("request", on_request)

    return token


async def extract_token(leanix_base: str, cdp_url: str = CDP_URL) -> str:
    """
    Connect to a running browser via CDP and extract a LeanIX Bearer token.

    Args:
        leanix_base: Base URL of the LeanIX workspace,
                     e.g. "https://eu-10.leanix.net/VolvoInformationTechnologyABSandbox"
        cdp_url:     Chrome DevTools Protocol endpoint, default "http://localhost:9222"

    Returns:
        The raw Bearer token string.

    Raises:
        RuntimeError: If connection fails or token cannot be found.
    """
    leanix_host = leanix_base.split("/")[2]  # e.g. "eu-10.leanix.net"

    print(f"Connecting to browser at {cdp_url} …")
    async with async_playwright() as pw:
        try:
            browser: Browser = await pw.chromium.connect_over_cdp(cdp_url)
        except Exception as exc:
            raise RuntimeError(
                f"Could not connect to browser at {cdp_url}.\n"
                "\n"
                "IMPORTANT: If Edge/Chrome is already running, launching it again with\n"
                "--remote-debugging-port is silently ignored — the flag only works on a\n"
                "fresh browser process. Use a separate --user-data-dir to force a new\n"
                "isolated instance that coexists with your normal browser session:\n"
                "\n"
                '  Start-Process "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe" `\n'
                '    "--remote-debugging-port=9222 --user-data-dir=C:\\Temp\\edge-debug `\n'
                '     --no-first-run --no-default-browser-check"\n'
                "\n"
                "Then log in to LeanIX in that new window and re-run lean-ix.\n"
                "\n"
                "To verify the debug port is active before retrying:\n"
                "  Invoke-RestMethod http://localhost:9222/json/version\n"
                "\n"
                f"Original error: {exc}"
            ) from exc

        contexts = browser.contexts
        if not contexts:
            raise RuntimeError("No browser contexts found. Make sure the browser has pages open.")

        context = contexts[0]
        print(f"Connected. Found {len(context.pages)} open tab(s).")

        # Try fast path: look in storage first
        token = await _find_token_in_storage(context, leanix_base)
        if token:
            print("  ✓ Token found in browser storage.")
            return token

        # Slow path: watch network requests
        print("  Token not in storage — watching network requests…")
        token = await _wait_for_token(context, leanix_host, leanix_base)
        print("  ✓ Token captured from network request.")
        return token


def get_token_sync(leanix_base: str, cdp_url: str = CDP_URL) -> str:
    """Synchronous wrapper around extract_token."""
    return asyncio.run(extract_token(leanix_base, cdp_url))


def get_token_from_api_key(
    api_key: str,
    leanix_base: str,
    ssl_verify: bool | str = True,
) -> str:
    """
    Exchange a LeanIX Technical User API key for a Bearer access token.

    The API key is the secret generated in the LeanIX administration area under
    Technical Users.  It is exchanged via OAuth2 client-credentials grant at:
      https://{host}/services/mtm/v1/oauth2/token

    Args:
        api_key:      The LeanIX API key (Technical User secret).
        leanix_base:  LeanIX workspace URL, e.g. "https://eu-10.leanix.net/MyWS".
        ssl_verify:   SSL verification — True (default), False, or path to PEM bundle.

    Returns:
        The raw Bearer access token string.

    Raises:
        RuntimeError: If the OAuth2 exchange fails.
    """
    host = "/".join(leanix_base.split("/")[:3])  # https://eu-10.leanix.net
    oauth_url = f"{host}/services/mtm/v1/oauth2/token"

    try:
        response = httpx.post(
            oauth_url,
            auth=("apitoken", api_key),
            data={"grant_type": "client_credentials"},
            verify=ssl_verify,
            timeout=30.0,
        )
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise RuntimeError(
            f"OAuth2 token exchange failed ({exc.response.status_code}): "
            f"{exc.response.text}"
        ) from exc
    except Exception as exc:
        raise RuntimeError(f"OAuth2 token exchange error: {exc}") from exc

    token = response.json().get("access_token")
    if not token:
        raise RuntimeError(
            f"OAuth2 response did not contain 'access_token'. Response: {response.text}"
        )
    return token
