"""
GraphiQL standalone HTML page served at GET /graphql.

Uses the official GraphiQL CDN build from unpkg.
"""

GRAPHIQL_HTML = """\
<!DOCTYPE html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>GraphiQL – LeanIX</title>
    <style>
      body { height: 100%; margin: 0; overflow: hidden; font-family: sans-serif; }
      #graphiql { height: calc(100vh - 32px); }
      #topbar {
        height: 32px; background: #1c1c1c; color: #ccc;
        display: flex; align-items: center; padding: 0 12px;
        font-size: 12px; gap: 16px;
      }
      #topbar a { color: #7ec8e3; text-decoration: none; }
      #topbar a:hover { text-decoration: underline; }
    </style>
    <link rel="stylesheet" href="https://unpkg.com/graphiql@3/graphiql.min.css" />
  </head>
  <body>
    <div id="topbar">
      <span>LeanIX GraphQL Proxy</span>
      <a href="https://help.sap.com/docs/leanix/ea/graphql-api" target="_blank" rel="noopener">
        SAP LeanIX GraphQL API docs &#8599;
      </a>
    </div>
    <div id="graphiql">Loading GraphiQL…</div>

    <script
      crossorigin
      src="https://unpkg.com/react@18/umd/react.production.min.js"
    ></script>
    <script
      crossorigin
      src="https://unpkg.com/react-dom@18/umd/react-dom.production.min.js"
    ></script>
    <script
      crossorigin
      src="https://unpkg.com/graphiql@3/graphiql.min.js"
    ></script>

    <script>
      const fetcher = GraphiQL.createFetcher({
        url: window.location.origin + "/graphql",
      });
      const root = ReactDOM.createRoot(document.getElementById("graphiql"));
      root.render(React.createElement(GraphiQL, { fetcher }));
    </script>
  </body>
</html>
"""
