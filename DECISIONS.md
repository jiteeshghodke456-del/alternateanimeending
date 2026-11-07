# Decisions log

Agents record non-obvious decisions here. Anything marked **Second opinion wanted** should be checked by Jiteesh (e.g. with ChatGPT) before submission.

## Second opinion wanted
- **Auth fallback token (backend scaffold).** Login sets an httponly SameSite=Lax cookie *and* returns the same signed token; `api.ts` keeps it in sessionStorage and sends `Authorization: Bearer` (and `?token=` for SSE/images). Reason: the HF Space page embeds the app in a cross-site iframe, and private windows block third-party cookies, so cookie-only login can silently fail. Risk: token in sessionStorage is readable by JS (acceptable for a demo with no real accounts). Judges should still be given the direct `*.hf.space` URL.
- **Meaning of `Finding.quote`.** §4 has `quote` on Finding; we treat it as the *offending content excerpt*. The guideline quote + page come from the joined `rule` (RuleRef) in `FindingOut`.
- **Rule edits are in place.** PATCH bumps `Rule.version` and `Finding.rule_version` records which version flagged it; old rule text is not kept (only the audit event lists changed fields). Good enough for Round 2; a rule history table would be Round 3.

## Decided
- Stack fixed per CLAUDE.md (FastAPI + SQLite + React/Vite static, one container, HF Spaces).
- Frontend: Tailwind v4 (via @tailwindcss/vite, `@config` loads tailwind.config.ts) because the Magic UI registry components use v4 syntax. Magic UI (number-ticker, border-beam, animated-list, blur-fade) came from the official registry into `frontend/src/components/ui/`.
- Theme: `html[data-theme]` is always set (inline script in index.html follows prefers-color-scheme unless the toggle stored a choice in localStorage). Dark variant = `[data-theme="dark"]`.
- Contrast: semantic colours have AA-safe text variants (`text-flag-text`, `text-human-text`, `text-pass-text`); the raw #FF453A / #FF9F0A / #30D158 are used only for bars, fills and tints, always paired with an icon and label.
- Session: `src/lib/session.tsx` calls `/api/demo/login` and `/api/me` with plain fetch (api.ts did not exist yet). Demo user ids assumed to be `asha`, `rohan`, `meera`, `kabir`. Falls back to a local identity when the backend is offline.
- Placeholder pages and the gallery use `src/lib/mock.ts` (local types) with a "sample data" note on screen; /dev/gallery exists only in dev builds.
- Backend scaffold: extra columns beyond §4 (all optional) are listed in the `models.py` docstring: `ContentItem.key`, `Version.kind/caption/alt_text/mime`, `Unit.char_start/char_end`, `Finding.rule_version/tier/claim_status/dismiss_note/edited_text`, `AICall.unit_id/ok`, nullable `AuditEvent.item_id` (brand-kit edits) + `version_id`.
- Seed loader: numeric `users.json` ids become lowercase name slugs (`1/Asha` -> `asha`) so ids match the frontend and stay readable; string ids are kept. Items reference creators by id or name. The loader uses `backend/app/seed/` when both `users.json` and `rules.json` exist, otherwise `seed/_placeholder/` (never mixed). `SEED_DIR` overrides.
- Seeded items get `Version.check_status = "not_run"` (no findings at boot). The pipeline owner decides whether to run checks on seeded items at startup so the dashboard has data.
- `GET /api/me` works without a session and returns `{user|null, demo_mode, users[]}`; the role picker uses `users` instead of a separate endpoint.
- Errors: every non-2xx is `{"detail": "<human message>"}`; 422 adds `errors[]`. Unknown `/api/*` routes return JSON 404, everything else falls back to `frontend/dist/index.html`.
- Datetimes are stored naive-UTC in SQLite and serialised with `Z` (`UtcDatetime` in schemas.py).
- Uploads: type is sniffed from bytes (PNG/JPG/WEBP/PDF), not the filename. Limits: 10 MB, 20 PDF pages, 20k text chars. PDF paragraphs = vertical-gap grouping of pdfplumber lines.
- Docker: runs as uid 1000; DB and uploads live in `/home/user/data` (ephemeral on free HF Spaces: data resets on restart, which the README must say).

## Workflow agent (review.py / insights / finding actions)
- **Rules live in `app/workflow.py`** and feed both the handlers and `item_actions()`, so a disabled button always shows the same message the server would return. 403 = who you are, 409 = state of the item, 422 = bad input.
- **State changes:** submit only from `findings_ready` (or `draft` when the latest version's check is `done`); `checking` is never submittable. `changes_requested` leaves via `resubmit` (new version, then `checking`), which stands in for the diagram's `changes_requested -> draft`. Audit `detail` is `{from, to, version}`.
- **Blocking = open.** A blocking finding blocks submit only while `status == "open"`; accepted, edited, dismissed and overridden all count as handled. Override sets `status="overridden"` and writes an `Override` row.
- **request-changes / reject** need a non-empty note (422) and cannot be done by the item's creator. Both also post the note as a comment.
- **Finding actions:** allowed for the item's creator, reviewers and approvers (not admins), on the latest version only, not once approved/rejected. Accept/edit also require a span.
- **Resubmit** keeps the image for image items (caption is the version text). A PDF item's new version is a text version of the corrected paragraphs. Overlapping spans: earlier one wins, later skipped and returned in `skipped`.
- **Contract changes (schemas.py + api.ts):** `ResubmitOut` gains `diff[{op,text}]`, `applied[]`, `skipped[{finding_id,reason}]`; `AIUsageOut` gains `summary` (groups are now provider + purpose, `model` = distinct models joined); `CountBucket.label` (rule statement on `top_rules`); `DismissalRate.statement`; `OverrideIn` min length is checked in the handler (10 chars) for a readable message.
- **Insights** count findings across all versions (a v2 re-check adds its own findings); fix-accept rate = `accepted / findings that had a fix`.
- **Seed:** seeded items now carry an audit trail matching their state (created, submitted, changes_requested/approved) and a reviewer comment on changes-requested items. Seeded `draft` items are `not_run`: the first check is triggered by `POST /items/{id}/resubmit` (creates v2).
