---
name: decodo-http-specialist
description: "Separate lane (raw HTTP only, not browser): Use Decodo/raw HTTP for residential proxy fetches," API endpoints, JSON endpoints, cheap bulk data fetching, and requests/curl-style workflows. Use when browser rendering is unnecessary.
---

# Decodo HTTP Specialist

## Use For

- Raw HTTP and API endpoints.
- Cheap residential proxy requests.
- Bulk data where browser rendering is wasteful.
- Geo-targeting and proxy rotation.

## Do Not Use For

- Sites requiring real browser rendering.
- Advanced headless browser fingerprinting.
- Logged-in browser sessions.
- Public URLs that redirect into loopback, private-network, link-local, or local-file targets unless the run was explicitly planned for that same target scope.

## Env

- No env var is required for direct raw HTTP against a supplied `http://` or `https://` endpoint.
- Set `DECODO_PROXY` when residential proxy routing, geo-targeting, or IP rotation is required.

## Verification

Start with an IP-check endpoint and one target endpoint. Record status code, final URL, final target scope, resolved target evidence, response size, proxy region, redirect count, and retry count. A `raw_http_redirect_target_scope` event means the redirect was blocked before fetching a sensitive target. A `raw_http_resolved_target_scope` event means a public-looking hostname resolved to loopback, private-network, or link-local before the request was opened. Treat both as non-resumable safety stops; create a new run or replan for the intended target scope.
