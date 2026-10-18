# Nawantara UI

This is the Open WebUI fork used by `nawantara-workspace`.
The `upstream` remote tracks Open WebUI; `nawantara` contains our changes.

- Edit source directly. Do not patch compiled bundles or copy changes from another repo.
- Document Activity lives in `src/routes/(app)/admin/document-activity` and `backend/nawantara_activity.py`.
- Keep backend package names, API contracts and license notices intact.
- Use the upstream npm lockfile. Run the frontend dev server for UI work.
- The workspace provides isolated development services and credentials.
- Check builds and affected behavior. The user owns visual review.
