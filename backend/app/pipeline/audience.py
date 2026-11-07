"""P1 Audience Preview (MiroFish-style idea, no MiroFish code). OWNER: pipeline-agent.

Intended API:

    def load_personas() -> list[dict]       # seed/personas.json via app.seed.seed_file("personas.json")
    async def run_audience(version_id: int) -> None
        240 personas in chunks of 20; one Jev call per chunk with state={content, personas} and
        2 Nouls per persona: p{i}_distrust, p{i}_confused. Run in parallel. Store AudienceRun.results:
        {"personas": [{id, segment, distrust, confused}], "segments": [{name, distrust_pct,
        confused_pct, worst: [persona ids], line_idx}]}. Never for confidential items.
    Always labelled "Simulated audience, directional signal, not a survey".
"""
from __future__ import annotations


async def run_audience(version_id: int) -> None:
    raise NotImplementedError("pipeline-agent: implement audience (P1)")
