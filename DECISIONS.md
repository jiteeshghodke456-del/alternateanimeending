# Decisions log

Agents record non-obvious decisions here. Anything marked **Second opinion wanted** should be checked by Jiteesh (e.g. with ChatGPT) before submission.

## Second opinion wanted
_(none yet)_

## Decided
- Stack fixed per CLAUDE.md (FastAPI + SQLite + React/Vite static, one container, HF Spaces).
- Frontend: Tailwind v4 (via @tailwindcss/vite, `@config` loads tailwind.config.ts) because the Magic UI registry components use v4 syntax. Magic UI (number-ticker, border-beam, animated-list, blur-fade) came from the official registry into `frontend/src/components/ui/`.
- Theme: `html[data-theme]` is always set (inline script in index.html follows prefers-color-scheme unless the toggle stored a choice in localStorage). Dark variant = `[data-theme="dark"]`.
- Contrast: semantic colours have AA-safe text variants (`text-flag-text`, `text-human-text`, `text-pass-text`); the raw #FF453A / #FF9F0A / #30D158 are used only for bars, fills and tints, always paired with an icon and label.
- Session: `src/lib/session.tsx` calls `/api/demo/login` and `/api/me` with plain fetch (api.ts did not exist yet). Demo user ids assumed to be `asha`, `rohan`, `meera`, `kabir`. Falls back to a local identity when the backend is offline.
- Placeholder pages and the gallery use `src/lib/mock.ts` (local types) with a "sample data" note on screen; /dev/gallery exists only in dev builds.
