"""
Markdown help text explaining how to use lean_ix.download as a Python library.
Printed by ``lean-ix --help-library``.
"""

LIBRARY_HELP = """
# Using dvm-leanix as a Python Library

The `lean_ix.download` module can be imported directly into your own Python scripts
to download FactSheets and relationships without going through the CLI.

## Prerequisites

The **lean-ix proxy must be running** before you call any of these functions:

```powershell
dvm-leanix serve
# or with legacy SSL (default):
dvm-leanix serve --legacy-ssl
```

The proxy listens on `http://localhost:8765/graphql` by default.

---

## Installation

```powershell
pip install dvm-leanix
# or
uv add dvm-leanix
```

---

## Downloading FactSheets

### Simplest usage — `run_download()`

```python
from lean_ix.download import run_download

run_download(
    proxy_url="http://localhost:8765/graphql",
    type_name="Application",
    subtypes=[],            # [] = all subtypes
    output_path="apps.json", # None = auto-generate {Type}.json
    fmt="json",              # "json" (default) or "csv"
    list_subtypes=False,
    list_types=False,
    limit=None,             # int to stop early, e.g. limit=10 for testing
)
```

### Filtering by subtype

```python
run_download(
    proxy_url="http://localhost:8765/graphql",
    type_name="Application",
    subtypes=["Business_Application", "Platform"],
    output_path="business_apps.json",
    fmt="json",
    list_subtypes=False,
    list_types=False,
)
```

### Limiting the number of records (for testing)

```python
run_download(
    proxy_url="http://localhost:8765/graphql",
    type_name="Application",
    subtypes=[],
    output_path="sample.json",
    fmt="json",
    list_subtypes=False,
    list_types=False,
    limit=10,   # stop after 10 records — avoids fetching the full dataset
)
```

### Working with results in memory

Use the lower-level functions to get records as Python objects rather than
writing straight to a file:

```python
import ssl
from lean_ix.download import (
    introspect_type,
    list_relation_fields,
    build_query,
    fetch_all,
)

PROXY = "http://localhost:8765/graphql"

# Optional: legacy SSL (needed on corporate networks with SSL inspection)
ctx = ssl.create_default_context()
ctx.verify_flags &= ~ssl.VERIFY_X509_STRICT
ssl_verify = ctx   # pass True for default, False to skip entirely

# 1. Introspect the schema
type_fields = introspect_type(PROXY, "Application", ssl_verify)
base_fields_raw = introspect_type(PROXY, "BaseFactSheet", ssl_verify)
base_field_names = {f["name"] for f in base_fields_raw}

# 2. Build a validated field list and query
from lean_ix.download import _SAFE_BASE_FIELDS, _BASE_SUBSELECT
base_fields = [f for f in _SAFE_BASE_FIELDS if f in base_field_names or f in _BASE_SUBSELECT]
if "completion" in base_field_names:
    base_fields.append("completion")

query = build_query("Application", type_fields, base_fields)

# 3. Fetch all records (returns list of dicts)
records = fetch_all(
    proxy_url=PROXY,
    query=query,
    type_name="Application",
    subtypes=[],          # client-side subtype filter; [] = no filter
    ssl_verify=ssl_verify,
    verbose=True,         # prints progress to stdout
    type_fields=type_fields,
    base_fields=base_fields,
    limit=None,           # set to an int to stop early, e.g. limit=10
)

# 4. Use the records
for app in records:
    print(app["displayName"], app.get("status"))
```

### Listing available FactSheet types

```python
from lean_ix.download import list_factsheet_types

types = list_factsheet_types("http://localhost:8765/graphql")
print(types)
# ['Application', 'BusinessCapability', 'DataObject', 'ITComponent', ...]
```

---

## Downloading Relationships

### Simplest usage — `run_download_relations()`

```python
from lean_ix.download import run_download_relations

run_download_relations(
    proxy_url="http://localhost:8765/graphql",
    type_name="Application",    # required when calling as library (no interactive prompt)
    output_path="app_relations.csv",  # None = auto-generate {Type}_relations.csv
    list_relations=False,
    limit=None,                 # int to stop early, e.g. limit=50 for testing
)
```

### Working with relationship rows in memory

```python
from lean_ix.download import (
    introspect_type,
    list_relation_fields,
    build_relations_query,
    fetch_all_relations,
)

PROXY = "http://localhost:8765/graphql"

# 1. Introspect and find relation fields
type_fields = introspect_type(PROXY, "Application")
relation_fields = list_relation_fields(type_fields)

print("Available relations:")
for f in relation_fields:
    print(f"  {f['name']}")

# 2. Optionally filter to specific relations
relation_fields = [f for f in relation_fields if "BusinessCapability" in f["name"]]

# 3. Build query and fetch
query = build_relations_query("Application", relation_fields)
rows = fetch_all_relations(
    proxy_url=PROXY,
    query=query,
    type_name="Application",
    relation_fields=relation_fields,
    verbose=True,
    limit=None,   # set to an int to stop early, e.g. limit=50
)

# 4. Each row is a dict with these keys:
#   source_id, source_displayName, relation, target_id, target_displayName
for row in rows:
    print(f"{row['source_displayName']} --[{row['relation']}]--> {row['target_displayName']}")
```

---

## Writing output

```python
import io
import sys
from lean_ix.download import write_csv, write_json, write_relations_csv

# Write FactSheet records to a file
with open("apps.csv", "w", encoding="utf-8", newline="") as f:
    write_csv(records, f)

# Write FactSheet records as JSON
with open("apps.json", "w", encoding="utf-8") as f:
    write_json(records, f)

# Write relation rows
with open("app_relations.csv", "w", encoding="utf-8", newline="") as f:
    write_relations_csv(rows, f)

# Write to an in-memory buffer
buf = io.StringIO()
write_csv(records, buf)
csv_text = buf.getvalue()
```

---

## SSL options

```python
import ssl

# Default — system CA bundle
ssl_verify = True

# Legacy mode — fixes corporate SSL inspection proxies (Prisma)
ctx = ssl.create_default_context()
ctx.verify_flags &= ~ssl.VERIFY_X509_STRICT
ssl_verify = ctx

# Custom CA bundle (path to PEM file)
ssl_verify = "/path/to/corporate-ca.pem"

# Disable verification entirely (insecure, dev only)
ssl_verify = False
```

Pass `ssl_verify` to any function that accepts it:
`introspect_type`, `list_factsheet_types`, `fetch_all`, `fetch_all_relations`,
`run_download`, `run_download_relations`.
"""
