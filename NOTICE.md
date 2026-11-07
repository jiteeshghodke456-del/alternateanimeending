# NOTICE

Brand Gate is licensed under Apache-2.0. It builds on the following work. Teammates: add an entry
here when you reuse code, data or a documented method.

## Data
- **NVIDIA Nemotron-Personas-India** (CC-BY-4.0, attribution required).
  <https://huggingface.co/datasets/nvidia/Nemotron-Personas-India>.
  `backend/app/seed/personas.json` holds 240 personas sampled from the en_IN split (stratified into 6
  segments, persona text shortened; see `backend/app/seed/build/build_personas.py`). They are used by
  the Audience Preview (P1-1), which simulates audience reactions. Its results are a directional
  signal, not a survey. Changes: sampling, field selection, shortening and a `segment` label.

## Methods
- **packfix** (MIT): the logo-fidelity method of SIFT keypoints, Lowe's ratio test (0.75) and a RANSAC
  homography with an inlier count. `backend/app/pipeline/logo.py` is our own implementation of this
  method; no packfix code is copied. Our additions are a geometry check on the projected outline,
  a stretch check, and a CIEDE2000 colour check inside the logo.
- **TypeSafe skill** (`.claude/skills/typesafe-ai/SKILL.md`, MIT, vendored from typesafe-ai/skills):
  the guidance we follow for Jev questions.

## Libraries
- OpenCV (`opencv-python-headless`, Apache-2.0): SIFT, RANSAC homography and k-means for logo fidelity.
- pdfplumber (MIT): PDF text with page numbers (content units and guideline import).
- rapidfuzz (MIT): quote verification and fact matching.
- Pillow (HPND), NumPy (BSD-3-Clause), pyphen (GPL-2.0+/LGPL-2.1+/MPL-1.1 tri-licence, used under MPL-1.1), textstat (MIT).
- FastAPI, SQLModel, Pydantic, sse-starlette, uvicorn (MIT/BSD).
- typesafe-sdk (the Jev client) and openai (Apache-2.0; the OpenAI-compatible client for DeepSeek).
- Frontend: shadcn/ui (MIT) and Magic UI components (MIT), React, Tailwind CSS, framer-motion, recharts and lucide-react.

## Not used
- No AGPL code. The Audience Preview borrows MiroFish's *idea* (simulated audience reactions) but none
  of its code.
