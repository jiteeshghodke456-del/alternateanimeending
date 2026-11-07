# Things Jiteesh needs to provide or approve

Agents add items here instead of installing anything on Jiteesh's computer or guessing secrets.

## Accounts and keys (never commit; set as Hugging Face Space secrets)
- [ ] TYPESAFE_API_KEY (Jev)
- [ ] LLM_API_KEY + LLM_BASE_URL + LLM_MODEL (Groq / OpenRouter / Gemini, OpenAI-compatible)
- [ ] Hugging Face account to host the Space (or Render)
- [ ] Public GitHub repo to push to

## To run locally on the Omen 16 (only if you want to)
- [ ] Python 3.12, Node 20+, Docker Desktop (optional)

## Seed data (demo brand) - human follow-ups
- Samiksha (or anyone who didn't write the rules) must re-label the 18 test items in `backend/app/eval/dataset.jsonl` before the final eval run. The current labels are drafts (`labelled_by: draft-ai`).
- Eval label convention: `F-12/F-13/F-14` mark claims that rely on the 3 expired facts; all other labels are rule codes.
- `guideline.pdf` is generated (`backend/app/seed/build/build_guideline.py`, needs Fraunces/Inter TTFs in ~/.fonts). Samiksha may rewrite it in her own words; if she does, re-run `python backend/app/seed/validate_seed.py` so every rule's `source_quote` still matches its page.
- personas.json: downloaded from nvidia/Nemotron-Personas-India (en_IN) via the datasets-server API. The dataset has no city tier or student flag, so "tier-2/3" = rural or non-metro urban, and "students" = age 17-24, higher-secondary/graduate education, persona text mentions study. Keep the CC-BY-4.0 credit in NOTICE.md.
- `content/items.json`: the demo item has title "Monsoon caption (demo_flawed_post)" plus an extra `"key": "demo_flawed_post"` field; loaders should find it by `key`.

## Backend scaffold - follow-ups
- [ ] Set `SESSION_SECRET` (long random string) as a Space secret. Without it the insecure dev default is used.
- [ ] HF Space README frontmatter must include `sdk: docker` and `app_port: 7860` (README owner).
- [ ] The Docker image was NOT built in the agent sandbox (no Docker daemon there). First `docker build` / Space build must be checked by a human or a CI run.
