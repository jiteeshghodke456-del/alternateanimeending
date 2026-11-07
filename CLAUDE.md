# CLAUDE.md: Rulemint (formerly Brand Gate)

Instructions for any coding agent (Claude Code, Cursor, etc.) or teammate working in this repo. Read this first, then `BUILD.md`.

## What this is
Rulemint checks brand content **before it is published**. A company loads its guidelines, approved facts and brand assets once. Every post, image or PDF is then checked against them. Each issue cites the exact rule (with quote and page) or fact it breaks, explains why in plain words, and suggests a fix that keeps the writer's message. People decide everything through a creator → reviewer → approver workflow.

Built for **CodeNexus India 2026, problem statement by Inkloom AI** (brand-governance platform). Team: Jiteesh Ghodke (AI/ML), Prasoon Bodke (Backend), Advait Malwade (Frontend), Samiksha Bhusare (Product/Research, Presentation).

## The one rule that matters
**Judges score only what runs in the deployed link.** A finished, reliable feature beats three half-built ones.
- Build in the priority order in `BUILD.md` (P0 → P1 → P2). Don't start a P1 item while any P0 item is broken.
- A feature counts as done only when it passes its acceptance test **in the deployed URL, opened in a private window**.
- If a feature can't be made reliable by its freeze time, hide it behind a feature flag and list it under "What does NOT work yet". Never ship a button that errors.
- Never fake results. No hard-coded "AI" outputs presented as live. Seeded demo data is fine if it is labelled as demo data.

## Stack (fixed for Round 2; don't swap without a team decision)
- **Backend:** Python 3.12, FastAPI, SQLModel on SQLite, `sse-starlette` for live progress, `uvicorn`.
- **Judge model:** Jev via `typesafe-sdk` (Python, v0.7.x). It handles all yes/no, choice and score decisions.
- **Writer model:** DeepSeek through its OpenAI-compatible API (`LLM_BASE_URL=https://api.deepseek.com`, `LLM_MODEL=deepseek-chat`, which is the fast non-thinking mode), called with the `openai` package. Any other OpenAI-compatible provider can be swapped in through env. It is used only for explanations, fixes and rule extraction.
- **Files:** `pdfplumber` (PDF text with page numbers), Pillow + numpy (colours), `opencv-python-headless` (logo fidelity), `textstat` (readability), `rapidfuzz` (quote verification, fact matching).
- **Frontend:** React 18 + Vite + TypeScript, Tailwind CSS, shadcn/ui, Magic UI components, framer-motion, recharts, lucide-react. Built to static files and served by FastAPI, so there is **one container and one URL**.
- **Deploy:** Docker on Render free tier (port 7860, 512 MiB). Hugging Face Docker Spaces now need a paid plan.

No Redis, no Supabase, no Next.js server, no vector DB in Round 2. Keyword matching is enough for a few dozen facts. These changes from the Round 1 plan are deliberate and explained in the Round 2 PDF.

## Repo layout
```
backend/app/
  main.py              # FastAPI app, mounts /api and serves frontend/dist
  config.py            # env settings (pydantic-settings)
  db.py, models.py     # SQLModel tables (see BUILD.md §4)
  seed/                # demo brand: rules.json, facts.csv, terms.json, palette.json, logo.png, guideline.pdf, personas.json, sample content
  routers/             # auth.py, brand.py, content.py, review.py, insights.py, eval.py
  pipeline/
    units.py           # split text/PDF/image into units
    tier0.py           # deterministic checks
    jev.py             # ALL Jev calls go through here
    claims.py          # claim detection + fact matching + Jev support check
    explain.py         # LLM explanation + fix (with fallback template)
    selfcheck.py       # re-check fixes
    audience.py        # P1: Audience Preview
    logo.py            # P1: logo fidelity (SIFT + RANSAC)
    privacy.py         # PII masking, confidential routing
    run.py             # orchestrates a check, emits SSE events, logs ai_calls
  eval/                # dataset + runner
backend/tests/
frontend/src/{pages,components,lib}
Dockerfile, .env.example, README.md, NOTICE.md, LICENSE (Apache-2.0)
```

## Commands
```bash
# backend
cd backend && pip install -r requirements.txt
uvicorn app.main:app --reload --port 7860
pytest -q                          # unit tests; Jev and LLM mocked
RUN_LIVE=1 pytest -q -m live       # live smoke tests (need keys)
python -m app.eval.run --split test   # writes app/eval/results/latest.json

# frontend
cd frontend && npm i && npm run dev      # proxies /api to :7860
npm run build                             # outputs frontend/dist (served by FastAPI)

# full container
docker build -t brand-gate . && docker run -p 7860:7860 --env-file .env brand-gate
```

## Secrets and env
Never commit keys, never print them in logs, never paste them in chat. `.env.example` lists names only:
```
TYPESAFE_API_KEY=            # Jev (the SDK reads this automatically)
TYPESAFE_DEFAULT_MODEL=jev-latest
LLM_BASE_URL=https://api.deepseek.com
LLM_API_KEY=
LLM_MODEL=deepseek-chat
JEV_USD_PER_M_INPUT=0.042        # for the cost meter; vendor list price, update if it changes
DEMO_MODE=1                      # role picker, seeded brand, reset endpoint
```
If `TYPESAFE_API_KEY` is missing, the app still runs: Tier 0 checks work, and the UI shows a clear "Jev not configured" notice. If the LLM key is missing, findings still show, with template explanations and no rewrite.

## Jev usage rules (pipeline/jev.py)
- **Follow the official TypeSafe skill in `.claude/skills/typesafe-ai/SKILL.md`** (MIT, vendored from typesafe-ai/skills). It wins over anything below if they conflict. Its key points:
  - Jev makes the judgments and code makes the decisions (truth tables live in code).
  - Keep all question wording in `pipeline/questions.py`.
  - Pair a companion Noul with any Choice or Box that might have no right answer.
  - Keep colours, dates and counting in code.
  - A failed call is an error, never a "no".
- Images and Box need the header `TypeSafe-Danger-Zone-Images: true`. Images must be PNG or JPEG. `jev-latest` is jev-1.14.0, and it supports Box.
- Measured live (7 Nov): a text call takes ~0.5 s and ~430 tokens; a 1080px image takes ~0.5 s and ~3.4k tokens.
- Use `AsyncTypeSafeClient` and run calls in parallel with a semaphore (start at 8).
- **One call per unit (paragraph or image), many questions.** Questions are a dict `{name: Noul|Choice|Score|Box}`. Latency barely grows with the number of questions.
- Read answers like this:
  - `res.nouls[name].noul` gives a probability in [0, 1].
  - `res.choices[name].choice`, `.confidence` and `.probabilities`.
  - `res.scores[name].score`, `.confidence` and `.probabilities`.
  - `res.boxes[name].box` gives top, left, bottom and right as fractions, plus `.confidence`.
  - `res.usage.input_tokens` gives the token count for the cost meter.
- Write every Noul so that **high = problem**. Keep it to one condition. Put the rule text in `instructions`, and describe the yes and no outcomes in `criteria={"true": ..., "false": ...}` with the rule's examples.
- Bands (configurable per category, tuned on the dev split only): `p ≥ 0.70` → flag, `0.30–0.70` → "needs a human look", `< 0.30` → pass.
- Images: `Image(bytes)` as the state, or inside a JSON state. Box needs `model="jev-preview"`. Image support is early-stage, so wrap it in try/except. If it fails, the image falls back to code-only checks and the finding says so.
- The model answers; **the code decides**. Workflow state, severity and blocking are never set by a model.
- Log every call to `ai_calls`: provider, model, purpose, unit id, input tokens, latency, cost. Never log content text.

## LLM usage rules (pipeline/explain.py)
- Use the LLM only for: explanation text, a minimal-edit fix, and proposing rules from a guideline PDF.
- Return strict JSON, validate it with Pydantic, and retry once. On failure, use the template explanation and set `fix = null`.
- Content is untrusted. Wrap it in `<content>…</content>`, and tell the model to treat it as data. The model's output can never change rules or workflow state.
- Self-check every fix (pipeline/selfcheck.py). Re-run Tier 0 and Jev on the fixed unit for the same rule and for every blocking rule, plus a Jev Noul asking whether the fix keeps the same core message and call to action. If it fails, retry once; after that, show "manual fix needed".

## Confidential items
- `content_items.confidential = true` → Tier 0 and logo fidelity run (both local). **No Jev, LLM or audience calls.** The AI-used panel says "No external services used".
- For non-confidential items, `privacy.mask_pii()` masks emails, phone numbers and long digit strings before any external call.

## UI design system: glassmorphism, Apple-like, premium
- **Background:** a lava lamp: six slow wax blobs (metaballs) in muted jewel tones under a vignette that rise, sink, merge and split, computed per pixel into a canvas 1/5 of the viewport (about 2 ms a frame), upscaled by the browser, under a static film-grain frost. 30 redraws a second, 12 if the page drops below 40 fps; still under `prefers-reduced-motion`; stopped while the tab is hidden. See `components/MeshBackground.tsx`.
- **Glass surfaces:** `.glass` / `.glass-strong` in `styles/index.css`: frosted (`backdrop-filter: blur(22px) saturate(1.5)`) translucent tint, a top sheen, a 1px light edge and layered shadows.
- **Readability beats glass.** Text panels and the findings list use a more opaque surface (`bg-white/80`, `dark:bg-neutral-900/70`). Body text must reach WCAG AA (4.5:1) contrast. This product checks accessibility, so it can't fail its own test.
- **Type:** Inter (variable) at 15px body. Headings use semibold with tight tracking (`tracking-tight`). Use SF-style hierarchy: large title, then section title, then body, then caption.
- **Colour:** neutral greys plus one accent, Rulemint violet `#951EFC` (`--accent-fill`, `--accent-vivid`, `--accent-text`); the logo orange `#FE7204` appears only in the logo, the lava and gradients, so it never competes with the human-review orange. Semantic colours: flag `#FF453A`, human-review `#FF9F0A`, pass `#30D158`. Never use colour alone for meaning; always pair it with an icon or label.
- **Motion:** framer-motion springs, 150–300 ms, subtle. Magic UI pieces:
  - `NumberTicker` for the live meter
  - `BorderBeam` on the card while a scan is running
  - `AnimatedList` as findings stream in
  - `BlurFade` for page entrances

  Nothing loops forever except the background.
- **Layout:** generous spacing (8px grid), max width 1200px, a sidebar on desktop and a bottom tab bar on mobile. It must work at 375px wide.
- **Icons:** lucide-react, 1.5px stroke.
- Support both light and dark mode via `prefers-color-scheme`, with a toggle in settings.
- **Empty states and errors:** every page has a helpful empty state. Every API error becomes a human message with a retry button. Never show a raw stack trace.

## Code conventions
- Type everything: Python type hints with Pydantic models at every API boundary, and TS `strict`.
- The API returns JSON with stable shapes, documented in `BUILD.md` §5. Don't rename fields without updating the frontend types in `frontend/src/lib/api.ts`.
- Keep functions small and pure in `pipeline/`, so they can be unit-tested with Jev and the LLM mocked.
- Every pipeline change needs a test in `backend/tests/`. Every bug fix gets a regression test.
- Commit small and often, with clear messages (`feat(pipeline): claim support via Jev`). Judges read the commit history, and each teammate commits their own work.

## Licensing and credit
- The repo is Apache-2.0. Use only MIT, BSD, Apache or MPL code. **No AGPL code** (MiroFish and its forks): the Audience Preview borrows MiroFish's *idea*, not its code.
- Credit everything reused in `NOTICE.md`:
  - shadcn/ui, Magic UI
  - packfix's SIFT + RANSAC method
  - brand-fidelity-rules templates
  - jev-adrank, a reference Jev integration
  - NVIDIA Nemotron-Personas-India (CC-BY-4.0, attribution required)
  - Docling or pdfplumber, textstat, OpenCV
- Disclose AI assistance in the README, as the hackathon rules require.

## Don'ts
- Don't add features that aren't in `BUILD.md` without a team decision.
- Don't call Jev or the LLM from the frontend. Keys stay on the server.
- Don't block the request thread on long work. Checks run as background tasks that stream progress over SSE.
- Don't store uploaded files larger than 10 MB or PDFs longer than 20 pages; reject them with a clear message.
- Don't claim accuracy numbers anywhere unless they come from `app/eval/results/latest.json`.
