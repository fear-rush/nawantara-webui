# Nawantara development

Based on Open WebUI v0.12.3, revision `91ea4e3421d1d8069f13d4a463fcb5986d94ea60`.
Backend dependencies in `Dockerfile.nawantara` must match this revision.

Run `uv run workspace dev ui` from the workspace root (`../..` from this checkout).
It runs Vite against the isolated development backend. Edit this checkout directly;
Svelte and CSS changes reload in the browser. Backend source is mounted into the
development container with Uvicorn reload enabled.

Use `Dockerfile.nawantara` for release images. It builds this source, including
branding, Nextcloud connections and Document Activity, without rewriting generated JavaScript.

Keep `upstream` pointed at `https://github.com/open-webui/open-webui.git`.
Fetch upstream and merge a chosen release on an upgrade branch. Review API and
dependency changes, update the runtime image pin, and validate before merging
into `main`. Do not automatically follow upstream main.

The frontend serves port 5173 and uses the isolated backend on 3001. Production
uses port 3000. See [development](../../docs/development.md) for initialization,
credentials and restart commands, [architecture](../../docs/architecture.md) for
boundaries, and [user flows](../../docs/user-flows.md) for document behavior.

## Nextcloud integration

`backend/nawantara_nextcloud/` owns the supported Nextcloud API client, encrypted
connections, local extraction/OCR, derived index and durable jobs. Native tools
are registered in `open_webui/utils/tools.py`. The index is separate from native
Knowledge collections so native retrieval cannot bypass source authorization.

Run integration unit checks from the workspace with:

```sh
uv run python -m unittest discover -s apps/nawantara-webui/backend/nawantara_nextcloud/tests
```

The Docker runtime includes English/Indonesian Tesseract and Poppler. HTTP access,
PDF extraction and embeddings are verified through the workspace live check;
additional OCR checks run inside the release container. Application credentials
are encrypted using `NEXTCLOUD_CREDENTIAL_KEY`; keep the key with backups.

Embeddings use the workspace's internal llama.cpp service with the pinned
EmbeddingGemma 2 Q8_0 text model. `embeddings.py` owns query/document prompts,
vector validation and the index contract. Do not use the local SentenceTransformers
loader for this deployment. Changing weights or prompts requires a new index
contract and rebuilding derived vectors. Native private-file retrieval uses the
same local endpoint, with prefixes supplied through Compose.

Nextcloud UI components reuse the application theme, badges, confirmation dialogs
and modal. Document Activity uses paginated server filters and safe job events
from `activity.py`; never send raw payloads, source links or extracted text to
admin responses. The shared job-detail component switches between owner and
admin endpoints, which enforce their own authorization independently of the UI.

The Nextcloud page follows `routes/(app)/workspace/+layout.svelte` for its
full-width content and compact top navigation, and `workspace/Knowledge.svelte`
for file-row density and typography. Its sidebar link uses the same icon slot,
text size, spacing and active treatment as Workspace, with a shared Nextcloud
mark in both expanded and collapsed navigation. Keep these references when
changing the page instead of introducing a separate dashboard style.

Search modes, snapshots and reciprocal-rank fusion live in `search.py`. FFF's
Python binding runs with AI mode off; invalid regex fallback results must be
rejected. `evidence.py` handles current-access-checked version comparison.
`actions.py` restricts Context Agent MCP operations and stores owner-confirmed
proposals. Never add the confirmation endpoint as a model tool. `analysis.py`
passes selected bytes to the fixed-image broker and stores private generated files.
`events.py` coalesces authenticated file notifications without treating event data
as an access grant. Rebuild runtime images after changing pinned dependencies.
