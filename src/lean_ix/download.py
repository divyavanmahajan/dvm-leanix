"""
Download all FactSheets of a given type from LeanIX via the local GraphQL proxy.

Usage
-----
    uv run lean-ix download --type Application
    uv run lean-ix download --type Application --subtype "Business Application"
    uv run lean-ix download --type Application --subtype "Business Application" "Platform"
    uv run lean-ix download --type Application --output apps.json
    uv run lean-ix download --type Application --format csv --output apps.csv
    uv run lean-ix download --type Application --list-subtypes
    uv run lean-ix download --relations
    uv run lean-ix download --type Application --relations
    uv run lean-ix download --type Application --list-relations

How it works
------------
1. Introspects the GraphQL schema to discover all scalar + enum fields on the
   requested FactSheet type (e.g. Application).
2. Builds a paginated query using those fields inside an inline fragment
   (``... on Application { ... }``), plus the common BaseFactSheet fields.
3. Pages through all results (100 per page) using cursor-based pagination.
4. Optionally filters the results by ``category`` (subtype) client-side.
5. Writes the collected records to stdout or a file as JSON or CSV.

Relationship download (--relations)
------------------------------------
When --relations is used, the command downloads edges between FactSheets rather
than FactSheet field data. Output columns:
  source_id, source_displayName, relation, target_id, target_displayName
"""

from __future__ import annotations

import csv
import io
import json
import sys
from typing import Any, Optional

import httpx


# ── Safe minimal base fields (always exist on every FactSheet) ──────────────
# Extended base fields are added dynamically from BaseFactSheet introspection.

_SAFE_BASE_FIELDS = ["id", "name", "type", "category", "displayName",
                     "description", "fullName", "tags", "level", "status",
                     "updatedAt", "createdAt"]

# Fields that require sub-selection (not plain scalars in BASE_FIELDS)
_BASE_SUBSELECT = {
    "tags": "tags { name }",
    "completion": "completion { completion }",
    "businessCapabilities": None,   # connection — skip
    "userSubscriptions": None,
    "subscriptions": None,
    "documents": None,
    "comments": None,
    "watches": None,
    "permissions": None,
}

# Scalar GraphQL kinds whose fields we include in the dynamic query
_SCALAR_KINDS = {"SCALAR", "ENUM"}

# Fields to always skip (connections, interfaces, internal)
_SKIP_FIELDS = {
    "subscriptions", "comments", "watches", "documents",
    "qualitySeal", "permissions",
}


# ── Introspection ────────────────────────────────────────────────────────────

_INTROSPECTION_QUERY = """
query IntrospectType($name: String!) {
  __type(name: $name) {
    name
    fields {
      name
      type {
        kind
        name
        ofType {
          kind
          name
          ofType {
            kind
            name
          }
        }
      }
    }
  }
}
"""

_ALL_FACTSHEET_TYPES_QUERY = """
{
  __type(name: "FactSheetType") {
    enumValues { name }
  }
}
"""


def _leaf_kind(type_ref: dict) -> tuple[str, str]:
    """Recursively unwrap NON_NULL / LIST wrappers and return (kind, name)."""
    kind = type_ref.get("kind", "")
    name = type_ref.get("name") or ""
    if kind in ("NON_NULL", "LIST"):
        inner = type_ref.get("ofType") or {}
        return _leaf_kind(inner)
    return kind, name


def introspect_type(proxy_url: str, type_name: str, ssl_verify: Any = True) -> list[dict]:
    """
    Return the list of field descriptors for *type_name* from the GraphQL schema.
    Each descriptor is {"name": str, "kind": str, "type_name": str}.
    """
    body = _gql(proxy_url, _INTROSPECTION_QUERY, {"name": type_name}, ssl_verify)
    _check_errors(body)
    data = body.get("data") or {}
    type_info = data.get("__type") or {}
    fields = type_info.get("fields") or []
    result = []
    for f in fields:
        kind, tname = _leaf_kind(f["type"])
        result.append({"name": f["name"], "kind": kind, "type_name": tname})
    return result


def list_factsheet_types(proxy_url: str, ssl_verify: Any = True) -> list[str]:
    """Return all FactSheetType enum values from the schema."""
    body = _gql(proxy_url, _ALL_FACTSHEET_TYPES_QUERY, {}, ssl_verify)
    _check_errors(body)
    data = body.get("data") or {}
    type_info = data.get("__type") or {}
    return [v["name"] for v in (type_info.get("enumValues") or [])]


# ── Query building ────────────────────────────────────────────────────────────

def build_query(type_name: str, type_fields: list[dict], base_fields: list[str]) -> str:
    """
    Build a paginated allFactSheets query that fetches:
    - Validated BaseFactSheet scalar fields (introspected at runtime)
    - All scalar / enum fields specific to *type_name* via an inline fragment
    """
    # Build base field selection lines
    base_lines: list[str] = []
    for f in base_fields:
        if f in _BASE_SUBSELECT:
            sel = _BASE_SUBSELECT[f]
            if sel:
                base_lines.append(sel)
        else:
            base_lines.append(f)

    # Collect all base field names to avoid duplication in the fragment
    base_names = set(base_fields) | {"id", "name", "type", "category"}

    specific_fields = [
        f["name"]
        for f in type_fields
        if f["kind"] in _SCALAR_KINDS
        and f["name"] not in _SKIP_FIELDS
        and f["name"] not in base_names
    ]

    fragment = ""
    if specific_fields:
        lines = "\n          ".join(specific_fields)
        fragment = f"""
        ... on {type_name} {{
          {lines}
        }}"""

    base_selection = "\n        ".join(base_lines)

    return f"""
query DownloadFactSheets($factSheetType: FactSheetType, $after: String) {{
  allFactSheets(factSheetType: $factSheetType, first: 100, after: $after) {{
    totalCount
    pageInfo {{
      hasNextPage
      endCursor
    }}
    edges {{
      node {{
        {base_selection}
        {fragment.strip()}
      }}
    }}
  }}
}}
"""


# ── HTTP helpers ─────────────────────────────────────────────────────────────

def _make_ssl_ctx(ssl_verify: Any) -> Any:
    """Convert a bool/str/SSLContext to something httpx accepts."""
    import ssl as _ssl
    if isinstance(ssl_verify, _ssl.SSLContext):
        return ssl_verify
    return ssl_verify


def _gql(
    proxy_url: str,
    query: str,
    variables: dict,
    ssl_verify: Any = True,
) -> dict:
    """Execute a single GraphQL request and return the parsed response dict."""
    verify = _make_ssl_ctx(ssl_verify)
    with httpx.Client(verify=verify, timeout=60.0) as client:
        resp = client.post(
            proxy_url,
            json={"query": query, "variables": variables},
            headers={"Content-Type": "application/json", "Accept": "application/json"},
        )
    if resp.status_code == 401:
        raise RuntimeError(
            "401 Unauthorized — token expired.\n"
            "Restart lean-ix to refresh the token, or POST a new token to /token."
        )
    if resp.status_code != 200:
        raise RuntimeError(
            f"GraphQL request failed: HTTP {resp.status_code}\n{resp.text[:500]}"
        )
    return resp.json()


def _extract_permission_denied_fields(errors: list[dict]) -> set[str]:
    """
    Parse LeanIX permission error messages of the form:
      "No permission: fact_sheet_fields:read:application:FIELD_NAME"
    and return the set of denied field names.
    """
    denied: set[str] = set()
    for e in errors:
        msg = e.get("message", "")
        if msg.startswith("No permission: fact_sheet_fields:read:"):
            # Last colon-separated segment is the field name
            field = msg.rsplit(":", 1)[-1].strip()
            if field:
                denied.add(field)
    return denied


def _check_errors(body: dict) -> None:
    """Raise RuntimeError for non-permission errors. Permission errors are handled separately."""
    errors = body.get("errors") or []
    non_permission = [
        e for e in errors
        if not e.get("message", "").startswith("No permission: fact_sheet_fields")
    ]
    if non_permission:
        msgs = "; ".join(e.get("message", str(e)) for e in non_permission)
        raise RuntimeError(f"GraphQL errors: {msgs}")


# ── Pagination ────────────────────────────────────────────────────────────────

def fetch_all(
    proxy_url: str,
    query: str,
    type_name: str,
    subtypes: list[str],
    ssl_verify: Any = True,
    verbose: bool = True,
    type_fields: list[dict] | None = None,
    base_fields: list[str] | None = None,
) -> list[dict]:
    """
    Page through allFactSheets and return a flat list of node dicts.
    Filters by ``category`` (case-insensitive) if *subtypes* is non-empty.
    On permission errors, auto-excludes the denied fields and retries.
    """
    records: list[dict] = []
    cursor: Optional[str] = None
    total: Optional[int] = None
    page = 0
    excluded_fields: set[str] = set()

    subtypes_lc = [s.lower() for s in subtypes]

    while True:
        page += 1
        variables: dict[str, Any] = {"factSheetType": type_name}
        if cursor:
            variables["after"] = cursor

        body = _gql(proxy_url, query, variables, ssl_verify)

        # Handle permission errors — rebuild query without denied fields and restart
        if "errors" in body:
            denied = _extract_permission_denied_fields(body.get("errors", []))
            new_denied = denied - excluded_fields
            if new_denied and type_fields is not None and base_fields is not None:
                excluded_fields |= new_denied
                if verbose:
                    print(f"\n  Permission denied for fields: {', '.join(sorted(new_denied))}")
                    print(f"  Rebuilding query without {len(excluded_fields)} excluded field(s)…")
                # Rebuild with excluded fields added to skip set
                filtered_fields = [f for f in type_fields if f["name"] not in excluded_fields]
                query = build_query(type_name, filtered_fields, base_fields)
                # Restart from scratch
                records = []
                cursor = None
                total = None
                page = 0
                continue
            # Non-permission errors
            _check_errors(body)

        conn = (body.get("data") or {}).get("allFactSheets") or {}

        if total is None:
            total = conn.get("totalCount", "?")
            if verbose:
                print(f"  Total fact sheets reported: {total}")

        edges = conn.get("edges") or []
        for edge in edges:
            node = edge.get("node") or {}
            if subtypes_lc:
                cat = (node.get("category") or "").lower()
                if cat not in subtypes_lc:
                    continue
            records.append(node)

        page_info = conn.get("pageInfo") or {}

        if verbose:
            print(f"  Page {page}: {len(records)} matching records fetched so far…", end="\r")

        if not page_info.get("hasNextPage"):
            break
        cursor = page_info.get("endCursor")
        if not cursor:
            break

    if verbose:
        print(f"  Done. {len(records)} records collected.                    ")

    return records


# ── Output formatters ────────────────────────────────────────────────────────

def _flatten(node: dict, prefix: str = "") -> dict[str, Any]:
    """Flatten a nested dict one level deep (enough for most FactSheet fields)."""
    out: dict[str, Any] = {}
    for k, v in node.items():
        key = f"{prefix}{k}" if prefix else k
        if isinstance(v, dict):
            for k2, v2 in v.items():
                out[f"{key}.{k2}"] = v2
        elif isinstance(v, list):
            # Convert lists to semicolon-separated strings
            parts = []
            for item in v:
                if isinstance(item, dict):
                    parts.append("; ".join(str(vv) for vv in item.values()))
                else:
                    parts.append(str(item))
            out[key] = " | ".join(parts)
        else:
            out[key] = v
    return out


def write_json(records: list[dict], dest: io.TextIOBase) -> None:
    json.dump(records, dest, indent=2, ensure_ascii=False, default=str)
    dest.write("\n")


def write_csv(records: list[dict], dest: io.TextIOBase) -> None:
    if not records:
        return
    flat = [_flatten(r) for r in records]
    # Union of all keys, id/name first
    all_keys: list[str] = []
    seen: set[str] = set()
    for priority in ["id", "name", "type", "category", "displayName", "status"]:
        if any(priority in r for r in flat):
            all_keys.append(priority)
            seen.add(priority)
    for r in flat:
        for k in r:
            if k not in seen:
                all_keys.append(k)
                seen.add(k)

    writer = csv.DictWriter(
        dest, fieldnames=all_keys, extrasaction="ignore", lineterminator="\n"
    )
    writer.writeheader()
    writer.writerows(flat)


# ── Relationship helpers ──────────────────────────────────────────────────────

def list_relation_fields(type_fields: list[dict]) -> list[dict]:
    """Return fields that represent FactSheet relationships.

    Relationship fields have names starting with ``rel`` and resolve to an
    OBJECT type (a connection) after unwrapping NON_NULL / LIST wrappers.
    """
    return [
        f for f in type_fields
        if f["name"].startswith("rel") and f["kind"] == "OBJECT"
    ]


def build_relations_query(type_name: str, relation_fields: list[dict]) -> str:
    """Build a paginated allFactSheets query that fetches id/displayName of the
    source FactSheet and id/displayName of every related FactSheet for each
    supplied relationship field."""
    rel_lines = []
    for f in relation_fields:
        rel_lines.append(
            f"{f['name']} {{\n"
            f"              edges {{\n"
            f"                node {{\n"
            f"                  factSheet {{ id displayName }}\n"
            f"                }}\n"
            f"              }}\n"
            f"            }}"
        )
    fragment_body = "\n          ".join(rel_lines)

    return f"""
query DownloadRelations($factSheetType: FactSheetType, $after: String) {{
  allFactSheets(factSheetType: $factSheetType, first: 100, after: $after) {{
    totalCount
    pageInfo {{
      hasNextPage
      endCursor
    }}
    edges {{
      node {{
        id
        displayName
        ... on {type_name} {{
          {fragment_body}
        }}
      }}
    }}
  }}
}}
"""


def fetch_all_relations(
    proxy_url: str,
    query: str,
    type_name: str,
    relation_fields: list[dict],
    ssl_verify: Any = True,
    verbose: bool = True,
) -> list[dict]:
    """Paginate through allFactSheets and return a flat list of relation rows.

    Each row is::

        {
            "source_id": str,
            "source_displayName": str,
            "relation": str,       # field name, e.g. relApplicationToProcess
            "target_id": str,
            "target_displayName": str,
        }

    Permission-denied relation fields are auto-excluded and pagination restarts,
    matching the behaviour of :func:`fetch_all`.
    """
    rows: list[dict] = []
    cursor: Optional[str] = None
    total: Optional[int] = None
    page = 0
    excluded_fields: set[str] = set()
    active_fields = list(relation_fields)

    while True:
        page += 1
        variables: dict[str, Any] = {"factSheetType": type_name}
        if cursor:
            variables["after"] = cursor

        body = _gql(proxy_url, query, variables, ssl_verify)

        # Auto-exclude permission-denied relation fields and restart
        if "errors" in body:
            denied = _extract_permission_denied_fields(body.get("errors", []))
            new_denied = denied - excluded_fields
            if new_denied:
                excluded_fields |= new_denied
                if verbose:
                    print(f"\n  Permission denied for relations: {', '.join(sorted(new_denied))}")
                    print(f"  Rebuilding query without {len(excluded_fields)} excluded field(s)…")
                active_fields = [f for f in relation_fields if f["name"] not in excluded_fields]
                if not active_fields:
                    print("  No accessible relation fields remain.", file=sys.stderr)
                    break
                query = build_relations_query(type_name, active_fields)
                rows = []
                cursor = None
                total = None
                page = 0
                continue
            _check_errors(body)

        conn = (body.get("data") or {}).get("allFactSheets") or {}

        if total is None:
            total = conn.get("totalCount", "?")
            if verbose:
                print(f"  Total fact sheets: {total}")

        for edge in (conn.get("edges") or []):
            node = edge.get("node") or {}
            source_id = node.get("id", "")
            source_display = node.get("displayName") or node.get("name") or ""

            for rel_field in active_fields:
                rel_name = rel_field["name"]
                rel_conn = node.get(rel_name) or {}
                for rel_edge in (rel_conn.get("edges") or []):
                    rel_node = rel_edge.get("node") or {}
                    target_fs = rel_node.get("factSheet") or {}
                    target_id = target_fs.get("id", "")
                    target_display = target_fs.get("displayName", "")
                    if target_id:
                        rows.append({
                            "source_id": source_id,
                            "source_displayName": source_display,
                            "relation": rel_name,
                            "target_id": target_id,
                            "target_displayName": target_display,
                        })

        page_info = conn.get("pageInfo") or {}

        if verbose:
            print(f"  Page {page}: {len(rows)} relation rows so far…", end="\r")

        if not page_info.get("hasNextPage"):
            break
        cursor = page_info.get("endCursor")
        if not cursor:
            break

    if verbose:
        print(f"  Done. {len(rows)} relation rows collected.                    ")

    return rows


def write_relations_csv(rows: list[dict], dest: io.TextIOBase) -> None:
    """Write relation rows as CSV with fixed columns."""
    if not rows:
        return
    fieldnames = ["source_id", "source_displayName", "relation", "target_id", "target_displayName"]
    writer = csv.DictWriter(dest, fieldnames=fieldnames, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)


def _prompt_choice(prompt_text: str, options: list[str]) -> list[str]:
    """Display a numbered list and return selected item(s).

    Enter a single number, a comma-separated list, or press Enter to select all.
    """
    for i, opt in enumerate(options, 1):
        print(f"  {i:3}. {opt}")
    print()
    try:
        raw = input(f"{prompt_text} (number(s), comma-separated, or Enter for all): ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        sys.exit(1)

    if not raw:
        return options

    selected = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            idx = int(part) - 1
            if 0 <= idx < len(options):
                selected.append(options[idx])
            else:
                print(f"  Warning: {part} out of range, skipped.", file=sys.stderr)
        except ValueError:
            print(f"  Warning: '{part}' is not a number, skipped.", file=sys.stderr)

    return selected or options


def run_download_relations(
    proxy_url: str,
    type_name: Optional[str],
    output_path: Optional[str],
    list_relations: bool,
    ssl_verify: Any = True,
) -> None:
    """Orchestrate a relationship download: select type → select relations → paginate → write CSV."""

    # ── Interactive type selection if --type not given ──────────────────────
    if not type_name:
        print("Fetching FactSheet types from schema…")
        try:
            types = sorted(list_factsheet_types(proxy_url, ssl_verify))
        except RuntimeError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            sys.exit(1)

        print("\nAvailable FactSheet types:")
        chosen = _prompt_choice("Select type", types)
        if len(chosen) != 1:
            print("Please select exactly one type.", file=sys.stderr)
            sys.exit(1)
        type_name = chosen[0]
        print(f"\n  FactSheet type   : {type_name}")

    print(f"\n  Proxy            : {proxy_url}")
    print(f"  FactSheet type   : {type_name}")
    print()

    # ── Introspect to find relation fields ──────────────────────────────────
    print(f"Introspecting schema for '{type_name}'…")
    try:
        type_fields = introspect_type(proxy_url, type_name, ssl_verify)
    except RuntimeError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    if not type_fields:
        print(
            f"Error: type '{type_name}' not found in schema. "
            "Use --list-types to see valid types.",
            file=sys.stderr,
        )
        sys.exit(1)

    all_relation_fields = list_relation_fields(type_fields)

    if not all_relation_fields:
        print(f"No relationship fields found for type '{type_name}'.")
        return

    print(f"  Found {len(all_relation_fields)} relationship field(s)")

    # ── --list-relations: print and exit ────────────────────────────────────
    if list_relations:
        print(f"\nRelationship fields for {type_name}:")
        for f in all_relation_fields:
            print(f"  {f['name']}")
        return

    # ── Interactive relation selection ───────────────────────────────────────
    rel_names = [f["name"] for f in all_relation_fields]
    print("\nAvailable relationships:")
    chosen_names = _prompt_choice("Select relationships", rel_names)
    relation_fields = [f for f in all_relation_fields if f["name"] in chosen_names]
    print(f"\n  Downloading {len(relation_fields)} relationship(s)…\n")

    # ── Build query and paginate ─────────────────────────────────────────────
    query = build_relations_query(type_name, relation_fields)
    try:
        rows = fetch_all_relations(
            proxy_url, query, type_name, relation_fields, ssl_verify, verbose=True
        )
    except RuntimeError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    if not rows:
        print("No relation rows found.")
        return

    # ── Write output ─────────────────────────────────────────────────────────
    if not output_path:
        output_path = f"{type_name}_relations.csv"

    with open(output_path, "w", encoding="utf-8", newline="") as fh:
        write_relations_csv(rows, fh)

    print(f"\n  Written {len(rows)} relation rows as CSV to {output_path}")


# ── Main entry ────────────────────────────────────────────────────────────────

def run_download(
    proxy_url: str,
    type_name: str,
    subtypes: list[str],
    output_path: Optional[str],
    fmt: str,
    list_subtypes: bool,
    list_types: bool,
    ssl_verify: Any = True,
) -> None:
    # ── list available FactSheet types ──────────────────────────────────────
    if list_types:
        print("Fetching FactSheet types from schema…")
        types = list_factsheet_types(proxy_url, ssl_verify)
        print("\nAvailable FactSheet types:")
        for t in sorted(types):
            print(f"  {t}")
        return

    if not type_name:
        print(
            "Error: --type is required. Use --list-types to see available FactSheet types.",
            file=sys.stderr,
        )
        sys.exit(1)

    print(f"\n  Proxy            : {proxy_url}")
    print(f"  FactSheet type   : {type_name}")
    if subtypes:
        print(f"  Subtype filter   : {', '.join(subtypes)}")
    print()

    # ── Introspect schema ───────────────────────────────────────────────────
    print(f"Introspecting schema for type '{type_name}'…")
    try:
        type_fields = introspect_type(proxy_url, type_name, ssl_verify)
        base_fs_fields = introspect_type(proxy_url, "BaseFactSheet", ssl_verify)
    except RuntimeError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    if not type_fields:
        print(
            f"Error: type '{type_name}' not found in schema. "
            "Use --list-types to see valid types.",
            file=sys.stderr,
        )
        sys.exit(1)

    scalar_count = sum(1 for f in type_fields if f["kind"] in _SCALAR_KINDS)
    print(f"  Found {len(type_fields)} fields ({scalar_count} scalar/enum) on {type_name}")

    # Build validated base field list from actual BaseFactSheet schema
    base_field_names_in_schema = {f["name"] for f in base_fs_fields}
    base_fields = [
        f for f in _SAFE_BASE_FIELDS
        if f in base_field_names_in_schema or f in _BASE_SUBSELECT
    ]
    # Always include completion if it exists
    if "completion" in base_field_names_in_schema:
        base_fields.append("completion")
    print(f"  Base fields validated: {len(base_fields)} of {len(_SAFE_BASE_FIELDS)+1} available")

    # ── List subtypes ───────────────────────────────────────────────────────
    if list_subtypes:
        print(f"\nFetching all category values for {type_name}…")
        minimal_q = """
        query($factSheetType: FactSheetType, $after: String) {
          allFactSheets(factSheetType: $factSheetType, first: 100, after: $after) {
            totalCount
            pageInfo { hasNextPage endCursor }
            edges { node { category } }
          }
        }"""
        try:
            records = fetch_all(
                proxy_url, minimal_q, type_name, [], ssl_verify, verbose=True
            )
        except RuntimeError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            sys.exit(1)
        cats = sorted({r.get("category") or "(none)" for r in records})
        print(f"\nFound {len(cats)} distinct category (subtype) values:")
        for c in cats:
            print(f"  {c}")
        return

    # ── Build and run query ─────────────────────────────────────────────────
    print("Building query from schema…")
    query = build_query(type_name, type_fields, base_fields)

    print(f"Downloading fact sheets…")
    try:
        records = fetch_all(
            proxy_url, query, type_name, subtypes, ssl_verify,
            verbose=True, type_fields=type_fields, base_fields=base_fields,
        )
    except RuntimeError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    if not records:
        print("No records found matching the given filters.")
        return

    # ── Write output ────────────────────────────────────────────────────────
    if not output_path:
        ext = "csv" if fmt == "csv" else "json"
        output_path = f"{type_name}.{ext}"

    with open(output_path, "w", encoding="utf-8", newline="") as dest_file:
        if fmt == "csv":
            write_csv(records, dest_file)
        else:
            write_json(records, dest_file)

    print(f"\n  Written {len(records)} records as {fmt.upper()} to {output_path}")
