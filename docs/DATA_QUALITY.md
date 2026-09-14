# LLIS data quality report

Generated offline from the public NASA LLIS snapshot received at **2026-09-12T08:27:01.266535+00:00**.
This describes the backed-up search response, not all knowledge held by NASA.

## Record reconciliation

| Item | Count |
| --- | ---: |
| Raw search hits | 2,127 |
| Records with a valid, unique lesson number | 2,117 |
| Quarantined records without a valid lesson number | 10 |
| Distinct source field names across all hits | 44 |

Of the quarantined records, 10 contain search-query payloads. Their complete raw hits and original locations are retained in `quarantine.jsonl`. Nothing has been removed from the raw backup. A lesson number identifies a record; it does not certify the completeness or correctness of its text.

The count of 2,127 refers to **index records**, not that many lessons. The response is fully backed up according to its count and checksum checks, but article content can still be absent and separately linked files were never included. In this snapshot, 2,117 lessons parsed without a fatal extraction failure and 2,117 have source text in at least one core section; generated media markers alone do not qualify as source text.

## Missing or placeholder content

These counts are after HTML rendering, exact whole-field placeholder removal, and documented legacy fallback. A placeholder such as `None` is not indexed as a claim. Sentences beginning with “None of …” are retained. Missing titles receive a clearly generated display label; no NASA title is invented.

| Field or section | Lessons without content |
| --- | ---: |
| `title` | 12 |
| `abstract` | 1,087 |
| `driving_event` | 32 |
| `lesson` | 16 |
| `recommendation` | 24 |
| `evidence` | 1,161 |
| `related_policy` | 1,988 |

## New and legacy fields

| Section | Both fields present | Equivalent rendered content | Different nonempty variants | One variant empty |
| --- | ---: | ---: | ---: | ---: |
| `driving_event` | 2,005 | 1,835 | 168 | 2 |
| `lesson` | 2,005 | 1,962 | 42 | 1 |

There are **210 differing field pairs in 182 lessons**. These are differences, not necessarily factual contradictions. Current UI fields are selected first, with legacy fallback only when the current field has no content. Different nonempty alternatives, including their text, tables, references and provenance, are saved in `field_variants.jsonl.gz`; they are not concatenated into the main article.

For example, lesson 2456's event and lesson 1033's lesson text contain apparent broken quotation-entity spelling in the UI field while their legacy alternatives use quotation marks. No speculative text repairs are applied. Field selection is a reproducible rule, not a claim that the UI version is always better. These examples and the full variant file should be reviewed before choosing retrieval behavior.

## Review signals and extraction limits

Counts below are distinct lessons per flag; a lesson can appear in multiple rows. Missing evidence is often a source placeholder, not an extraction failure. Parser diagnostics can be recoverable markup errors; they do not automatically mean text was lost. Conversely, successful parsing does not prove fidelity to the original page layout.

| Signal | Lessons |
| --- | ---: |
| `attachment_content_not_loaded` | 92 |
| `field_variant` | 182 |
| `html_parser_diagnostics` | 42 |
| `media_content_not_loaded` | 380 |
| `missing_section` | 2,032 |
| `missing_title` | 12 |
| `possible_broken_quote_entity` | 110 |
| `same_body_text_as_other_lesson` | 13 |
| `table_requires_chunking_policy` | 134 |
| `unrecognized_html_tags` | 2 |

Tables retain rows, header cells and raw rowspan/colspan attributes, with cell-separated text for inspection. They still require a table-aware chunking decision. Subscripts and superscripts use explicit `_(...)` and `^(...)` markers. Images/media retain source references and alt text only: their contents have not been downloaded, OCRed or interpreted. Links and attachments are references with `not_downloaded` status, not evidence that their targets were retrieved or even remain accessible.

Unrecognized tags, parser messages and possible `quot` encoding damage remain in the audit. The quote detector is a review heuristic and can have false positives or miss other corruption. No semantic correction, image interpretation or external-document extraction was performed. Identical normalized body text across different IDs is flagged and retained, not merged; metadata or media can differ.

## Size and next step

The selected title plus five core sections total approximately **1,131,637 whitespace-separated units** (median 343, maximum 8,044 per lesson; 136 lessons exceed 1,500). This includes generated table/media markers, excludes related-policy text, metadata and alternate fields, and **is not a model-token count**. Do not estimate embedding costs from all legacy and current fields concatenated together.

This commit completes normalization and its audit, not chunking or RAG. Before producing chunks, select a policy for differing fields, tables and missing media; use a small set of source-checked questions to evaluate retrieval. A useful first scope is the available text, with media-dependent questions explicitly identified as unsupported. No need to claim the entire collection is clean or to fetch every linked file first.

## Reproduce and inspect

```sh
python -m pip install -r requirements-data.txt
python scripts/normalize_llis.py
python -m unittest discover -s tests -v
```

Outputs: [`data/processed/llis/2026-09-12T082701Z/`](../data/processed/llis/2026-09-12T082701Z/). The manifest records input/output SHA-256 checksums, parser versions, script hash and all counts. Each selected field and alternative points to its original raw field and records its UTF-8 hash. Full source HTML stays in the immutable raw snapshot.

See [preparation rules and schema](DATA_PREPARATION.md) and [raw backup scope](../data/raw/llis/README.md). Public source: [NASA LLIS](https://llis.nasa.gov/).
