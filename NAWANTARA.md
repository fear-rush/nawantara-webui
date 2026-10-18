# Nawantara development

Based on Open WebUI v0.12.3, revision `91ea4e3421d1d8069f13d4a463fcb5986d94ea60`.
Backend dependencies in `Dockerfile.nawantara` must match this revision.

Run `uv run workspace dev ui` from the sibling `nawantara-workspace` checkout.
It runs Vite against the isolated development backend. Edit this checkout directly;
Svelte and CSS changes reload in the browser. Backend source is mounted into the
development container with Uvicorn reload enabled.

Use `Dockerfile.nawantara` for release images. It builds this source, including
branding and Document Activity, without rewriting generated JavaScript.

Keep `upstream` pointed at `https://github.com/open-webui/open-webui.git`.
Fetch upstream and merge a chosen release on an upgrade branch. Review API and
dependency changes, update the runtime image pin, and validate before merging
into `nawantara`. Do not automatically follow upstream main.
