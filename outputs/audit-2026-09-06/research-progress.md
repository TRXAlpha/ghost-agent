# Research retrieval verification — 6 September 2026

This increment improves source identity and discovery; it does not establish autonomous research reliability.

## Implemented and tested

- Scholarly host ranking uses parsed host boundaries, not substring matches. Lookalike hosts, credentials in URLs, invalid ports, and malformed addresses cannot acquire a trusted publisher's rank. Wikipedia remains tertiary rather than receiving the generic organization rank.
- OpenAlex records retain work, DOI, venue, publisher, and retrieval-service identities. Retracted records are excluded. DOI/work aliases prevent counting copies of the same paper as distinct evidence.
- Discovery searches both academic and web channels. Source limits use publisher/venue identity when available instead of treating every DOI landing URL as the same publisher. This is a diversity proxy, not proof of independent experiments.
- Missing indexed abstracts remain metadata-only discovery leads. They carry no evidence text and must yield acceptable fetched text before entering the claim pipeline.
- Fetched HTML is labelled full text, and its final redirected URL is ranked again. Failed fetches and low-ranked redirects cannot become evidence merely because the original search record was scholarly.
- Research provenance retains retrieval and scholarly identity metadata. Exact-quotation and evidence-gap gates remain in place.

## Verification

Full suite after this increment: **266 passed in 8.15 seconds**. `python -m compileall -q ghost_agent tests` exited successfully.

Regression coverage includes lookalike hosts, DOI/work deduplication, different publishers sharing doi.org, simultaneous academic/web discovery, missing abstracts, failed fetches, and low-quality redirects. Pipeline cases use scripted model responses; they are not live-model benchmarks.

A read-only live OpenAlex smoke query for `carbon cycling` returned HTTP 200. Its first three works had no indexed abstract. After the fix, all three remained discovery leads with `source_kind=metadata_only`, and none contained evidence text. No full-text retrieval or supported research conclusion was established by that smoke check.

API field references: [OpenAlex work attributes](https://help.openalex.org/data/works/attributes/), [locations](https://help.openalex.org/data/locations/), and [source attributes](https://help.openalex.org/data/sources/attributes/).

## Remaining boundaries

HTML downloads still need byte limits and a genuine network-policy boundary, including redirect/private-network protection. PDF retrieval, citation chaining, stronger conflict analysis, and repeatable factuality benchmarks remain incomplete. Source scores are transparent heuristics, not guarantees of truth. Publisher diversity does not establish methodological independence. No installed-model reliability claim follows from these unit tests.

Follow-up: [bounded source downloads](research-download-limits.md) implements HTML body limits and bounded, body-free redirect handling. Network authorization, total download deadlines, PDF support, and broader retrieval limits remain incomplete.
