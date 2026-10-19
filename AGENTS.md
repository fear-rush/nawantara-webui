# Nawantara UI

Workspace setup and user flows live in `../../docs/`. Run integrated development
commands from `../..`; this checkout retains its own Git history and checks.

This is the Open WebUI fork used by `nawantara-workspace`.
The `upstream` remote tracks Open WebUI; `main` contains our changes.

- Edit source directly. Do not patch compiled bundles or copy changes from another repo.
- Documents, OCR and full-text search belong to Paperless. Open WebUI holds
  conversations and reaches Paperless through the MCP tool server; it keeps no
  document store, index or conversion pipeline of its own.
- Do not add a second document source here without a decision record. The
  workspace previously carried a Nextcloud file connector, and its removal was
  deliberate: two document stores meant two permission systems.
- Keep backend package names, API contracts and license notices intact.
- Use the upstream npm lockfile. Run the frontend dev server for UI work.
- The workspace provides isolated development services and credentials.
- Check builds and affected behavior. The user owns visual review.
