#!/usr/bin/env python3
"""Prepare the backed-up public LLIS response offline; never fetch linked content."""

import argparse
from collections import Counter, defaultdict
from datetime import date, datetime
import gzip
import hashlib
import json
from pathlib import Path
import re
import statistics
from urllib.parse import urljoin, urlsplit

from lxml import etree, html

ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT = "2026-09-12T082701Z"
BASE_URL = "https://llis.nasa.gov/"
SCHEMA_VERSION = 1
PLACEHOLDERS = {"", "none", "n/a", "not applicable", "null"}
SECTION_FIELDS = {
    "abstract": ("lessonAbstract",),
    "driving_event": ("drivingEvent", "description_event"),
    "lesson": ("lesson", "lesson_learned"),
    "recommendation": ("recommendation",),
    "evidence": ("evidence",),
    "related_policy": ("relatedPolicy",),
}
CORE_SECTIONS = tuple(k for k in SECTION_FIELDS if k != "related_policy")
BLOCK_TAGS = {"p", "div", "section", "article", "header", "footer", "center",
              "blockquote", "pre", "dl", "dt", "dd", "ul", "ol", "h1", "h2",
              "h3", "h4", "h5", "h6", "address", "figure", "figcaption"}
KNOWN_TAGS = BLOCK_TAGS | {"a", "img", "br", "hr", "li", "sub", "sup", "table",
    "tr", "td", "th", "caption", "thead", "tbody", "tfoot", "colgroup", "col",
    "span", "b", "i", "u", "em", "strong", "small", "font", "q", "s", "strike",
    "del", "ins", "code", "tt", "nobr", "abbr", "cite", "label", "wbr", "html",
    "body", "script", "style", "head", "iframe", "embed", "object", "video", "audio",
    "source", "noscript"}


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()


def compact(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def tidy(text):
    # Parse HTML entities once, in lxml. A second unescape could reinterpret source text.
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\u00a0", " ")
    lines = [re.sub(r"[^\S\n]+", " ", line).strip() for line in text.split("\n")]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def meaningful(text):
    return bool(text) and text.strip().casefold() not in PLACEHOLDERS


def resolved_url(reference):
    if not isinstance(reference, str) or not reference.strip():
        return None
    result = urljoin(BASE_URL, reference.strip())
    return result if urlsplit(result).scheme.lower() in {"http", "https"} else None


def render_html(value):
    """Retain text order, scientific notation, table cells/spans and media references."""
    result = {"text": None, "source_text_present": False, "tables": [], "links": [], "media": [],
              "parser_diagnostics": [], "unrecognized_tags": []}
    if value is None:
        return result
    if not isinstance(value, str):
        result["parser_diagnostics"] = [{"level": "ERROR", "message": "Expected a text field"}]
        return result
    if not value.strip():
        return result
    parser = html.HTMLParser(recover=True, no_network=True)
    try:
        root = html.fragment_fromstring(value, create_parent="div", parser=parser)
    except (etree.ParserError, ValueError) as exc:
        # Retain unparsed source, but flag it; callers must exclude it from chunking.
        result["text"] = value
        result["parse_failed"] = str(exc)
        return result
    result["parser_diagnostics"] = [
        {"level": e.level_name, "line": e.line, "column": e.column, "message": e.message}
        for e in parser.error_log
    ]
    for ignored in root.xpath(".//script | .//style | .//head"):
        ignored.drop_tree()  # Keep the following tail text.
    result["source_text_present"] = meaningful(tidy(root.text_content()))
    unknown = set()

    def children(node):
        return (node.text or "") + "".join(walk(child) + (child.tail or "") for child in node)

    def walk(node):
        if not isinstance(node.tag, str):
            return ""  # Comments are not article text; the raw snapshot retains them.
        tag = node.tag.lower()
        if tag not in KNOWN_TAGS:
            unknown.add(tag)
        if tag in {"script", "style", "head"}:
            return ""
        if tag in {"br", "hr"}:
            return "\n"
        if tag in {"img", "iframe", "embed", "object", "video", "audio", "source"}:
            number = len(result["media"]) + 1
            ref = node.get("src") or node.get("data") or ""
            alt = tidy(node.get("alt") or "")
            result["media"].append({"number": number, "tag": tag, "reference": ref,
                "resolved_url": resolved_url(ref), "alt": alt or None,
                "title": node.get("title"), "status": "not_downloaded"})
            label = "Image" if tag == "img" else "Media"
            marker = f"[{label} {number}" + (f"; source alt: {alt}" if alt else "")
            return marker + "; content not loaded]" + children(node)
        if tag == "table":
            number = len(result["tables"]) + 1
            table = {"number": number, "caption": None, "rows": []}
            result["tables"].append(table)
            captions = node.xpath("./caption")
            if captions:
                table["caption"] = tidy("\n".join(children(c) for c in captions))
            lines = [f"[Table {number}]"]
            if table["caption"]:
                lines.append(table["caption"])
            for row in node.xpath(".//tr"):
                if next(row.iterancestors("table"), None) is not node:
                    continue
                cells = []
                for cell in row:
                    if cell.tag not in {"td", "th"}:
                        continue
                    item = {"text": tidy(children(cell)), "is_header": cell.tag == "th",
                            "rowspan": cell.get("rowspan", "1"),
                            "colspan": cell.get("colspan", "1")}
                    cells.append(item)
                if cells:
                    table["rows"].append(cells)
                    lines.append(" | ".join(
                        c["text"] + (f" [rowspan={c['rowspan']}; colspan={c['colspan']}]"
                                     if (c["rowspan"], c["colspan"]) != ("1", "1") else "")
                        for c in cells))
            # Text outside cells is uncommon but must not silently disappear.
            outside = []
            if meaningful(tidy(node.text or "")):
                outside.append(tidy(node.text))
            for desc in node.iterdescendants():
                if not isinstance(desc.tag, str):
                    continue
                if next(desc.iterancestors("table"), None) is not node:
                    continue
                in_cell = any(a.tag in {"td", "th", "caption"} for a in desc.iterancestors())
                if not in_cell and desc.tag not in {"td", "th", "caption", "table"}:
                    if meaningful(tidy(desc.text or "")):
                        outside.append(tidy(desc.text))
                if not in_cell and meaningful(tidy(desc.tail or "")):
                    outside.append(tidy(desc.tail))
            if outside:
                table["text_outside_cells"] = outside
                lines.append("[Text outside table cells] " + " ".join(outside))
            return "\n" + "\n".join(lines) + f"\n[End table {number}]\n"
        body = children(node)
        if tag == "a":
            ref = node.get("href")
            if ref is not None:
                result["links"].append({"text": tidy(body), "reference": ref,
                    "resolved_url": resolved_url(ref), "status": "not_downloaded"})
        if tag == "sup":
            return "^(" + body + ")"
        if tag == "sub":
            return "_(" + body + ")"
        if tag == "li":
            parent = node.getparent()
            marker = "-"
            if parent is not None and parent.tag == "ol":
                try:
                    start = int(parent.get("start", "1"))
                    siblings = [n for n in parent if n.tag == "li"]
                    current = start
                    for sibling in siblings:
                        current = int(sibling.get("value", str(current)))
                        if sibling is node:
                            break
                        current += 1
                    marker = f"{current}."
                except ValueError:
                    marker = "-"
                    unknown.add("invalid_ordered_list_number")
            return "\n" + marker + " " + body.strip() + "\n"
        if tag in BLOCK_TAGS:
            return "\n" + body + "\n"
        return body

    text = tidy(children(root))
    result["text"] = text if meaningful(text) else None
    result["unrecognized_tags"] = sorted(unknown)
    return result


def provenance(index, field, value):
    escaped = field.replace("~", "~0").replace("/", "~1")
    return {"field": field, "raw_pointer": f"/hits/hits/{index}/_source/{escaped}",
            "raw_utf8_sha256": sha256(value.encode()) if isinstance(value, str) else None}


def content_signature(rendered):
    return compact({k: rendered[k] for k in ("text", "tables", "links", "media")})


def has_content(rendered):
    return bool(rendered["text"] or rendered["tables"] or rendered["media"])


def prepare(hits, snapshot_path, snapshot_sha):
    lessons, variants, quarantine, issues = [], [], [], []
    aliases = {name: Counter() for name, fields in SECTION_FIELDS.items() if len(fields) > 1}
    identifiers = set()
    missing = Counter()

    for index, hit in enumerate(hits):
        source = hit.get("_source")
        raw_pointer = f"/hits/hits/{index}"
        identifier = source.get("lesson_number") if isinstance(source, dict) else None
        valid_identifier = (not isinstance(identifier, bool) and
                            isinstance(identifier, (str, int)) and
                            re.fullmatch(r"[0-9]+", str(identifier)) is not None)
        if not valid_identifier:
            quarantine.append({"raw_document_id": hit.get("_id"), "raw_pointer": raw_pointer,
                "reason": "missing_or_invalid_lesson_number",
                "source_fields": sorted(source) if isinstance(source, dict) else [],
                "raw_hit": hit})
            continue
        lesson_id = str(identifier)
        if lesson_id in identifiers:
            raise ValueError(f"Duplicate lesson identifier: {lesson_id}; refusing to overwrite")
        identifiers.add(lesson_id)
        record_issues = []

        def issue(code, **details):
            record_issues.append({"code": code, **details})

        if str(hit.get("_id")) != lesson_id:
            issue("document_id_differs_from_lesson_number", raw_document_id=hit.get("_id"))
        title = render_html(source.get("title"))
        title["source"] = provenance(index, "title", source.get("title")) if "title" in source else None
        if not title["text"]:
            missing["title"] += 1
            issue("missing_title")
        sections = {}
        for section, fields in SECTION_FIELDS.items():
            available = []
            for field in fields:
                if field in source:
                    rendered = render_html(source[field])
                    rendered["source"] = provenance(index, field, source[field])
                    available.append((field, rendered))
            selected = next((pair for pair in available if has_content(pair[1])),
                            available[0] if available else (None, {**render_html(None), "source": None}))
            selected_field, rendered = selected
            sections[section] = rendered
            if not has_content(rendered):
                missing[section] += 1
                issue("missing_section", section=section)
            if selected_field and selected_field != fields[0]:
                issue("legacy_field_fallback", section=section, field=selected_field)
            if section in aliases:
                stats = aliases[section]
                if len(available) == 2:
                    stats["both_fields_present"] += 1
                    if content_signature(available[0][1]) == content_signature(available[1][1]):
                        stats["equivalent_after_rendering"] += 1
                    elif all(has_content(pair[1]) for pair in available):
                        stats["different_nonempty_variants"] += 1
                        alternate = next(pair for pair in available if pair[0] != selected_field)
                        variants.append({"lesson_id": lesson_id, "section": section,
                            "selection_rule": "first_nonempty_in_documented_field_order",
                            "selected_field": selected_field, "selected": rendered,
                            "alternate_field": alternate[0], "alternate": alternate[1]})
                        issue("field_variant", section=section, selected_field=selected_field,
                              alternate_field=alternate[0])
                    else:
                        stats["one_variant_empty"] += 1
                else:
                    stats["one_or_no_field_present"] += 1
        for name, section in [("title", title), *sections.items()]:
            if section.get("parse_failed"):
                issue("html_parse_failed", section=name, detail=section["parse_failed"])
            if section["parser_diagnostics"]:
                issue("html_parser_diagnostics", section=name,
                      diagnostics=section["parser_diagnostics"])
            if section["unrecognized_tags"]:
                issue("unrecognized_html_tags", section=name, tags=section["unrecognized_tags"])
            if re.search(r"(?<!\w)quot\w|\wquot(?!\w)", section["text"] or ""):
                issue("possible_broken_quote_entity", section=name)
            if section["tables"]:
                issue("table_requires_chunking_policy", section=name,
                      table_count=len(section["tables"]))
            if section["media"]:
                issue("media_content_not_loaded", section=name, count=len(section["media"]))
        normalized_date = None
        raw_date = source.get("lesson_date")
        if raw_date and meaningful(str(raw_date)):
            try:
                normalized_date = date.fromisoformat(raw_date).isoformat()
            except (TypeError, ValueError):
                issue("invalid_iso_date", value=raw_date)
        else:
            issue("missing_iso_date")
        ui_date = source.get("lessonDate")
        if meaningful(ui_date):
            try:
                if re.match(r"^\d{4}-\d{2}-\d{2}(?:[ T]|$)", ui_date):
                    parsed_ui = datetime.fromisoformat(ui_date).date().isoformat()
                else:
                    parts = ui_date.split()
                    months = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()
                    parsed_ui = date(int(parts[-1]), months.index(parts[1]) + 1, int(parts[2])).isoformat()
                if normalized_date and parsed_ui != normalized_date:
                    issue("date_field_variant", lesson_date=normalized_date, lessonDate=parsed_ui)
            except (TypeError, ValueError, IndexError):
                issue("unparsed_display_date", value=ui_date)
        attachments = []
        raw_attachments = source.get("attachments") or []
        if not isinstance(raw_attachments, list):
            issue("unexpected_attachments_type")
            raw_attachments = []
        for number, attachment in enumerate(raw_attachments):
            ref = attachment.get("link") if isinstance(attachment, dict) else None
            attachments.append({"source_pointer": f"{raw_pointer}/_source/attachments/{number}",
                "source_value": attachment, "resolved_url": resolved_url(ref), "status": "not_downloaded"})
        for number in range(4):
            field = f"documentUrl{number}"
            ref = source.get(field)
            if isinstance(ref, str) and meaningful(ref):
                attachments.append({"source_pointer": f"{raw_pointer}/_source/{field}",
                    "reference": ref, "name": source.get(f"documentName{number}"),
                    "description": source.get(f"documentDescription{number}"),
                    "resolved_url": resolved_url(ref), "status": "not_downloaded"})
        if attachments:
            issue("attachment_content_not_loaded", count=len(attachments))
        consumed = {"title", *(f for fields in SECTION_FIELDS.values() for f in fields)}
        has_body = any(sections[name]["source_text_present"] for name in CORE_SECTIONS)
        parse_failed = any(s.get("parse_failed") for s in sections.values()) or title.get("parse_failed")
        if not has_body:
            issue("no_core_body_text")
        lesson = {"schema_version": SCHEMA_VERSION, "lesson_id": lesson_id,
            "title": title, "display_title": title["text"] or f"LLIS lesson {lesson_id}",
            "display_title_generated": not bool(title["text"]), "lesson_date": normalized_date,
            "sections": sections, "attachments": attachments,
            "metadata_raw": {k: v for k, v in source.items() if k not in consumed},
            "source": {"snapshot_path": snapshot_path, "snapshot_sha256": snapshot_sha,
                       "raw_record_pointer": raw_pointer, "raw_document_id": hit.get("_id"),
                       "public_lesson_url": f"{BASE_URL}lesson/{lesson_id}"},
            "has_core_body_text": has_body,
            "text_extraction_succeeded": not bool(parse_failed),
            "quality_flags": sorted({i["code"] for i in record_issues})}
        lessons.append(lesson)
        if record_issues:
            issues.append({"lesson_id": lesson_id, "issues": record_issues})

    # Equal normalized body text is a review signal, never permission to drop an ID.
    groups = defaultdict(list)
    for lesson in lessons:
        body = compact([lesson["sections"][name]["text"] for name in CORE_SECTIONS])
        if lesson["has_core_body_text"]:
            groups[sha256(body.encode())].append(lesson["lesson_id"])
    duplicate_groups = [ids for ids in groups.values() if len(ids) > 1]
    for ids in duplicate_groups:
        for lesson_id in ids:
            lesson = next(item for item in lessons if item["lesson_id"] == lesson_id)
            lesson["quality_flags"] = sorted(set(lesson["quality_flags"]) | {"same_body_text_as_other_lesson"})
            entry = next((item for item in issues if item["lesson_id"] == lesson_id), None)
            if entry is None:
                entry = {"lesson_id": lesson_id, "issues": []}
                issues.append(entry)
            entry["issues"].append({"code": "same_body_text_as_other_lesson", "lesson_ids": ids})
    lessons.sort(key=lambda item: int(item["lesson_id"]))
    variants.sort(key=lambda item: (int(item["lesson_id"]), item["section"]))
    issues.sort(key=lambda item: int(item["lesson_id"]))
    flag_counts = Counter(flag for lesson in lessons for flag in lesson["quality_flags"])
    words = [sum(len((section["text"] or "").split()) for section in
                 [lesson["title"], *(lesson["sections"][name] for name in CORE_SECTIONS)])
             for lesson in lessons]
    stats = {"input_hits": len(hits), "normalized_lessons": len(lessons),
             "quarantined_hits": len(quarantine), "source_field_count": len({
                 key for hit in hits for key in (hit.get("_source") or {})}),
             "quarantined_hits_with_query_payload": sum("query" in q["source_fields"] for q in quarantine),
             "lessons_with_core_source_text": sum(l["has_core_body_text"] for l in lessons),
             "lessons_with_successful_text_extraction": sum(l["text_extraction_succeeded"] for l in lessons),
             "missing_fields": {k: missing[k] for k in ("title", *SECTION_FIELDS)},
             "alias_comparison": {k: dict(v) for k, v in aliases.items()},
             "field_variant_pairs": len(variants),
             "lessons_with_field_variants": len({v["lesson_id"] for v in variants}),
             "lesson_counts_by_flag": dict(sorted(flag_counts.items())),
             "same_body_text_groups": duplicate_groups,
             "word_estimate": {"method": "whitespace-separated units; title plus five core sections, including generated table/media markers; excludes related_policy, metadata and alternate fields; not model tokens",
                               "total": sum(words), "median_per_lesson": statistics.median(words) if words else 0,
                               "maximum_per_lesson": max(words, default=0),
                               "lessons_over_1500": sum(n > 1500 for n in words)}}
    assert len(lessons) + len(quarantine) == len(hits)
    return lessons, variants, quarantine, issues, stats


def load_snapshot(snapshot_dir):
    manifest = json.loads((snapshot_dir / "manifest.json").read_text())
    info = manifest["files"]["compressed"]
    compressed = (snapshot_dir / info["path"]).read_bytes()
    if len(compressed) != info["bytes"] or sha256(compressed) != info["sha256"]:
        raise ValueError("Raw snapshot compressed checksum/size mismatch")
    raw = gzip.decompress(compressed)
    info_raw = manifest["files"]["uncompressed"]
    if len(raw) != info_raw["bytes"] or sha256(raw) != info_raw["sha256"]:
        raise ValueError("Raw snapshot uncompressed checksum/size mismatch")
    response = json.loads(raw)
    hits = response["hits"]["hits"]
    total = response["hits"]["total"]
    if isinstance(total, dict):
        if total.get("relation") != "eq":
            raise ValueError("Raw total is not exact")
        total = total["value"]
    if response.get("timed_out") or response.get("_shards", {}).get("failed", 0):
        raise ValueError("Raw search was incomplete")
    if len(hits) != total or len(hits) != manifest["record_count"]:
        raise ValueError("Raw record-count mismatch")
    if len({h["_id"] for h in hits}) != len(hits):
        raise ValueError("Repeated raw document IDs")
    return manifest, hits


def report_text(stats, snapshot_dir, source_manifest):
    flag = stats["lesson_counts_by_flag"]
    missing_rows = "\n".join(f"| `{k}` | {v:,} |" for k, v in stats["missing_fields"].items())
    alias_rows = "\n".join(
        f"| `{name}` | {s.get('both_fields_present', 0):,} | {s.get('equivalent_after_rendering', 0):,} | {s.get('different_nonempty_variants', 0):,} | {s.get('one_variant_empty', 0):,} |"
        for name, s in stats["alias_comparison"].items())
    flag_rows = "\n".join(f"| `{k}` | {v:,} |" for k, v in flag.items())
    w = stats["word_estimate"]
    return f"""# LLIS data quality report

Generated offline from the public NASA LLIS snapshot received at **{source_manifest['response_received_utc']}**.
This describes the backed-up search response, not all knowledge held by NASA.

## Record reconciliation

| Item | Count |
| --- | ---: |
| Raw search hits | {stats['input_hits']:,} |
| Records with a valid, unique lesson number | {stats['normalized_lessons']:,} |
| Quarantined records without a valid lesson number | {stats['quarantined_hits']:,} |
| Distinct source field names across all hits | {stats['source_field_count']:,} |

Of the quarantined records, {stats['quarantined_hits_with_query_payload']:,} contain search-query payloads. Their complete raw hits and original locations are retained in `quarantine.jsonl`. Nothing has been removed from the raw backup. A lesson number identifies a record; it does not certify the completeness or correctness of its text.

The count of {stats['input_hits']:,} refers to **index records**, not that many lessons. The response is fully backed up according to its count and checksum checks, but article content can still be absent and separately linked files were never included. In this snapshot, {stats['lessons_with_successful_text_extraction']:,} lessons parsed without a fatal extraction failure and {stats['lessons_with_core_source_text']:,} have source text in at least one core section; generated media markers alone do not qualify as source text.

## Missing or placeholder content

These counts are after HTML rendering, exact whole-field placeholder removal, and documented legacy fallback. A placeholder such as `None` is not indexed as a claim. Sentences beginning with “None of …” are retained. Missing titles receive a clearly generated display label; no NASA title is invented.

| Field or section | Lessons without content |
| --- | ---: |
{missing_rows}

## New and legacy fields

| Section | Both fields present | Equivalent rendered content | Different nonempty variants | One variant empty |
| --- | ---: | ---: | ---: | ---: |
{alias_rows}

There are **{stats['field_variant_pairs']:,} differing field pairs in {stats['lessons_with_field_variants']:,} lessons**. These are differences, not necessarily factual contradictions. Current UI fields are selected first, with legacy fallback only when the current field has no content. Different nonempty alternatives, including their text, tables, references and provenance, are saved in `field_variants.jsonl.gz`; they are not concatenated into the main article.

For example, lesson 2456's event and lesson 1033's lesson text contain apparent broken quotation-entity spelling in the UI field while their legacy alternatives use quotation marks. No speculative text repairs are applied. Field selection is a reproducible rule, not a claim that the UI version is always better. These examples and the full variant file should be reviewed before choosing retrieval behavior.

## Review signals and extraction limits

Counts below are distinct lessons per flag; a lesson can appear in multiple rows. Missing evidence is often a source placeholder, not an extraction failure. Parser diagnostics can be recoverable markup errors; they do not automatically mean text was lost. Conversely, successful parsing does not prove fidelity to the original page layout.

| Signal | Lessons |
| --- | ---: |
{flag_rows}

Tables retain rows, header cells and raw rowspan/colspan attributes, with cell-separated text for inspection. They still require a table-aware chunking decision. Subscripts and superscripts use explicit `_(...)` and `^(...)` markers. Images/media retain source references and alt text only: their contents have not been downloaded, OCRed or interpreted. Links and attachments are references with `not_downloaded` status, not evidence that their targets were retrieved or even remain accessible.

Unrecognized tags, parser messages and possible `quot` encoding damage remain in the audit. The quote detector is a review heuristic and can have false positives or miss other corruption. No semantic correction, image interpretation or external-document extraction was performed. Identical normalized body text across different IDs is flagged and retained, not merged; metadata or media can differ.

## Size and next step

The selected title plus five core sections total approximately **{w['total']:,} whitespace-separated units** (median {w['median_per_lesson']:,.0f}, maximum {w['maximum_per_lesson']:,} per lesson; {w['lessons_over_1500']:,} lessons exceed 1,500). This includes generated table/media markers, excludes related-policy text, metadata and alternate fields, and **is not a model-token count**. Do not estimate embedding costs from all legacy and current fields concatenated together.

This commit completes normalization and its audit, not chunking or RAG. Before producing chunks, select a policy for differing fields, tables and missing media; use a small set of source-checked questions to evaluate retrieval. A useful first scope is the available text, with media-dependent questions explicitly identified as unsupported. No need to claim the entire collection is clean or to fetch every linked file first.

## Reproduce and inspect

```sh
python -m pip install -r requirements-data.txt
python scripts/normalize_llis.py
python -m unittest discover -s tests -v
```

Outputs: [`data/processed/llis/{snapshot_dir.name}/`](../data/processed/llis/{snapshot_dir.name}/). The manifest records input/output SHA-256 checksums, parser versions, script hash and all counts. Each selected field and alternative points to its original raw field and records its UTF-8 hash. Full source HTML stays in the immutable raw snapshot.

See [preparation rules and schema](DATA_PREPARATION.md) and [raw backup scope](../data/raw/llis/README.md). Public source: [NASA LLIS](https://llis.nasa.gov/).
"""


def write_jsonl(path, records):
    data = ("".join(compact(record) + "\n" for record in records)).encode()
    if path.suffix == ".gz":
        # No timestamp or original filename in the gzip header.
        with path.open("wb") as stream:
            with gzip.GzipFile(fileobj=stream, mode="wb", filename="", mtime=0, compresslevel=9) as zipped:
                zipped.write(data)
    else:
        path.write_bytes(data)


def run(snapshot_dir, output_dir, report_path):
    snapshot_dir, output_dir = Path(snapshot_dir).resolve(), Path(output_dir).resolve()
    report_path = Path(report_path).resolve()
    raw_root = (ROOT / "data/raw").resolve()
    if output_dir == raw_root or raw_root in output_dir.parents or report_path == raw_root or raw_root in report_path.parents:
        raise ValueError("Refusing to write outputs into the raw backup")
    source_manifest, hits = load_snapshot(snapshot_dir)
    snapshot_file = snapshot_dir / source_manifest["files"]["compressed"]["path"]
    snapshot_path = snapshot_file.relative_to(ROOT).as_posix()
    source_sha = source_manifest["files"]["compressed"]["sha256"]
    lessons, variants, quarantine, issues, stats = prepare(hits, snapshot_path, source_sha)
    output_dir.mkdir(parents=True, exist_ok=True)
    products = {"lessons.jsonl.gz": lessons, "field_variants.jsonl.gz": variants,
                "quarantine.jsonl": quarantine, "quality_issues.jsonl.gz": issues}
    files = {}
    for name, records in products.items():
        path = output_dir / name
        write_jsonl(path, records)
        data = path.read_bytes()
        files[name] = {"bytes": len(data), "sha256": sha256(data), "records": len(records)}
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(report_text(stats, snapshot_dir, source_manifest), encoding="utf-8")
    manifest = {"schema_version": SCHEMA_VERSION, "source_snapshot": snapshot_path,
        "source_snapshot_sha256": source_sha,
        "source_manifest_sha256": sha256((snapshot_dir / "manifest.json").read_bytes()),
        "source_received_utc": source_manifest["response_received_utc"],
        "pipeline": {"script": "scripts/normalize_llis.py",
                     "script_sha256": sha256(Path(__file__).read_bytes()),
                     "lxml_version": etree.LXML_VERSION, "libxml_version": etree.LIBXML_VERSION},
        "external_fetches": 0, "statistics": stats, "files": files,
        "quality_report_sha256": sha256(report_path.read_bytes())}
    (output_dir / "manifest.json").write_bytes(json_bytes(manifest))
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", type=Path, default=ROOT / "data/raw/llis" / SNAPSHOT)
    parser.add_argument("--output", type=Path, default=ROOT / "data/processed/llis" / SNAPSHOT)
    parser.add_argument("--report", type=Path, default=ROOT / "docs/DATA_QUALITY.md")
    args = parser.parse_args()
    try:
        manifest = run(args.snapshot, args.output, args.report)
    except (ValueError, KeyError, OSError) as exc:
        parser.exit(1, f"Normalization failed: {exc}\n")
    print(json.dumps(manifest["statistics"], indent=2))


if __name__ == "__main__":
    main()
