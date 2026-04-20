"""
SSL and connectivity diagnostics for lean-ix.

Run with:  dvm-leanix diagnose [--url URL] [--ca-bundle PATH]
"""

from __future__ import annotations

import socket
import ssl
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse


def _ok(msg: str) -> None:
    print(f"  [OK]   {msg}")


def _fail(msg: str) -> None:
    print(f"  [FAIL] {msg}")


def _info(msg: str) -> None:
    print(f"  [INFO] {msg}")


def _warn(msg: str) -> None:
    print(f"  [WARN] {msg}")


def _section(title: str) -> None:
    print(f"\n{'─' * 60}")
    print(f"  {title}")
    print(f"{'─' * 60}")


# --------------------------------------------------------------------------- #
# Individual checks                                                             #
# --------------------------------------------------------------------------- #

def check_dns(host: str) -> bool:
    _section(f"DNS resolution: {host}")
    try:
        addrs = socket.getaddrinfo(host, 443)
        ip = addrs[0][4][0]
        _ok(f"Resolved to {ip}")
        return True
    except socket.gaierror as exc:
        _fail(f"DNS resolution failed: {exc}")
        return False


def check_tcp(host: str, port: int = 443) -> bool:
    _section(f"TCP connectivity: {host}:{port}")
    try:
        with socket.create_connection((host, port), timeout=10):
            _ok(f"TCP connection to {host}:{port} succeeded")
            return True
    except OSError as exc:
        _fail(f"TCP connection failed: {exc}")
        _info("Check proxy settings or firewall rules.")
        return False


def check_ssl(host: str, port: int = 443, ca_file: str | None = None, legacy: bool = False) -> bool:
    """Try a raw TLS handshake and show the certificate chain."""
    label = f"custom CA bundle ({ca_file})" if ca_file else "system CA bundle"
    if legacy:
        label += " + legacy mode (relaxed X.509)"
    _section(f"TLS handshake with {label}")

    ctx = ssl.create_default_context()
    if ca_file:
        ctx.load_verify_locations(cafile=ca_file)
    if legacy:
        # Disable strict X.509 validation added in Python 3.13+
        # Fixes "Missing Authority Key Identifier" on non-compliant corporate certs
        try:
            ctx.verify_flags &= ~ssl.VERIFY_X509_STRICT
        except AttributeError:
            pass  # Python < 3.12 doesn't have this flag

    try:
        with socket.create_connection((host, port), timeout=10) as sock:
            with ctx.wrap_socket(sock, server_hostname=host) as ssock:
                cert = ssock.getpeercert()
                subject = dict(x[0] for x in cert.get("subject", []))
                issuer  = dict(x[0] for x in cert.get("issuer", []))
                _ok("TLS handshake succeeded")
                _info(f"Protocol : {ssock.version()}")
                _info(f"Cipher   : {ssock.cipher()[0]}")
                _info(f"Subject  : {subject.get('commonName', '?')}")
                _info(f"Issuer   : {issuer.get('commonName', '?')}")
                _info(f"Expires  : {cert.get('notAfter', '?')}")
                return True
    except ssl.SSLCertVerificationError as exc:
        _fail(f"Certificate verification failed: {exc.reason}")
        _info(f"Full error: {exc}")
        return False
    except ssl.SSLError as exc:
        _fail(f"SSL error: {exc}")
        return False
    except OSError as exc:
        _fail(f"Connection error: {exc}")
        return False


def check_ssl_no_verify(host: str, port: int = 443) -> tuple[bool, list[dict]]:
    """Connect without verification to inspect the actual certificate chain."""
    _section("TLS handshake without verification (chain inspection)")
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    try:
        with socket.create_connection((host, port), timeout=10) as sock:
            with ctx.wrap_socket(sock, server_hostname=host) as ssock:
                ssock.getpeercert(binary_form=True)
                _ok("Connected (no verification)")
                _info(f"Protocol : {ssock.version()}")
                _info(f"Cipher   : {ssock.cipher()[0]}")
                _info("Certificate served by the host (may be intercepted by proxy):")
                cert = ssock.getpeercert()
                if cert:
                    subject = dict(x[0] for x in cert.get("subject", []))
                    issuer  = dict(x[0] for x in cert.get("issuer", []))
                    _info(f"  Subject : {subject.get('commonName', '?')}")
                    _info(f"  Issuer  : {issuer.get('commonName', '?')}")
                    if subject == issuer:
                        _warn("Subject == Issuer — this looks like a self-signed or root cert!")
                return True, []
    except OSError as exc:
        _fail(f"Even without verification, connection failed: {exc}")
        return False, []


def export_windows_ca_bundle(output_path: Path) -> bool:
    """Export trusted root CAs from the Windows cert store to a PEM file."""
    _section("Exporting Windows trusted root CAs to PEM")
    ps_script = (
        "$certs = Get-ChildItem -Path Cert:\\LocalMachine\\Root; "
        "$pem = $certs | ForEach-Object { "
        "  '-----BEGIN CERTIFICATE-----'; "
        "  [Convert]::ToBase64String($_.RawData, 'InsertLineBreaks'); "
        "  '-----END CERTIFICATE-----' "
        "}; "
        f"$pem | Set-Content -Path '{output_path}' -Encoding ascii"
    )
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps_script],
            capture_output=True, text=True, timeout=30
        )
        if result.returncode != 0 or not output_path.exists():
            _fail(f"PowerShell export failed: {result.stderr.strip()}")
            return False
        size_kb = output_path.stat().st_size // 1024
        _ok(f"Exported {size_kb} KB to {output_path}")
        return True
    except Exception as exc:
        _fail(f"Export failed: {exc}")
        return False


def check_httpx(url: str, ssl_verify: bool | str = True, legacy: bool = False) -> bool:
    """Test a real HTTPS request using httpx (same library the proxy uses)."""
    label = (
        "no verification" if ssl_verify is False
        else f"custom CA ({ssl_verify})" if isinstance(ssl_verify, str)
        else "system CA bundle"
    )
    if legacy:
        label += " + legacy mode"
    _section(f"httpx request to {url} [{label}]")
    try:
        import httpx
        if legacy:
            import ssl as _ssl
            ctx = _ssl.create_default_context()
            if isinstance(ssl_verify, str):
                ctx.load_verify_locations(cafile=ssl_verify)
            try:
                ctx.verify_flags &= ~_ssl.VERIFY_X509_STRICT
            except AttributeError:
                pass
            verify: bool | str | _ssl.SSLContext = ctx
        else:
            verify = ssl_verify
        with httpx.Client(verify=verify, timeout=15.0) as client:
            resp = client.get(url)
            _ok(f"HTTP {resp.status_code} — connection works")
            return True
    except httpx.ConnectError as exc:
        _fail(f"Connection error: {exc}")
        return False
    except Exception as exc:
        _fail(f"{type(exc).__name__}: {exc}")
        return False


# --------------------------------------------------------------------------- #
# Main diagnostic runner                                                        #
# --------------------------------------------------------------------------- #

def run_diagnostics(leanix_url: str, ca_bundle: str | None = None) -> None:
    parsed = urlparse(leanix_url)
    host = parsed.hostname or leanix_url
    port = parsed.port or 443
    # Use just the host for TLS checks (the path isn't relevant for SSL)
    base_https = f"https://{host}"

    print(f"\nDiagnosing connectivity to: {leanix_url}")
    print(f"Host: {host}  Port: {port}\n")

    # 1. DNS
    if not check_dns(host):
        print("\nCannot proceed — DNS resolution failed.")
        return

    # 2. TCP
    if not check_tcp(host, port):
        print("\nCannot proceed — TCP connection failed.")
        return

    # 3. TLS without verification (always run — gives us chain info)
    check_ssl_no_verify(host, port)

    # 4. TLS with system CA bundle
    system_ok = check_ssl(host, port)

    # 5. Legacy mode — relaxes Python 3.13+ strict X.509 (fixes Missing Authority Key Identifier)
    legacy_ok = False
    if not system_ok:
        legacy_ok = check_ssl(host, port, legacy=True)

    # 6. TLS with provided CA bundle
    custom_ok = False
    if ca_bundle:
        custom_ok = check_ssl(host, port, ca_file=ca_bundle)
        if not custom_ok:
            custom_ok = check_ssl(host, port, ca_file=ca_bundle, legacy=True)

    # 7. httpx with system bundle
    httpx_system_ok = check_httpx(base_https, ssl_verify=True)

    # 8. httpx legacy mode
    httpx_legacy_ok = False
    if not httpx_system_ok:
        httpx_legacy_ok = check_httpx(base_https, ssl_verify=True, legacy=True)

    # 9. httpx with custom bundle if provided
    if ca_bundle:
        check_httpx(base_https, ssl_verify=ca_bundle)
        if not httpx_legacy_ok:
            check_httpx(base_https, ssl_verify=ca_bundle, legacy=True)

    # 10. If still failing, try Windows cert store export
    win_bundle_path: Path | None = None
    if not system_ok and not custom_ok and not legacy_ok and sys.platform == "win32":
        lean_ix_dir = Path.home() / ".lean-ix"
        lean_ix_dir.mkdir(exist_ok=True)
        win_bundle_path = lean_ix_dir / "corporate-ca.pem"
        exported = export_windows_ca_bundle(win_bundle_path)
        if exported:
            check_ssl(host, port, ca_file=str(win_bundle_path))
            check_httpx(base_https, ssl_verify=str(win_bundle_path))

    # ── Summary and recommendations ────────────────────────────────────
    _section("Summary & Recommendations")

    if system_ok or httpx_system_ok:
        _ok("SSL verification works with the system CA bundle — no extra flags needed.")
        return

    if legacy_ok or httpx_legacy_ok:
        _ok("Legacy SSL mode works!")
        _info("The corporate proxy certificate is missing the 'Authority Key Identifier'")
        _info("extension, which Python 3.13+ rejects by default.")
        print("\n  Run lean-ix with:")
        print("    dvm-leanix --legacy-ssl")
        return

    if custom_ok:
        _ok("SSL verification works with your CA bundle.")
        print(f"\n  Run lean-ix with:  --ca-bundle \"{ca_bundle}\"")
        return

    if win_bundle_path and win_bundle_path.exists():
        win_path_str = str(win_bundle_path)
        win_ctx = ssl.create_default_context()
        try:
            win_ctx.load_verify_locations(cafile=win_path_str)
            try:
                win_ctx.verify_flags &= ~ssl.VERIFY_X509_STRICT
            except AttributeError:
                pass
            with socket.create_connection((host, port), timeout=5) as s:
                with win_ctx.wrap_socket(s, server_hostname=host):
                    _ok("Windows cert store export + legacy mode worked!")
                    print("\n  Run lean-ix with:")
                    print(f"    --ca-bundle \"{win_path_str}\" --legacy-ssl")
                    return
        except Exception:
            pass

    # Nothing worked — suggest --no-verify-ssl as last resort
    _warn("Could not fix SSL with any CA bundle.")
    print("""
  Options:
  1. Ask your IT/network team for the corporate root CA PEM file, then:
       dvm-leanix --ca-bundle path\\to\\corporate-root-ca.pem

  2. Set the env var so all Python tools pick it up:
       $env:REQUESTS_CA_BUNDLE = "path\\to\\corporate-root-ca.pem"

  3. Use --no-verify-ssl as a last resort (insecure, traffic is not encrypted end-to-end):
       dvm-leanix --no-verify-ssl
""")
