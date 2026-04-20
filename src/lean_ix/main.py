"""
lean-ix – SAP LeanIX GraphQL proxy CLI

Usage
-----
    dvm-leanix [OPTIONS]

Options
-------
    --url           LeanIX workspace base URL
                    (default: https://eu-10.leanix.net/VolvoInformationTechnologyABSandbox)
    --port          Port to listen on (default: 8765)
    --connect       Chrome DevTools Protocol endpoint to connect to an existing
                    browser session (default: http://localhost:9222)
    --token         Skip browser extraction and use this Bearer token directly
    --no-save       Do not persist the token to ~/.lean-ix/tokens.json
    --ca-bundle     Path to a PEM CA bundle for SSL verification
                    (use when behind a corporate SSL inspection proxy)
    --no-verify-ssl Disable SSL certificate verification entirely (insecure)
"""

from __future__ import annotations

import argparse
import os
import ssl
import sys
from pathlib import Path

import uvicorn

DEFAULT_URL = "https://eu-10.leanix.net/VolvoInformationTechnologyABSandbox"
DEFAULT_PORT = 8765
DEFAULT_CDP = "http://localhost:9222"


def _prompt(prompt_text: str, default: str) -> str:
    """Prompt the user for a value, showing the default."""
    try:
        value = input(f"{prompt_text} [{default}]: ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return default
    return value if value else default


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="dvm-leanix",
        description="SAP LeanIX GraphQL proxy with GraphiQL UI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )

    subparsers = parser.add_subparsers(dest="command")

    from importlib.metadata import PackageNotFoundError
    from importlib.metadata import version as _pkg_version
    try:
        _version = _pkg_version("dvm-leanix")
    except PackageNotFoundError:
        _version = "dev"

    parser.add_argument(
        "--version", "-V",
        action="version",
        version=f"%(prog)s {_version}",
    )
    parser.add_argument(
        "--help-library",
        action="store_true",
        default=False,
        help="Print Markdown documentation for using lean_ix as a Python library and exit",
    )

    # ── shared arguments factory ───────────────────────────────────────
    def _add_shared(p: argparse.ArgumentParser) -> None:
        p.add_argument(
            "--url",
            metavar="URL",
            default=None,
            help=f"LeanIX workspace base URL (default: {DEFAULT_URL})",
        )
        ssl_group = p.add_mutually_exclusive_group()
        ssl_group.add_argument(
            "--ca-bundle",
            metavar="PATH",
            default=None,
            help=(
                "Path to a PEM CA bundle for SSL verification. "
                "Use when behind a corporate SSL inspection proxy. "
                "Run 'lean-ix diagnose' to auto-detect and export the right bundle."
            ),
        )
        ssl_group.add_argument(
            "--no-verify-ssl",
            action="store_true",
            default=False,
            help="Disable SSL certificate verification entirely (insecure).",
        )
        p.add_argument(
            "--legacy-ssl",
            action="store_true",
            default=True,
            help=(
                "Relax Python 3.13+ strict X.509 certificate validation. "
                "Fixes 'Missing Authority Key Identifier' errors from corporate SSL proxies. "
                "(default: enabled)"
            ),
        )
        p.add_argument(
            "--no-legacy-ssl",
            action="store_false",
            dest="legacy_ssl",
            help="Disable legacy SSL mode and use strict X.509 validation.",
        )

    # ── serve (default) ───────────────────────────────────────────────
    serve = subparsers.add_parser(
        "serve",
        help="Start the GraphQL proxy server (default when no subcommand given)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    _add_shared(serve)
    serve.add_argument(
        "--port",
        type=int,
        default=DEFAULT_PORT,
        help=f"Port for the proxy server (default: {DEFAULT_PORT})",
    )
    serve.add_argument(
        "--connect",
        metavar="CDP_URL",
        default=DEFAULT_CDP,
        dest="cdp_url",
        help=(
            "Connect to an existing browser via Chrome DevTools Protocol. "
            f"(default: {DEFAULT_CDP})"
        ),
    )
    serve.add_argument(
        "--token",
        metavar="TOKEN",
        default=None,
        help="Use this Bearer token directly (skips browser extraction)",
    )
    serve.add_argument(
        "--api-token",
        metavar="API_KEY",
        default=None,
        dest="api_key",
        help=(
            "LeanIX Technical User API key. Exchanges the key for a Bearer token "
            "via OAuth2 (no browser needed). Also reads from env var LEANIX_API_TOKEN."
        ),
    )
    serve.add_argument(
        "--no-save",
        action="store_true",
        default=False,
        help="Do not save the token to ~/.lean-ix/tokens.json",
    )

    # ── diagnose ──────────────────────────────────────────────────────
    diag = subparsers.add_parser(
        "diagnose",
        help="Test SSL/TLS connectivity to LeanIX and recommend fixes",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    _add_shared(diag)

    # ── download ──────────────────────────────────────────────────────
    dl = subparsers.add_parser(
        "download",
        help="Download all FactSheets of a type from LeanIX via the proxy",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=(
            "Introspects the GraphQL schema, builds a query with all scalar fields\n"
            "for the requested FactSheet type, paginates through all results, and\n"
            "writes them as JSON or CSV.\n\n"
            "Docs: https://help.sap.com/docs/leanix/ea/graphql-api"
        ),
    )
    _add_shared(dl)
    dl.add_argument(
        "--type", "-t",
        metavar="TYPE",
        default=None,
        dest="fs_type",
        help="FactSheet type to download (e.g. Application, ITComponent). "
             "Use --list-types to see all available types.",
    )
    dl.add_argument(
        "--subtype", "-s",
        metavar="SUBTYPE",
        nargs="+",
        default=[],
        dest="subtypes",
        help="Filter by category (subtype). Case-insensitive. "
             "Use --list-subtypes to see available values for a type.",
    )
    dl.add_argument(
        "--proxy",
        metavar="URL",
        default="http://localhost:8765/graphql",
        help="GraphQL proxy URL (default: http://localhost:8765/graphql)",
    )
    dl.add_argument(
        "--output", "-o",
        metavar="FILE",
        default=None,
        help="Write output to FILE (default: {Type}.csv)",
    )
    dl.add_argument(
        "--format", "-f",
        choices=["json", "csv"],
        default="csv",
        dest="fmt",
        help="Output format: csv (default) or json",
    )
    dl.add_argument(
        "--list-types",
        action="store_true",
        default=False,
        help="List all available FactSheet types from the schema and exit",
    )
    dl.add_argument(
        "--list-subtypes",
        action="store_true",
        default=False,
        help="List all distinct category (subtype) values for --type and exit",
    )
    dl.add_argument(
        "--relations",
        action="store_true",
        default=False,
        help=(
            "Download relationships between FactSheets instead of field data. "
            "If --type is omitted, an interactive type selector is shown. "
            "Output is CSV with columns: source_id, source_displayName, relation, "
            "target_id, target_displayName. Default file: {Type}_relations.csv"
        ),
    )
    dl.add_argument(
        "--list-relations",
        action="store_true",
        default=False,
        help="List all available relationship fields for --type and exit",
    )

    # ── backward compat: top-level flags still work (no subcommand) ───
    # Add serve flags at the top level too so existing invocations keep working
    _add_shared(parser)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT,
                        help=argparse.SUPPRESS)
    parser.add_argument("--connect", metavar="CDP_URL", default=DEFAULT_CDP,
                        dest="cdp_url", help=argparse.SUPPRESS)
    parser.add_argument("--token", metavar="TOKEN", default=None,
                        help=argparse.SUPPRESS)
    parser.add_argument("--api-token", metavar="API_KEY", default=None,
                        dest="api_key", help=argparse.SUPPRESS)
    parser.add_argument("--no-save", action="store_true", default=False,
                        help=argparse.SUPPRESS)

    return parser.parse_args(argv)


def _resolve_ssl(args: argparse.Namespace) -> bool | str | ssl.SSLContext:
    """
    Determine the ssl_verify value to pass to build_app.

    Priority:
      1. --no-verify-ssl  → False              (skip all verification)
      2. --legacy-ssl     → SSLContext          (relaxed X.509 strict mode)
      3. --ca-bundle PATH → str                 (custom CA file, optionally + legacy)
      4. default          → True or SSLContext  (system/certifi bundle)
    """
    import ssl as _ssl

    if args.no_verify_ssl:
        print("  SSL verify       : DISABLED (--no-verify-ssl)")
        return False

    legacy = getattr(args, "legacy_ssl", False)

    def _make_ctx(ca_file: str | None = None) -> _ssl.SSLContext:
        ctx = _ssl.create_default_context()
        if ca_file:
            ctx.load_verify_locations(cafile=ca_file)
        try:
            ctx.verify_flags &= ~_ssl.VERIFY_X509_STRICT
        except AttributeError:
            pass
        return ctx

    if args.ca_bundle:
        path = Path(args.ca_bundle)
        if not path.is_file():
            print(f"Error: --ca-bundle path not found: {path}", file=sys.stderr)
            sys.exit(1)
        if legacy:
            print(f"  SSL verify       : custom CA bundle + legacy mode ({path})")
            return _make_ctx(str(path))
        print(f"  SSL verify       : custom CA bundle ({path})")
        return str(path)

    if legacy:
        print("  SSL verify       : legacy mode (relaxed X.509 strict checking)")
        return _make_ctx()

    # Check env var as a convenience (mirrors requests/httpx convention)
    env_bundle = os.environ.get("REQUESTS_CA_BUNDLE") or os.environ.get("SSL_CERT_FILE")
    if env_bundle and Path(env_bundle).is_file():
        print(f"  SSL verify       : CA bundle from env ({env_bundle})")
        return env_bundle

    print("  SSL verify       : system CA bundle")
    return True


def _extract_from_browser(leanix_url: str, cdp_url: str) -> str:
    """Extract Bearer token from browser; print guidance and exit on failure."""
    print(f"  CDP endpoint     : {cdp_url}")
    print(
        "\nConnecting to browser to extract Bearer token…\n"
        "  Make sure Chrome/Edge is running with:\n"
        "    chrome.exe --remote-debugging-port=9222 --user-data-dir=C:\\Temp\\chrome-debug\n"
        "  and that you are already logged in to LeanIX.\n"
    )
    from .token import get_token_sync
    try:
        return get_token_sync(leanix_url, cdp_url)
    except RuntimeError as exc:
        print(f"\nError: {exc}", file=sys.stderr)
        sys.exit(1)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)

    if getattr(args, "help_library", False):
        from ._library_help import LIBRARY_HELP
        print(LIBRARY_HELP)
        return

    # ------------------------------------------------------------------ #
    # Route to subcommand                                                  #
    # ------------------------------------------------------------------ #
    if args.command == "diagnose":
        url = args.url or DEFAULT_URL
        from .diagnose import run_diagnostics
        run_diagnostics(url.rstrip("/"), ca_bundle=args.ca_bundle)
        return

    if args.command == "download":
        ssl_verify = _resolve_ssl(args)
        if getattr(args, "relations", False) or getattr(args, "list_relations", False):
            from .download import run_download_relations
            run_download_relations(
                proxy_url=args.proxy,
                type_name=args.fs_type,
                output_path=args.output,
                list_relations=getattr(args, "list_relations", False),
                ssl_verify=ssl_verify,
            )
        else:
            from .download import run_download
            run_download(
                proxy_url=args.proxy,
                type_name=args.fs_type,
                subtypes=args.subtypes,
                output_path=args.output,
                fmt=args.fmt,
                list_subtypes=args.list_subtypes,
                list_types=args.list_types,
                ssl_verify=ssl_verify,
            )
        return

    # Default: serve (args.command == "serve" or None for backward compat)

    # ------------------------------------------------------------------ #
    # Resolve LeanIX URL                                                   #
    # ------------------------------------------------------------------ #
    leanix_url = args.url
    if not leanix_url:
        leanix_url = _prompt("LeanIX workspace URL", DEFAULT_URL)
    leanix_url = leanix_url.rstrip("/")

    print(f"\n  LeanIX workspace : {leanix_url}")

    # ------------------------------------------------------------------ #
    # Resolve SSL verification                                             #
    # ------------------------------------------------------------------ #
    ssl_verify = _resolve_ssl(args)

    # ------------------------------------------------------------------ #
    # Obtain Bearer token                                                  #
    # ------------------------------------------------------------------ #
    from .persistence import load_token, save_token

    token: str | None = None

    # Resolve API key: CLI flag takes priority, then env var
    api_key: str | None = getattr(args, "api_key", None) or os.environ.get("LEANIX_API_TOKEN")

    if args.token:
        # Explicit Bearer token provided via CLI — use it directly
        token = args.token
        print("  Token            : provided via --token flag")

    elif api_key:
        # Technical User API key — exchange for a Bearer token via OAuth2
        print("  Token source     : Technical User API key (OAuth2)")
        from .token import get_token_from_api_key
        try:
            ssl_for_oauth: bool | str = (
                False if ssl_verify is False
                else ssl_verify if isinstance(ssl_verify, str)
                else True
            )
            token = get_token_from_api_key(api_key, leanix_url, ssl_for_oauth)
            print("  Token            : obtained via OAuth2 client-credentials")
        except RuntimeError as exc:
            print(f"\nError: {exc}", file=sys.stderr)
            sys.exit(1)

    else:
        # Try loading a previously saved token
        saved = load_token(leanix_url)
        if saved:
            print("  Token            : loaded from ~/.lean-ix/tokens.json")
            token = saved
        else:
            # No saved token — extract from browser
            token = _extract_from_browser(leanix_url, args.cdp_url)

    # Persist (unless --no-save or --token was used, where we still save)
    if not args.no_save:
        save_token(leanix_url, token)

    # ------------------------------------------------------------------ #
    # Build and start server                                               #
    # ------------------------------------------------------------------ #
    from .server import build_app

    app = build_app(leanix_url, token, cdp_url=args.cdp_url, ssl_verify=ssl_verify, api_key=api_key)

    host = "127.0.0.1"
    refresh_note = (
        f"    Token refresh    → POST http://{host}:{args.port}/token/refresh\n"
    )
    print(
        f"\n  ✓ Starting LeanIX GraphQL proxy on http://{host}:{args.port}\n"
        f"    GraphiQL UI      → http://{host}:{args.port}/graphql\n"
        f"    API endpoint     → POST http://{host}:{args.port}/graphql\n"
        f"    Upstream         → {leanix_url}/services/pathfinder/v1/graphql\n"
        + refresh_note +
        "\n  LeanIX GraphQL API docs:\n"
        "    https://help.sap.com/docs/leanix/ea/graphql-api\n"
        "\nPress Ctrl+C to stop.\n"
    )

    uvicorn.run(app, host=host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
