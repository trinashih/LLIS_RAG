#!/usr/bin/env python3
"""Back up NASA's public LLIS search response without transforming lessons.

Standard library only. Download: python scripts/backup_llis.py
Offline verification: python scripts/backup_llis.py --verify data/raw/llis/<snapshot>
"""

import argparse
from datetime import datetime, timezone
import gzip
import hashlib
import json
from pathlib import Path
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


ENDPOINT = "https://llis.nasa.gov/llis/lesson/_search"
ROOT = Path(__file__).resolve().parents[1]
ARCHIVE = "llis_raw_response.json.gz"


def digest(data):
    return hashlib.sha256(data).hexdigest()


def total_hits(response):
    if response.get("error") or response.get("timed_out"):
        raise ValueError("LLIS returned an error or timed out")
    shards = response.get("_shards", {})
    if shards.get("failed", 0) or shards.get("successful") != shards.get("total"):
        raise ValueError("LLIS shard results are incomplete")
    total = response["hits"]["total"]
    if isinstance(total, dict):
        if total.get("relation") != "eq":
            raise ValueError("LLIS returned a lower bound, not an exact count")
        total = total["value"]
    if not isinstance(total, int) or total <= 0:
        raise ValueError("LLIS returned no records")
    return total


def request(payload):
    req = Request(
        ENDPOINT,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "User-Agent": "LLIS-RAGrets/0.1 (public-records backup)"},
        method="POST",
    )
    for attempt in range(3):
        try:
            with urlopen(req, timeout=45) as response:
                raw = response.read()
                headers = {k: response.headers[k] for k in ("Date", "Content-Type", "ETag", "Last-Modified") if response.headers.get(k)}
            break
        except HTTPError as error:
            if error.code not in (408, 429, 500, 502, 503, 504) or attempt == 2:
                raise
            print(f"Temporary NASA HTTP {error.code}; retry {attempt + 1}/2", flush=True)
        except (URLError, TimeoutError):
            if attempt == 2:
                raise
            print(f"Temporary connection failure; retry {attempt + 1}/2", flush=True)
        time.sleep(2 ** (attempt + 1))
    parsed = json.loads(raw)
    total_hits(parsed)
    return raw, parsed, headers


def validate_records(parsed, expected):
    hits = parsed["hits"]["hits"]
    if total_hits(parsed) != expected or len(hits) != expected:
        raise ValueError("Returned record count does not match the exact total")
    ids = [str(h["_id"]) for h in hits]
    if len(set(ids)) != expected:
        raise ValueError("Duplicate document IDs found")
    if any(not isinstance(h.get("_source"), dict) or not h["_source"] for h in hits):
        raise ValueError("Missing original source object")
    return hits


def verify(folder):
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    packed = (folder / ARCHIVE).read_bytes()
    raw = gzip.decompress(packed)
    for name, data in (("compressed", packed), ("uncompressed", raw)):
        expected = manifest["files"][name]
        if len(data) != expected["bytes"] or digest(data) != expected["sha256"]:
            raise ValueError(f"{name} size or SHA-256 mismatch")
    hits = validate_records(json.loads(raw), manifest["record_count"])
    print(f"VERIFIED: {len(hits)} unique records; gzip and original JSON SHA-256 match.")
    return manifest


def backup():
    started = datetime.now(timezone.utc)
    _, before, _ = request({"query": {"match_all": {}}, "size": 0})
    count = total_hits(before)
    print(f"Public index reports {count} records; downloading full response...", flush=True)
    if count > 10000:
        raise ValueError("Corpus exceeds this downloader's 10,000-result window; implement pagination first")
    payload = {"query": {"match_all": {}}, "from": 0, "size": count}
    raw, parsed, headers = request(payload)
    received = datetime.now(timezone.utc)
    hits = validate_records(parsed, count)
    print(f"Downloaded {len(raw):,} bytes; checking post-download total...", flush=True)
    _, after, _ = request({"query": {"match_all": {}}, "size": 0})
    if total_hits(after) != count:
        raise ValueError("Corpus count changed during download; retry to make a new snapshot")
    packed = gzip.compress(raw, compresslevel=9, mtime=0)
    if gzip.decompress(packed) != raw:
        raise ValueError("Lossless compression verification failed")
    folder = ROOT / "data" / "raw" / "llis" / received.strftime("%Y-%m-%dT%H%M%SZ")
    manifest = {
        "schema_version": 1,
        "source": "NASA public Lessons Learned Information System (LLIS)",
        "source_url": "https://llis.nasa.gov/",
        "endpoint": ENDPOINT,
        "method": "POST (read-only search)",
        "request_body": payload,
        "download_started_utc": started.isoformat(),
        "response_received_utc": received.isoformat(),
        "response_headers": headers,
        "record_count": count,
        "unique_document_ids": len({h["_id"] for h in hits}),
        "count_before": total_hits(before),
        "count_after": total_hits(after),
        "source_fields": sorted({key for h in hits for key in h["_source"]}),
        "files": {
            "compressed": {"path": ARCHIVE, "bytes": len(packed), "sha256": digest(packed)},
            "uncompressed": {"path": "llis_raw_response.json", "bytes": len(raw), "sha256": digest(raw)},
        },
        "scope": "All records returned by the public LLIS lesson index at download time; includes the full search response and every _source field.",
        "transformations": "None. Raw HTTP response bytes are preserved with lossless gzip compression; HTML and duplicate/legacy fields remain unchanged.",
        "exclusions": ["Separately linked attachments, PDFs, images and videos", "Non-public NASA records", "Other NASA databases"],
        "limitations": ["Count checks are not a transactional database snapshot", "Source availability and public endpoint behavior can change", "Publicly accessible does not imply every field is accurate or complete"],
    }
    folder.mkdir(parents=True, exist_ok=False)
    (folder / ARCHIVE).write_bytes(packed)
    (folder / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (folder / "SHA256SUMS").write_text(f"{digest(packed)}  {ARCHIVE}\n", encoding="ascii")
    verify(folder)
    print(json.dumps({"snapshot": str(folder.relative_to(ROOT)), "record_count": count, "raw_bytes": len(raw), "gzip_bytes": len(packed)}, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify", type=Path, metavar="SNAPSHOT_DIRECTORY", help="Verify an existing backup offline without downloading")
    args = parser.parse_args()
    if args.verify:
        verify(args.verify)
    else:
        backup()
