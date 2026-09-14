# NASA LLIS raw backup

This directory holds dated, losslessly compressed snapshots of NASA's public
[Lessons Learned Information System](https://llis.nasa.gov/). The backup is kept
in this repository so development can reuse the same source data without
repeatedly downloading it from NASA.

## Initial snapshot

[`2026-09-12T082701Z/`](2026-09-12T082701Z/) was downloaded on September 12,
2026 at 08:27:01 UTC (01:27:01 PDT).

| Measure | Value |
| --- | ---: |
| Returned records / unique document IDs | 2,127 / 2,127 |
| Count before / after download | 2,127 / 2,127 |
| Original JSON response | 16,233,143 bytes |
| Losslessly compressed archive | 3,146,966 bytes |

The record count describes the public index at that time, not a permanent NASA
total or a count of all separately linked attachments.

## Contents of each snapshot

- `llis_raw_response.json.gz`: the complete, unmodified JSON response from the
  public LLIS lesson search endpoint, including every returned record and field.
- `manifest.json`: source URL, exact query, UTC retrieval time, record counts,
  field names, file sizes, SHA-256 checksums, and scope limitations.
- `SHA256SUMS`: checksum of the compressed archive.

This is the public LLIS **record corpus**, not a backup of every NASA document.
The original HTML stored in fields is retained. Linked PDFs, images, videos,
and other attachments are not downloaded. No OCR, text cleanup, chunking,
embeddings, or generated interpretations have been applied.

## Reuse the backup offline

Clone this private repository using an account with access. The following uses
only Python's standard library; no model, API key, or GPU is required:

```python
import gzip
import json
from pathlib import Path

archive = sorted(Path("data/raw/llis").glob("*/llis_raw_response.json.gz"))[-1]
with gzip.open(archive, "rt", encoding="utf-8") as stream:
    response = json.load(stream)

records = response["hits"]["hits"]
lessons = [record["_source"] for record in records]
print(len(lessons))
```

Keep the original `records` if you need Elasticsearch document IDs or other
response metadata. Some fields may be legacy/duplicate representations of the
same text. Do not concatenate all fields and treat that as a deduplicated
word or token count.

Verify a snapshot without contacting NASA:

```sh
python scripts/backup_llis.py --verify data/raw/llis/<snapshot-directory>
```

The verifier checks both compressed and decompressed SHA-256 checksums, exact
record count, unique document IDs, and successful search shards.

Only if a fresh snapshot is actually needed:

```sh
python scripts/backup_llis.py
```

The script uses the read-only search endpoint called by the public LLIS website.
This endpoint is not a guaranteed or versioned public API. The script stops if
the corpus exceeds the current single-request result window, if counts change
during retrieval, or if the response is incomplete. Successful count checks do
not guarantee a transactional snapshot of a live database.

## Retention and source attribution

These are NASA-origin public records, not original project-authored lessons.
The repository's MIT license must not be assumed to relicense source material
or any referenced third-party material. Preserve NASA provenance and any
source notices when deriving a RAG corpus.

The backup may be removed from the current branch after development if it is no
longer needed. An ordinary Git deletion leaves the earlier copy in Git history;
removing that history would be a separate, deliberate operation.

Independent educational project. Not affiliated with or endorsed by NASA.
