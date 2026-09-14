# LLIS preparation rules

The normalized data is a reproducible view of the saved public search response. It is not a replacement for the raw snapshot and does not claim that every article, attachment or figure is complete.

## Run locally

Use Python 3.10 or newer. No model, API key, GPU or cloud server is needed for this preparation step.

```sh
python -m venv .venv
# macOS/Linux:
source .venv/bin/activate
# Windows PowerShell: .venv\Scripts\Activate.ps1
python -m pip install -r requirements-data.txt
python scripts/normalize_llis.py
python -m unittest discover -s tests -v
```

The script reads only the saved snapshot. It verifies compressed and uncompressed SHA-256 hashes, byte counts, response completeness and record counts before processing. It performs no network requests. Inputs are never rewritten, and output paths inside `data/raw` are rejected.

The default snapshot is `2026-09-12T082701Z`. For another backed-up snapshot within this repo, pass `--snapshot data/raw/llis/<timestamp> --output data/processed/llis/<timestamp> --report docs/DATA_QUALITY.md`. The report includes observations specific to the initial snapshot; review its narrative when replacing the dataset. JSON statistics are computed on every run.

## Field selection

| Normalized field | Source fields in preference order |
| --- | --- |
| `lesson_id` | `lesson_number`, converted to a string |
| `title` | `title` |
| `sections.abstract` | `lessonAbstract` |
| `sections.driving_event` | `drivingEvent`, then `description_event` |
| `sections.lesson` | `lesson`, then `lesson_learned` |
| `sections.recommendation` | `recommendation` |
| `sections.evidence` | `evidence` |
| `sections.related_policy` | `relatedPolicy` |
| `lesson_date` | Validated ISO `lesson_date`; compared with `lessonDate` |
| `metadata_raw` | Every source field other than the title and section fields above, unchanged |

Selection uses the first field with rendered text, a table or media. Empty strings and exact whole-field `None`, `N/A`, `Not Applicable` and `null` placeholders are treated as absent, ignoring case and surrounding whitespace. Substantive sentences are retained. No spelling correction, summarization or model rewriting occurs.

When current and legacy fields render identically, only one enters the canonical article. When both have nonempty but different rendered content, the selected and alternate values are retained in the variants file. Comparison includes text, structured tables, link references and media references, not just word counts. Differences can be formatting, encoding or substantive changes; they have not been adjudicated. The current-field preference can be changed in a future reviewed version of this pipeline.

Records without a numeric lesson number go to quarantine, with the entire raw hit. Duplicate lesson numbers cause the run to fail rather than silently overwrite an article. All identified lessons remain in the output even with missing titles or sections. A generated `display_title` is explicitly marked and must not be represented as a NASA-authored title.

## Text and HTML handling

HTML is parsed with the pinned [lxml HTML parser](https://lxml.de/lxmlhtml.html). Its recovery behavior and the installed libxml version can affect malformed HTML, so both versions are recorded. An input is parsed once; entities are not repeatedly decoded. Script, style, head content and HTML comments do not enter article text. Unknown tags retain their text and create a review signal. Parser errors are retained. If parsing fails, the source string remains available and `text_extraction_succeeded` becomes false.

Paragraphs and lists receive line boundaries; ordered list numbering is retained where valid. Inline text remains joined as in the source. Superscript and subscript content uses `^(...)` and `_(...)`, preventing `10<sup>3</sup>` from becoming `103`.

Tables have separate row/cell objects, header flags, captions, and the original string-valued `rowspan`/`colspan` attributes. The text view uses ` | ` cell separators and explicit span markers. It does not expand spans into a rectangular grid or guarantee that a later plain-text chunker can answer table questions correctly. Generated `[Table …]`, `[Image …]` and `[Media …]` labels are extraction annotations, not NASA prose.

Links, images, embedded media, `attachments`, and legacy `documentUrl0`–`documentUrl3` entries retain references and source metadata. Relative references resolve against `https://llis.nasa.gov/`, a candidate URL that has not been fetched or validated. Non-HTTP schemes retain the original reference and have a null resolved URL. Source alt text is labeled as such; no image contents are inferred. There is no PDF extraction, OCR or link availability check in this step.

## Outputs and provenance

Files are in `data/processed/llis/<timestamp>/`:

| File | Contents |
| --- | --- |
| `lessons.jsonl.gz` | One normalized record per unique lesson number, sorted numerically |
| `field_variants.jsonl.gz` | Different nonempty current/legacy field pairs with full rendered alternatives |
| `quarantine.jsonl` | Non-lesson hits, exclusion reasons and complete source records |
| `quality_issues.jsonl.gz` | Per-lesson review signals with affected sections and details |
| `manifest.json` | Source/output hashes, file sizes, counts, parser versions, script hash and quality-report hash |

Every lesson includes its raw snapshot path/hash, original document ID and JSON Pointer to the raw hit. Its public lesson URL is derived from the ID and is not a link-health assertion. Every selected text field and alternate records its exact original field name, JSON Pointer and SHA-256 of the original UTF-8 string. Full source HTML can therefore be retrieved without guessing which duplicate field was used. All remaining source values are preserved unchanged in `metadata_raw`; placeholders in that object are source values, not cleaned retrieval text.

`classification` in this dataset is source taxonomy, not an inferred security classification. `restriction` notices, organizations, categories and dates are preserved without interpreting or silently replacing them. `metadata_raw` should not be blindly concatenated into embedding input.

Each section's `source_text_present` identifies non-placeholder source text; generated media markers alone do not qualify. `has_core_body_text` and `text_extraction_succeeded` describe extraction only. They do not certify factual accuracy, complete media coverage or readiness for every question. Quality flags remain available for the next chunking policy. The quality report counts lessons per flag; several flags may apply to one lesson. Alternative-field parser messages are in the variants file, while main-record flags describe the selected fields.

Compressed JSONL is UTF-8, one complete JSON object per line, with deterministic gzip headers. Reruns with the same source, code and parser build produce the same output bytes. The automated check reruns the pipeline into a temporary directory and compares all generated files and the report. Parser upgrades can legitimately change outputs and require review.

```python
import gzip
import json

with gzip.open("data/processed/llis/2026-09-12T082701Z/lessons.jsonl.gz", "rt", encoding="utf-8") as stream:
    for line in stream:
        lesson = json.loads(line)
        # Read sections individually; do not concatenate metadata or variants.
        print(lesson["lesson_id"], lesson["display_title"])
```

Chunking, embeddings, retrieval evaluation and answer generation are subsequent steps. The raw backup still excludes linked files and non-public NASA material; normalization does not change that scope.
