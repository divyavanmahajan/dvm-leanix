# SAP LeanIX GraphQL API

> Source: [SAP Help Portal — GraphQL API](https://help.sap.com/docs/leanix/ea/graphql-api)  
> Specification: [GraphQL October 2021](https://spec.graphql.org/October2021/)

---

## Overview

GraphQL is a query language for APIs developed by Facebook as a flexible, efficient alternative to REST. The SAP LeanIX GraphQL API lets you retrieve and manipulate **Fact Sheet** data using queries and mutations.

### Benefits

| Benefit | Detail |
|---|---|
| **Single-request aggregation** | Combine related data in one call — no more stitching REST responses |
| **Exact field selection** | Transfer only the attributes you need, reducing latency for large data volumes |
| **Self-documenting schema** | Strongly-typed schema with field descriptions; explore via the built-in GraphiQL tool |
| **Additive evolution** | New fields/types are added without breaking existing queries; `@deprecated` replaces versioning |

### Use cases

- Retrieve single or bulk Fact Sheets with specific attributes
- Manage relations between Fact Sheets
- Update Fact Sheet attributes
- Build custom reports and dashboards
- Automate EA governance workflows

---

## Authentication

### Option 1 — Browser session (interactive use)

When you open the GraphiQL tool inside your LeanIX workspace, authentication is handled automatically. No token is needed.

When using `dvm-leanix`, the proxy extracts the Bearer token from your already-logged-in browser via Playwright CDP. See the [main README](../README.md) for setup steps.

### Option 2 — Technical User API key (automation/CI)

A **Technical User** is a service account created in the LeanIX administration area. It has an **API key** (a long-lived secret) that you exchange for a short-lived Bearer token via the OAuth2 client-credentials grant:

```
POST https://{HOST}/services/mtm/v1/oauth2/token
Authorization: Basic base64("apitoken:{API_KEY}")
Content-Type: application/x-www-form-urlencoded

grant_type=client_credentials
```

The response contains an `access_token` that you include as `Authorization: Bearer {access_token}` on every GraphQL request.

#### Creating a Technical User

1. In your LeanIX workspace, go to **Administration → Technical Users**
2. Click **Create Technical User**
3. Set a name and assign appropriate roles/permissions
4. Copy the generated **API key** — it is shown only once

#### Exchange the API key for a Bearer token (Python)

```python
import httpx

HOST = "https://eu-10.leanix.net"
API_KEY = "your-api-key-here"

response = httpx.post(
    f"{HOST}/services/mtm/v1/oauth2/token",
    auth=("apitoken", API_KEY),
    data={"grant_type": "client_credentials"},
)
response.raise_for_status()
access_token = response.json()["access_token"]
```

#### Using `dvm-leanix` with an API key

```powershell
# Pass the API key directly
dvm-leanix --api-token "your-api-key-here"

# Or export as an environment variable (recommended for scripts/CI)
$env:LEANIX_API_TOKEN = "your-api-key-here"
dvm-leanix
```

When an API key is provided, `dvm-leanix`:
- Exchanges it for a Bearer token via OAuth2 at startup
- Automatically re-exchanges for a fresh token when the current one expires (no browser required)

---

## API Endpoints

| Endpoint | Purpose |
|---|---|
| `https://{HOST}/services/pathfinder/v1/graphql` | Standard GraphQL queries and mutations |
| `https://{HOST}/services/pathfinder/v1/graphql/upload` | GraphQL file uploads (multipart) |
| `https://{HOST}/services/mtm/v1/oauth2/token` | OAuth2 token exchange (Technical User) |

For the europe sandbox workspace:  
`HOST = eu-10.leanix.net`

---

## Queries

Queries retrieve data — equivalent to `GET` in REST APIs.

### Retrieve a single Fact Sheet by ID

```graphql
{
  factSheet(id: "28fe4aa2-6e46-41a1-a131-72afb3acf256") {
    id
    name
    type
  }
}
```

### Retrieve all Fact Sheets of a type

```graphql
{
  allFactSheets(factSheetType: Application, first: 10) {
    totalCount
    edges {
      node {
        id
        name
        displayName
        lxState
        completion { percentage }
      }
    }
  }
}
```

### Introspect the schema

```graphql
query {
  __schema {
    types {
      name
      kind
      fields { name }
    }
    queryType { name }
  }
}
```

---

## Mutations

Mutations modify data — equivalent to `POST`, `PUT`, `PATCH`, `DELETE` in REST.

### Create a Fact Sheet (with variable)

```graphql
mutation ($input: BaseFactSheetInput!) {
  createFactSheet(input: $input) {
    factSheet {
      id
      name
      type
    }
  }
}
```

Variables:
```json
{ "input": { "name": "New Application", "type": "Application" } }
```

### Create a Fact Sheet (inline)

```graphql
mutation {
  createFactSheet(input: { name: "New Application", type: Application }) {
    factSheet { id name type }
  }
}
```

### Batch mutations with aliases

Include multiple mutations in one request using aliases. Recommended chunk size: ~50 mutations.

```graphql
mutation {
  fs1: createFactSheet(input: { name: "App One", type: Application }) {
    factSheet { id }
  }
  fs2: createFactSheet(input: { name: "App Two", type: Application }) {
    factSheet { id }
  }
}
```

> ⚠️ If any mutation in a batch is invalid, **none** of the mutations are executed.

---

## Filtering

Use `facetFilters` to filter `allFactSheets` results.

### Filter by type and lifecycle

```graphql
query ($filter: FilterInput, $sort: [SortInput]) {
  allFactSheets(filter: $filter, sort: $sort) {
    totalCount
    edges {
      node {
        id
        displayName
        technicalSuitability
        functionalSuitability
        lifecycle { phases { phase startDate } }
      }
    }
  }
}
```

Variables:
```json
{
  "filter": {
    "facetFilters": [
      { "facetKey": "FactSheetTypes", "operator": "OR", "keys": ["Application"] },
      { "facetKey": "technicalSuitability", "operator": "OR", "keys": ["unreasonable"] },
      { "facetKey": "functionalSuitability", "operator": "NOR", "keys": ["perfect", "appropriate"] },
      { "facetKey": "lifecycle", "operator": "OR", "keys": ["phaseIn"],
        "dateFilter": { "type": "between", "from": "2023-01-01", "to": "2029-12-31" } }
    ]
  },
  "sort": [{ "key": "updatedAt", "order": "desc" }]
}
```

### Available facet operators

| Operator | Meaning |
|---|---|
| `OR` | Include items matching any of the keys |
| `AND` | Include items matching all keys |
| `NOR` | Exclude items matching any of the keys |

### Retrieve available facet keys

```graphql
{
  allFactSheets {
    filterOptions {
      facets {
        facetKey
        results { name key }
      }
    }
  }
}
```

---

## Pagination

LeanIX uses **relay cursor-based pagination** (not offset-based).

### First page

```graphql
{
  allFactSheets(first: 100) {
    pageInfo {
      hasNextPage
      endCursor
    }
    edges {
      node { id name }
    }
  }
}
```

### Subsequent pages

Use the `endCursor` from `pageInfo` as the `after` argument:

```graphql
{
  allFactSheets(first: 100, after: "cursor-from-previous-response") {
    pageInfo {
      hasNextPage
      endCursor
    }
    edges {
      node { id name }
    }
  }
}
```

Repeat until `hasNextPage` is `false`.

> **Tip:** The `dvm-leanix download` command handles cursor-based pagination automatically.

---

## Error Handling

Unlike REST APIs, GraphQL always returns HTTP 200. Errors appear in the `errors` array of the JSON response body.

```json
{
  "data": null,
  "errors": [
    {
      "message": "No fact sheet found with id '...'",
      "locations": [{ "line": 2, "column": 3 }]
    }
  ]
}
```

**Always check the `errors` field** in the response, not just the HTTP status code.

### Complexity limits

To prevent Denial-of-Service attacks, LeanIX rejects queries that exceed a complexity threshold. If you hit this limit, simplify your query or split it into smaller requests.

---

## Best Practices

1. **Use pagination** for large data sets (`allFactSheets` with `first` + cursor)
2. **Request only needed fields** — avoid `__typename`-only queries in production
3. **Use variables** for mutations with many parameters — avoids query-string injection and improves readability
4. **Batch carefully** — alias multiple mutations in one request, but cap at ~50 per batch to avoid HTTP timeouts
5. **Handle errors in the response body** — not via HTTP status codes
6. **Use the Integration API** for complex ETL workflows where GraphQL complexity limits are a constraint

---

## The GraphiQL Tool

The built-in GraphiQL tool in your LeanIX workspace lets you explore the schema and run queries interactively.

**To open:** Help menu → **Developer Tools → GraphQL Editor**

Features:
- **Query Editor** with variable support (JSON format)
- **Documentation Explorer** — browse the full schema
- **Request History** — view, favourite, and replay previous requests
- **GraphiQL Explorer** — visually build queries by selecting fields

> By default only admins have access. Admins can grant access to other roles via **Administration → User Roles and Permissions**.

The `dvm-leanix` proxy serves the same GraphiQL interface locally at `http://localhost:8765/graphql`, with no need to log in to the LeanIX web app.

---

## REST API Migration Guide

Mapping of common REST endpoints to their GraphQL equivalents:

| REST | GraphQL |
|---|---|
| `GET /factSheets` | `allFactSheets { edges { node { ... } } }` |
| `GET /factSheets/{id}` | `factSheet(id: "...") { ... }` |
| `GET /factSheets/hierarchy/{rootId}` | `factSheet(id: "...") { ... relToChild { ... } }` |
| `GET /factSheets/{id}/relations` | `factSheet(id: "...") { relApplicationToITComponent { ... } }` |
| `POST /factSheets` | `mutation { createFactSheet(input: {...}) { factSheet { id } } }` |
| `PATCH /factSheets/{id}` | `mutation { updateFactSheet(id: "...", input: {...}) { factSheet { id } } }` |
| `DELETE /factSheets/{id}` | `mutation { archiveFactSheet(id: "...") { factSheet { id } } }` |

> **Note:** GraphQL requires you to explicitly list all fields and relations you need. There is no equivalent to `GET /factSheets/{id}` that returns all fields automatically.

---

## Further Reading

- [SAP Help Portal — GraphQL API](https://help.sap.com/docs/leanix/ea/graphql-api)
- [SAP LeanIX GitHub — example scripts](https://github.com/leanix)
- [GraphQL specification](https://spec.graphql.org/October2021/)
- [Relay cursor connections](https://relay.dev/graphql/connections.htm)
