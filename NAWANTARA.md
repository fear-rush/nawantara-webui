# Nawantara development

Based on Open WebUI v0.12.3, revision `91ea4e3421d1d8069f13d4a463fcb5986d94ea60`.
Backend dependencies in `Dockerfile.nawantara` must match this revision.

Run `uv run workspace ui` from the workspace root (`../..` from this checkout). It
runs Vite with live reload and proxies the API to the running deployment, which is
the only stack. Edit this checkout directly; Svelte and CSS changes reload in the
browser without a rebuild. Backend changes are not mounted: rebuild and recreate
the service (`docker compose build open-webui && docker compose up -d open-webui`).

Use `Dockerfile.nawantara` for release images. It builds this source, including
branding, without rewriting generated JavaScript.

Keep `upstream` pointed at `https://github.com/open-webui/open-webui.git`.
Fetch upstream and merge a chosen release on an upgrade branch. Review API and
dependency changes, update the runtime image pin, and validate before merging
into `main`. Do not automatically follow upstream main.

The frontend dev server serves port 5173 and proxies to the deployment on port
3000. See [development](../../docs/development.md) for initialization,
credentials and restart commands and [architecture](../../docs/architecture.md)
for boundaries.

## Documents

Paperless owns documents, OCR and full-text search. This application keeps no
document index, no embedding pipeline and no file connector: it reaches Paperless
through the MCP tool server that the workspace Compose file runs, and private
conversations stay private until their owner publishes something from them.

The workspace previously carried `backend/nawantara_nextcloud/`, a WebDAV and
OCS connector with its own derived index. It was removed rather than adapted, and
`open_webui/utils/tools.py` and `open_webui/main.py` no longer reference it. Do not
reintroduce a second document source without a decision record.

## Branding

Visible product text is `Nawantara`. Keep internal Open WebUI identifiers, package
names, API contracts and license notices unchanged.
