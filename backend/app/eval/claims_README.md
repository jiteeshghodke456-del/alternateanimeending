# Claim-flagging eval set

`claims_dataset.jsonl` (45 items, 46 labels) measures whether each claim in a piece of marketing copy gets the
band a reviewer would expect. Runner: `python -m app.eval.claims_run` (see its docstring); results go to
`results/claims-<tag>.json`.

**Labels are AI-assisted draft labels, to be reviewed by the team.** They were written by the coding agent
(`labelled_by: "draft-ai"`) before the gate was built, and not changed after seeing results.

## Sources
- `source: "swiggy_blog"` (25 items): sentences copied verbatim from Swiggy Instamart's public blog post
  "How India Instamarted 2025" (a brand's own year-in-review), extracted from the saved HTML with Python. Used only
  as test input; the text belongs to Swiggy.
- `source: "synthetic"` (20 items): sentences written for this set to mimic typical Indian D2C ads (sunscreen,
  protein bars, kurtas, delivery apps). No real brand.

Nothing was downloaded for this set.

## Brand context used by the runner
- Rules: the India pack's IN-ASCI-02 ("Best", "No. 1" and other rankings need evidence) and R-EVAL-NUM, a generic
  brand rule like the demo brand's R-CLAIM-02 ("every number, rating, price, offer, delivery time and health or
  safety claim must match a current approved fact"). This is the setting where number flagging was too aggressive.
- Facts: `claims_facts.csv` (X-01..X-05, stand-ins written for this eval). An item only sees the facts it lists in
  `facts`, so atomic coverage can be tested ("Rajkot 10x" is covered by X-01, "Ludhiana 7x" is not).

## Labelling policy
Each label is a span (usually the figure) with an expected band and the claim's kind:
- `flag`: a ranking or comparison, a service promise (delivery time, price, discount, free, guarantee, what the
  product contains), or a health or safety claim, with no matching approved fact. The rules above ask for evidence.
- `human`: the brand reporting its own data (order counts, peaks, growth, spends) with no matching fact: a reminder
  to verify, not a violation.
- `none`: opinion, mood or story (no claim), or a figure an approved fact covers.
An item with no labels expects no claim finding at all. A claim finding that overlaps no label counts as a
false alarm.

## Known judgment calls
- "Mondays emerged as the ultimate gifting day" and "Handcrafted in small batches in Jaipur" are labelled `none`;
  a reviewer could reasonably want a reminder for either.
- "₹500 crores saved" and "a ₹1,00,000 cart for ₹7,000" are labelled as the brand's own data (`human`), not
  savings promises.
- Health claims without a number ("Boosts immunity") are labelled `flag`.
