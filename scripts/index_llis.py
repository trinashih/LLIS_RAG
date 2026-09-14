"""English LLIS retrieval: deterministic documents, Gemini API, cached vectors, Qdrant.

No API key is stored. Preparing documents and rebuilding Qdrant are offline.
Only GeminiClient sends network requests, to the fixed Google API host.
"""

from collections import Counter
from contextlib import closing
import gzip
import hashlib
import json
import math
import os
from pathlib import Path
import re
import time
import urllib.error
import urllib.request
import uuid
import zipfile

import numpy as np
from qdrant_client import QdrantClient, models

MODEL = "gemini-embedding-2"
DIMENSIONS = 3072
POLICY_VERSION = "llis-whole-lesson-v1"
SECTION_ORDER = ("abstract", "driving_event", "lesson", "recommendation", "evidence", "related_policy")
REFERENCE_ONLY = {"same as in lesson learned", "see lesson(s) learned", "see lesson learned"}
TABLE = re.compile(r"\[Table (\d+)\][\s\S]*?\[End table \1\]")


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(data):
    return hashlib.sha256(data).hexdigest()


def atomic_write(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp-" + uuid.uuid4().hex)
    try:
        with temporary.open("wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def save_json(path, value):
    atomic_write(path, (canonical(value) + "\n").encode())


def load_lessons(directory):
    directory = Path(directory)
    manifest = json.loads((directory / "manifest.json").read_text())
    info = manifest["files"]["lessons.jsonl.gz"]
    data = (directory / "lessons.jsonl.gz").read_bytes()
    if digest(data) != info["sha256"] or len(data) != info["bytes"]:
        raise ValueError("Normalized dataset checksum/size mismatch; restore the repo files.")
    lessons = [json.loads(line) for line in gzip.decompress(data).splitlines()]
    if len(lessons) != info["records"] or len({l["lesson_id"] for l in lessons}) != len(lessons):
        raise ValueError("Lesson count or unique-ID validation failed.")
    if any(l["source"]["snapshot_sha256"] != manifest["source_snapshot_sha256"] for l in lessons):
        raise ValueError("Mixed source snapshots.")
    return lessons, info["sha256"]


def paragraph_ranges(text, start, end):
    for match in re.finditer(r"\S[\s\S]*?(?=\n\s*\n|\Z)", text[start:end]):
        a, b = start + match.start(), start + match.end()
        while b > a and text[b - 1].isspace():
            b -= 1
        if b > a:
            yield a, b, False


def block_ranges(text):
    position = 0
    for match in TABLE.finditer(text):
        yield from paragraph_ranges(text, position, match.start())
        yield match.start(), match.end(), True
        position = match.end()
    yield from paragraph_ranges(text, position, len(text))


def split_range(text, start, end, fits):
    """Lossless non-whitespace coverage; split oversized blocks at safe text boundaries."""
    while start < end:
        while start < end and text[start].isspace():
            start += 1
        if start == end:
            return
        if fits(text[start:end]):
            yield start, end
            return
        low, high = start, end
        while low < high:
            middle = (low + high + 1) // 2
            if fits(text[start:middle]):
                low = middle
            else:
                high = middle - 1
        if low == start:
            raise ValueError("Title/section header exceeds the chunk budget.")
        fragment = text[start:low]
        candidates = [m.end() for m in re.finditer(r"\n|(?<=[.!?])\s+", fragment)]
        boundary = next((n for n in reversed(candidates) if n >= len(fragment) // 2), None)
        if boundary is None:
            spaces = [m.start() for m in re.finditer(r"\s+", fragment)]
            boundary = next((n for n in reversed(spaces) if n > 0), len(fragment))
        finish = start + boundary
        yield start, finish
        start = finish


def make_plan(lessons, dataset_sha, max_words=1200, max_input_bytes=20000):
    if max_words < 32 or max_input_bytes < 512:
        raise ValueError("Chunk budgets are too small.")
    config = {"model": MODEL, "dimensions": DIMENSIONS, "policy": POLICY_VERSION,
              "max_words": max_words, "max_input_bytes": max_input_bytes,
              "query_prefix": "task: search result | query: ",
              "document_format": "title: {title} | text: LLIS lesson {id}\\n\\n{sections}"}
    chunks, excluded = [], []
    for lesson in lessons:
        if not lesson["text_extraction_succeeded"] or not lesson["has_core_body_text"]:
            excluded.append({"lesson_id": lesson["lesson_id"], "reason": "extraction_or_body_missing"})
            continue
        sections = lesson["sections"]
        included, omitted = [], []
        for name in SECTION_ORDER:
            section = sections[name]
            text = section["text"]
            if not text:
                continue
            if re.sub(r"\s+", " ", text).strip(" .").casefold() in REFERENCE_ONLY:
                omitted.append(name)
                continue
            included.append({"section": name, "start": 0, "end": len(text), "split_large_block": False})
        if not included:
            excluded.append({"lesson_id": lesson["lesson_id"], "reason": "no_indexable_sections"})
            continue
        title = lesson["title"]["text"] or "none"
        prefix = f"title: {title} | text: LLIS lesson {lesson['lesson_id']}\n\n"

        def render(parts):
            return "\n\n".join(p["section"].replace("_", " ").upper() + "\n" +
                               sections[p["section"]]["text"][p["start"]:p["end"]] for p in parts)

        def fits_body(body):
            return len((prefix + body).split()) <= max_words and len((prefix + body).encode()) <= max_input_bytes

        groups = []
        if fits_body(render(included)):
            groups = [included]
        else:
            units = []
            for part in included:
                name = part["section"]
                text = sections[name]["text"]
                for start, end, is_table in block_ranges(text):
                    spans = list(split_range(text, start, end,
                        lambda value, name=name: fits_body(name.replace("_", " ").upper() + "\n" + value)))
                    for a, b in spans:
                        units.append({"section": name, "start": a, "end": b,
                                      "split_large_block": len(spans) > 1,
                                      "table_fragment": is_table and len(spans) > 1})
            current = []
            for unit in units:
                if current and not fits_body(render(current + [unit])):
                    groups.append(current)
                    current = []
                current.append(unit)
            if current:
                groups.append(current)
        for number, parts in enumerate(groups):
            text = render(parts)
            embedding_input = prefix + text
            if not fits_body(text):
                raise ValueError(f"Unbounded chunk in lesson {lesson['lesson_id']}")
            content_hash = digest(embedding_input.encode())
            key = digest(canonical([config, dataset_sha, lesson["lesson_id"], number, content_hash]).encode())
            spans = [{**p, "source": sections[p["section"]]["source"]} for p in parts]
            chunks.append({"id": str(uuid.UUID(key[:32])), "cache_key": key,
                "lesson_id": lesson["lesson_id"], "title": lesson["display_title"],
                "url": lesson["source"]["public_lesson_url"], "chunk_number": number,
                "whole_lesson": len(groups) == 1, "text": text, "embedding_input": embedding_input,
                "input_sha256": content_hash, "spans": spans, "omitted_reference_sections": omitted,
                "quality_flags": lesson["quality_flags"], "lesson_date": lesson["lesson_date"]})
    if len({c["id"] for c in chunks}) != len(chunks):
        raise ValueError("Duplicate point IDs.")
    counts = Counter(c["lesson_id"] for c in chunks)
    manifest = {"config": config, "dataset_sha256": dataset_sha,
                "chunks_sha256": digest(canonical(chunks).encode()),
                "input_lessons": len(lessons), "indexed_lessons": len(counts),
                "whole_lessons": sum(n == 1 for n in counts.values()),
                "split_lessons": sum(n > 1 for n in counts.values()),
                "chunks": len(chunks), "excluded": excluded,
                "embedding_input_bytes": sum(len(c["embedding_input"].encode()) for c in chunks),
                "table_fragments": sum(any(s.get("table_fragment") for s in c["spans"]) for c in chunks)}
    manifest["run_id"] = digest(canonical(manifest).encode())[:24]
    return {"manifest": manifest, "chunks": chunks}


def save_plan(plan, output_root):
    path = Path(output_root) / plan["manifest"]["run_id"]
    existing = path / "plan.json"
    if existing.exists() and json.loads(existing.read_text()) != plan["manifest"]:
        raise ValueError("Existing run has a different configuration.")
    save_json(existing, plan["manifest"])
    lines = "".join(canonical(c) + "\n" for c in plan["chunks"]).encode()
    atomic_write(path / "chunks.jsonl.gz", gzip.compress(lines, mtime=0))
    return path


def validate_vector(values, dimensions):
    vector = np.asarray(values, dtype=np.float32)
    if vector.shape != (dimensions,) or not np.isfinite(vector).all():
        raise ValueError("Invalid embedding shape or non-finite values.")
    norm = float(np.linalg.norm(vector))
    if not math.isfinite(norm) or norm <= 0:
        raise ValueError("Zero or invalid embedding norm.")
    return (vector / norm).tolist()


class GeminiClient:
    def __init__(self, api_key, dimensions=DIMENSIONS, min_interval=3.0, max_attempts=5):
        if not isinstance(api_key, str) or not api_key.strip():
            raise ValueError("Enable Notebook access for GEMINI_API_KEY in Colab Secrets.")
        if dimensions not in {768, 1536, 3072} or min_interval < 0 or max_attempts < 1:
            raise ValueError("Invalid Gemini client settings.")
        self._key = api_key.strip()
        self.dimensions = dimensions
        self.min_interval = min_interval
        self.max_attempts = max_attempts
        self._last_request = None

    def embed(self, texts):
        if not texts or len(texts) > 100:
            raise ValueError("Expected 1–100 independently embedded text inputs.")
        request_body = {"requests": [
            {"model": "models/" + MODEL, "content": {"parts": [{"text": text}]},
             "embedContentConfig": {"outputDimensionality": self.dimensions, "autoTruncate": False}}
            for text in texts]}
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:batchEmbedContents"
        for attempt in range(self.max_attempts):
            if self._last_request is not None:
                time.sleep(max(0, self.min_interval - (time.monotonic() - self._last_request)))
            request = urllib.request.Request(url, data=canonical(request_body).encode(),
                headers={"Content-Type": "application/json", "x-goog-api-key": self._key}, method="POST")
            self._last_request = time.monotonic()
            try:
                with urllib.request.urlopen(request, timeout=90) as response:
                    body = json.load(response)
                break
            except urllib.error.HTTPError as error:
                try:
                    details = json.loads(error.read()).get("error", {})
                except (ValueError, UnicodeDecodeError):
                    details = {}
                message = str(details.get("message", "Request failed")).replace(self._key, "[redacted]")[:600]
                if error.code in {429, 500, 502, 503, 504} and attempt + 1 < self.max_attempts:
                    delay = min(60.0, 2.0 ** (attempt + 1))
                    retry_header = error.headers.get("Retry-After", "") if error.headers else ""
                    if retry_header.isdigit():
                        delay = min(60.0, max(delay, float(retry_header)))
                    for item in details.get("details", []):
                        retry = str(item.get("retryDelay", ""))
                        if re.fullmatch(r"\d+(?:\.\d+)?s", retry):
                            delay = min(60.0, max(delay, float(retry[:-1])))
                    print(f"Gemini HTTP {error.code}; retry {attempt + 1}/{self.max_attempts - 1} in {delay:.0f}s.", flush=True)
                    time.sleep(delay)
                    continue
                raise RuntimeError(f"Gemini HTTP {error.code}: {message}. Completed batches are saved; check model access/quota or input limits, then rerun.") from None
            except (urllib.error.URLError, TimeoutError):
                if attempt + 1 < self.max_attempts:
                    time.sleep(min(30, 2 ** (attempt + 1)))
                    continue
                raise RuntimeError("Gemini connection failed. Completed batches are saved; rerun when connected.") from None
        embeddings = body.get("embeddings", [])
        if len(embeddings) != len(texts):
            raise ValueError("Gemini did not return one embedding per input; no batch was cached.")
        vectors = [validate_vector(e.get("values"), self.dimensions) for e in embeddings]
        return vectors, body.get("usageMetadata", {})


def read_cache(plan, run_dir):
    expected = {c["cache_key"]: c for c in plan["chunks"]}
    cached = {}
    for path in sorted((Path(run_dir) / "cache").glob("*.json.gz")):
        try:
            envelope = json.loads(gzip.decompress(path.read_bytes()))
            data = envelope["data"]
            if digest(canonical(data).encode()) != envelope["sha256"]:
                raise ValueError("Cache checksum mismatch")
            if data["run_id"] != plan["manifest"]["run_id"]:
                raise ValueError("Cache belongs to another model, dataset or policy")
            for item in data["items"]:
                key = item["cache_key"]
                if key not in expected or item["input_sha256"] != expected[key]["input_sha256"]:
                    raise ValueError("Unexpected input in cache")
                if key in cached:
                    raise ValueError("Duplicate cached input; do not combine concurrent runs")
                cached[key] = validate_vector(item["vector"], plan["manifest"]["config"]["dimensions"])
        except (KeyError, ValueError, OSError, EOFError, TypeError) as error:
            raise ValueError(f"Invalid cache file {path.name}: {error}. Preserve it for diagnosis.") from None
    return cached


def embed_pending(plan, client, run_dir, batch_size=16, max_new=None):
    if client.dimensions != plan["manifest"]["config"]["dimensions"]:
        raise ValueError("Client/index dimensions differ.")
    if not 1 <= batch_size <= 100 or (max_new is not None and max_new < 0):
        raise ValueError("Invalid batch limit.")
    cached = read_cache(plan, run_dir)
    pending = [c for c in plan["chunks"] if c["cache_key"] not in cached]
    if max_new is not None:
        pending = pending[:max_new]
    print(f"Saved vectors: {len(cached)}/{len(plan['chunks'])}; new vectors this call: {len(pending)}", flush=True)
    for offset in range(0, len(pending), batch_size):
        batch = pending[offset:offset + batch_size]
        vectors, usage = client.embed([c["embedding_input"] for c in batch])
        if len(vectors) != len(batch):
            raise ValueError("Embedding count mismatch.")
        items = [{"cache_key": c["cache_key"], "input_sha256": c["input_sha256"],
                  "vector": validate_vector(v, client.dimensions)} for c, v in zip(batch, vectors)]
        data = {"run_id": plan["manifest"]["run_id"], "items": items, "usage": usage}
        checksum = digest(canonical(data).encode())
        name = digest(canonical([c["cache_key"] for c in batch]).encode()) + ".json.gz"
        destination = Path(run_dir) / "cache" / name
        packed = gzip.compress(canonical({"data": data, "sha256": checksum}).encode(), mtime=0)
        atomic_write(destination, packed)
        if destination.read_bytes() != packed:
            raise OSError("Checkpoint read-back failed; stopping before the next API request.")
        cached.update({item["cache_key"]: item["vector"] for item in items})
        print(f"Saved {len(cached)}/{len(plan['chunks'])} vectors to persistent cache.", flush=True)
    status = {"run_id": plan["manifest"]["run_id"], "vectors": len(cached),
              "expected": len(plan["chunks"]), "complete": len(cached) == len(plan["chunks"])}
    save_json(Path(run_dir) / "embedding_status.json", status)
    return status


def build_index(plan, run_dir, local_root):
    cached = read_cache(plan, run_dir)
    if len(cached) != len(plan["chunks"]):
        raise ValueError("Embedding is incomplete. Rerun embed_pending before building the final index.")
    directory = Path(local_root) / plan["manifest"]["run_id"]
    marker = directory / "llis-index.json"
    if directory.exists() and any(directory.iterdir()):
        if not marker.exists() or json.loads(marker.read_text()) != plan["manifest"]:
            raise ValueError("Refusing to mix an existing Qdrant directory with this run.")
    save_json(marker, plan["manifest"])
    with closing(QdrantClient(path=str(directory / "qdrant"))) as client:
        if not client.collection_exists("llis"):
            client.create_collection("llis", vectors_config=models.VectorParams(
                size=plan["manifest"]["config"]["dimensions"], distance=models.Distance.COSINE))
        vector_config = client.get_collection("llis").config.params.vectors
        if vector_config.size != plan["manifest"]["config"]["dimensions"] or vector_config.distance != models.Distance.COSINE:
            raise ValueError("Qdrant vector configuration mismatch.")
        for offset in range(0, len(plan["chunks"]), 64):
            batch = plan["chunks"][offset:offset + 64]
            points = [models.PointStruct(id=c["id"], vector=cached[c["cache_key"]],
                payload={k: v for k, v in c.items() if k not in {"embedding_input", "cache_key"}}) for c in batch]
            client.upsert("llis", points=points, wait=True)
        if client.count("llis", exact=True).count != len(plan["chunks"]):
            raise ValueError("Qdrant point-count reconciliation failed.")
    # Package only after closing SQLite/Qdrant. Never operate a live DB on Drive FUSE.
    archive = Path(run_dir) / "qdrant-index.zip"
    temporary = archive.with_suffix(".zip.tmp")
    with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as zipped:
        for file in sorted(directory.rglob("*")):
            if file.is_file() and file.name != ".lock":
                zipped.write(file, arcname=file.relative_to(directory))
    os.replace(temporary, archive)
    with zipfile.ZipFile(archive) as zipped:
        if zipped.testzip() is not None:
            raise ValueError("Qdrant archive verification failed.")
    save_json(Path(run_dir) / "index_status.json", {
        "run_id": plan["manifest"]["run_id"], "points": len(plan["chunks"]), "complete": True,
        "archive_sha256": digest(archive.read_bytes()), "archive_bytes": archive.stat().st_size,
        "qdrant_client_version": "1.15.1"})
    return directory


def search(plan, client, index_dir, query, top_k=5):
    if not isinstance(query, str) or not query.strip() or not 1 <= top_k <= 20:
        raise ValueError("Provide a nonempty English query and top_k from 1 to 20.")
    if client.dimensions != plan["manifest"]["config"]["dimensions"]:
        raise ValueError("Query/document dimensions differ.")
    marker = json.loads((Path(index_dir) / "llis-index.json").read_text())
    if marker != plan["manifest"]:
        raise ValueError("Query/index model or dataset mismatch.")
    query_input = plan["manifest"]["config"]["query_prefix"] + query.strip()
    if len(query_input.encode()) > 20000:
        raise ValueError("Query is too long; ask a focused question.")
    with closing(QdrantClient(path=str(Path(index_dir) / "qdrant"))) as db:
        if not db.collection_exists("llis") or db.count("llis", exact=True).count != len(plan["chunks"]):
            raise ValueError("Index is incomplete; rerun the Qdrant build step before searching.")
        vectors, _ = client.embed([query_input])
        # Increasing candidate depth avoids long lessons monopolizing unique lesson results.
        limit = min(len(plan["chunks"]), top_k * 4)
        while True:
            points = db.query_points("llis", query=vectors[0], limit=limit,
                                     search_params=models.SearchParams(exact=True), with_payload=True).points
            results, seen = [], set()
            for point in points:
                if point.payload["lesson_id"] not in seen:
                    results.append({"score": point.score, **point.payload})
                    seen.add(point.payload["lesson_id"])
                if len(results) == top_k:
                    break
            if len(results) >= top_k or limit == len(plan["chunks"]):
                return results
            limit = min(len(plan["chunks"]), limit * 2)


def lesson_context(lessons, lesson_id):
    lesson = next(l for l in lessons if l["lesson_id"] == str(lesson_id))
    text = "\n\n".join(name.replace("_", " ").upper() + "\n" + lesson["sections"][name]["text"]
                       for name in SECTION_ORDER if lesson["sections"][name]["text"])
    return {"lesson_id": lesson["lesson_id"], "title": lesson["display_title"],
            "url": lesson["source"]["public_lesson_url"], "text": text,
            "quality_flags": lesson["quality_flags"], "attachments": lesson["attachments"]}


def evaluate(plan, client, index_dir, cases, output):
    rows = []
    for case in cases:
        results = search(plan, client, index_dir, case["question"], top_k=5)
        ids = [r["lesson_id"] for r in results]
        rank = next((i + 1 for i, identifier in enumerate(ids) if identifier in case["expected_lesson_ids"]), None)
        rows.append({**case, "retrieved_ids": ids, "first_relevant_rank": rank, "hit_at_5": rank is not None})
        save_json(output, {"run_id": plan["manifest"]["run_id"], "complete": False, "results": rows})
    result = {"run_id": plan["manifest"]["run_id"], "complete": True, "queries": len(rows),
              "hit_at_5": sum(r["hit_at_5"] for r in rows) / len(rows) if rows else None,
              "mrr_at_5": sum(1/r["first_relevant_rank"] if r["first_relevant_rank"] else 0 for r in rows) / len(rows) if rows else None,
              "scope": "Six source-checked smoke questions, not a broad quality benchmark or answer evaluation.",
              "results": rows}
    save_json(output, result)
    return result
