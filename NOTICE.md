# NOTICE

| Component | Licence |
|---|---|
| NVIDIA Nemotron-Personas-India (`backend/app/seed/personas.json`, sampled and shortened) | CC-BY-4.0 |
| TypeSafe skill (`.claude/skills/typesafe-ai/SKILL.md`) | MIT |
| OpenCV (`opencv-python-headless`) | Apache-2.0 |
| pdfplumber | MIT |
| rapidfuzz | MIT |
| Pillow | HPND |
| NumPy | BSD-3-Clause |
| pyphen | MPL-1.1 (tri-licensed GPL-2.0+/LGPL-2.1+/MPL-1.1) |
| textstat | MIT |
| FastAPI | MIT |
| SQLModel | MIT |
| Pydantic, pydantic-settings | MIT |
| sse-starlette | BSD-3-Clause |
| uvicorn | BSD-3-Clause |
| typesafe-sdk | MIT |
| openai (Python client) | Apache-2.0 |
| shadcn/ui | MIT |
| Magic UI | MIT |
| React | MIT |
| Tailwind CSS | MIT |
| framer-motion | MIT |
| recharts | MIT |
| lucide-react | ISC |
| Inter font | SIL Open Font License 1.1 |

## Ideas and methods we borrowed (no code copied)

| Source | What we took |
|---|---|
| packfix | The method for logo fidelity: SIFT keypoints matched to the approved logo, then a RANSAC homography |
| jev-adrank | A reference for calling Jev with typed questions |
| brand-fidelity-rules | Templates for writing brand rules as single checkable statements |
| MiroFish | The idea of a simulated audience reacting to content. MiroFish is AGPL-3.0; none of its code is used |

## Public documents quoted in the India regulatory pack

`backend/app/seed/packs/india_regulatory.json` paraphrases and quotes short phrases from public documents, with links to the official sources (read 7 Nov 2026): Guidelines for Prevention and Regulation of Dark Patterns, 2023 and Guidelines for Prevention of Misleading Advertisements and Endorsements for Misleading Advertisements, 2022 (Central Consumer Protection Authority, Government of India); the Consumer Protection Act, 2019; the Information Technology (Intermediary Guidelines and Digital Media Ethics Code) Rules, 2021 as amended on 10 Feb 2026 (MeitY); the ASCI Code for Self-Regulation of Advertising Content and the ASCI Guidelines for Influencer Advertising in Digital Media (© Advertising Standards Council of India). Not legal advice.

## Test fixtures

`backend/tests/fixtures/provenance/c2pa-rs_C.jpg` is from contentauth/c2pa-rs (MIT OR Apache-2.0, © 2020 Adobe), used unchanged.

