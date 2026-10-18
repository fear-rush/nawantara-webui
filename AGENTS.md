# Nawantara UI

Workspace setup and user flows live in `../../docs/`. Run integrated development
commands from `../..`; this checkout retains its own Git history and checks.

This is the Open WebUI fork used by `nawantara-workspace`.
The `upstream` remote tracks Open WebUI; `main` contains our changes.

- Edit source directly. Do not patch compiled bundles or copy changes from another repo.
- Nextcloud integration lives in `backend/nawantara_nextcloud/`; its account and file UI is `src/routes/(app)/nextcloud`.
- Document Activity reads sanitized job status from that integration. Never expose private content or filenames in admin monitoring.
- Nextcloud-derived content must not enter native Knowledge or File storage. Every retrieval must recheck current source access.
- Keep backend package names, API contracts and license notices intact.
- Use the upstream npm lockfile. Run the frontend dev server for UI work.
- The workspace provides isolated development services and credentials.
- Check builds and affected behavior. The user owns visual review.
