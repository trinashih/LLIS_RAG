"""Check evidence retention, extraction edge cases and full-snapshot reproducibility."""

import gzip
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

from lxml import html

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("normalize_llis", ROOT / "scripts/normalize_llis.py")
normalizer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(normalizer)
RAW = ROOT / "data/raw/llis" / normalizer.SNAPSHOT
PROCESSED = ROOT / "data/processed/llis" / normalizer.SNAPSHOT


def fixture(lesson_id=1, **fields):
    return {"_id": str(lesson_id), "_source": {
        "lesson_number": lesson_id, "title": "Test lesson", "lesson_date": "2001-01-02",
        "lessonDate": "Tue Jan 02 00:00:00 GMT 2001", **fields}}


def resolve(document, pointer):
    for part in pointer.strip("/").split("/"):
        part = part.replace("~1", "/").replace("~0", "~")
        document = document[int(part)] if isinstance(document, list) else document[part]
    return document


class ExtractionTests(unittest.TestCase):
    def test_symbols_entities_and_inline_words(self):
        rendered = normalizer.render_html(
            '<p>Use <b>pro</b>tection: x &lt; 5 &amp; y &gt; 2.</p>'
            '<p>10<sup>3</sup> and H<sub>2</sub>O; &amp;lt; is literal.</p>'
            '<script>bad()</script>Tail')
        self.assertIn('protection: x < 5 & y > 2.', rendered['text'])
        self.assertIn('10^(3) and H_(2)O; &lt; is literal.', rendered['text'])
        self.assertNotIn('bad()', rendered['text'])
        self.assertTrue(rendered['text'].endswith('Tail'))

    def test_ordered_list_and_table_spans(self):
        rendered = normalizer.render_html(
            '<ol start="3"><li>First</li><li value="8">Second</li><li>Third</li></ol>'
            '<table><caption>Loads</caption><tr><th rowspan="2">Part</th>'
            '<th colspan="2">Force</th></tr><tr><td>1</td><td>20</td></tr></table>')
        for item in ('3. First', '8. Second', '9. Third', '1 | 20'):
            self.assertIn(item, rendered['text'])
        table = rendered['tables'][0]
        self.assertEqual(table['caption'], 'Loads')
        self.assertEqual(table['rows'][0][0]['rowspan'], '2')
        self.assertTrue(table['rows'][0][1]['is_header'])
        self.assertEqual(table['rows'][0][1]['colspan'], '2')

    def test_media_and_links_are_references(self):
        rendered = normalizer.render_html(
            '<a href="/documents/1">Read</a><img src="/lessons/1/a.jpg" alt="Circuit">'
            '<a href="mailto:test@example.com">Email</a>')
        self.assertEqual(rendered['links'][0]['resolved_url'], 'https://llis.nasa.gov/documents/1')
        self.assertIsNone(rendered['links'][1]['resolved_url'])
        self.assertEqual(rendered['media'][0]['alt'], 'Circuit')
        self.assertEqual(rendered['media'][0]['status'], 'not_downloaded')
        self.assertIn('content not loaded', rendered['text'])

    def test_whole_field_placeholders_only(self):
        for text in ('None', '<p> N/A&nbsp;</p>', '<div></div>', 'null', 'Not Applicable'):
            self.assertIsNone(normalizer.render_html(text)['text'])
        self.assertEqual(normalizer.render_html('None of the tests failed.')['text'], 'None of the tests failed.')

    def test_aliases_fallback_variants_missing_title_and_quarantine(self):
        hits = [fixture(1, title='None', lesson='<p>A</p>', lesson_learned='A'),
                fixture(2, drivingEvent='None', description_event='Recovered text'),
                fixture(3, lesson='First version', lesson_learned='Second version'),
                {'_id': '_search?example.css', '_source': {'query': {'match_all': {}}}}]
        lessons, variants, quarantine, _, stats = normalizer.prepare(hits, 'raw.gz', 'abc')
        self.assertEqual(len(lessons), 3)
        self.assertTrue(lessons[0]['display_title_generated'])
        self.assertEqual(lessons[0]['display_title'], 'LLIS lesson 1')
        self.assertEqual(lessons[1]['sections']['driving_event']['source']['field'], 'description_event')
        self.assertEqual(len(variants), 1)
        self.assertEqual(variants[0]['alternate']['text'], 'Second version')
        self.assertEqual(quarantine[0]['raw_hit'], hits[-1])
        self.assertEqual(stats['alias_comparison']['lesson']['equivalent_after_rendering'], 1)

    def test_duplicate_lesson_numbers_fail(self):
        with self.assertRaisesRegex(ValueError, 'Duplicate lesson identifier'):
            normalizer.prepare([fixture(1), fixture(1)], 'raw.gz', 'abc')

    def test_date_disagreement_is_not_silently_rewritten(self):
        hits = [fixture(1, lesson='Some text', lessonDate='Wed Jan 03 00:00:00 GMT 2001')]
        lessons, _, _, issues, _ = normalizer.prepare(hits, 'raw.gz', 'abc')
        self.assertEqual(lessons[0]['lesson_date'], '2001-01-02')
        self.assertIn('date_field_variant', lessons[0]['quality_flags'])
        self.assertEqual(issues[0]['lesson_id'], '1')

    def test_iso_display_timestamp_and_media_only_body(self):
        hits = [fixture(1, lesson='<img src="/image.jpg">',
                        lessonDate='2001-01-02 16:56:38.056')]
        lessons, _, _, _, _ = normalizer.prepare(hits, 'raw.gz', 'abc')
        self.assertFalse(lessons[0]['has_core_body_text'])
        self.assertNotIn('unparsed_display_date', lessons[0]['quality_flags'])
        self.assertIn('no_core_body_text', lessons[0]['quality_flags'])

    def test_checksum_failure_stops_processing(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            manifest = json.loads((RAW / 'manifest.json').read_text())
            (directory / 'manifest.json').write_text(json.dumps(manifest))
            (directory / 'llis_raw_response.json.gz').write_bytes(b'corrupted')
            with self.assertRaisesRegex(ValueError, 'checksum/size mismatch'):
                normalizer.load_snapshot(directory)

    def test_raw_directory_write_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'raw backup'):
            normalizer.run(RAW, RAW / 'processed', ROOT / 'docs/DATA_QUALITY.md')


class SnapshotTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source_manifest, cls.hits = normalizer.load_snapshot(RAW)
        cls.response = {'hits': {'hits': cls.hits}}
        cls.lessons, cls.variants, cls.quarantine, cls.issues, cls.stats = normalizer.prepare(
            cls.hits, 'data/raw/llis/' + normalizer.SNAPSHOT + '/llis_raw_response.json.gz',
            cls.source_manifest['files']['compressed']['sha256'])

    def assert_provenance(self, section):
        if section['source']:
            original = resolve(self.response, section['source']['raw_pointer'])
            expected = normalizer.sha256(original.encode()) if isinstance(original, str) else None
            self.assertEqual(expected, section['source']['raw_utf8_sha256'])

    def test_complete_partition_and_original_metadata(self):
        self.assertEqual((len(self.hits), len(self.lessons), len(self.quarantine)), (2127, 2117, 10))
        pointers = [l['source']['raw_record_pointer'] for l in self.lessons]
        pointers += [q['raw_pointer'] for q in self.quarantine]
        self.assertEqual(len(set(pointers)), len(self.hits))
        self.assertTrue(all(lesson['text_extraction_succeeded'] for lesson in self.lessons))
        consumed = {'title', *(f for fields in normalizer.SECTION_FIELDS.values() for f in fields)}
        for lesson in self.lessons:
            original = resolve(self.response, lesson['source']['raw_record_pointer'])['_source']
            self.assertEqual(lesson['metadata_raw'], {k: v for k, v in original.items() if k not in consumed})
            self.assert_provenance(lesson['title'])
            for section in lesson['sections'].values():
                self.assert_provenance(section)
            for attachment in lesson['attachments']:
                resolve(self.response, attachment['source_pointer'])
        for variant in self.variants:
            self.assert_provenance(variant['selected'])
            self.assert_provenance(variant['alternate'])
            self.assertNotEqual(normalizer.content_signature(variant['selected']),
                                normalizer.content_signature(variant['alternate']))
        for item in self.quarantine:
            self.assertEqual(resolve(self.response, item['raw_pointer']), item['raw_hit'])

    def test_all_parsed_tables_links_and_media_are_accounted_for(self):
        media_tags = {'img', 'iframe', 'embed', 'object', 'video', 'audio', 'source'}
        fields = []
        for lesson in self.lessons:
            fields.extend([lesson['title'], *lesson['sections'].values()])
        fields.extend(variant['alternate'] for variant in self.variants)
        for section in fields:
            if not section['source']:
                continue
            raw = resolve(self.response, section['source']['raw_pointer'])
            if not raw or not raw.strip():
                continue
            root = html.fragment_fromstring(raw, create_parent='div')
            nodes = [node for node in root.iterdescendants()
                     if not any(parent.tag in {'script', 'style', 'head'} for parent in node.iterancestors())]
            context = section['source']['raw_pointer']
            self.assertEqual(sum(node.tag == 'table' for node in nodes), len(section['tables']), context)
            self.assertEqual(sum(node.tag == 'a' and node.get('href') is not None for node in nodes),
                             len(section['links']), context)
            self.assertEqual(sum(node.tag in media_tags for node in nodes), len(section['media']), context)

    def test_full_rerun_matches_saved_outputs_and_keeps_raw_unchanged(self):
        before = {p.name: normalizer.sha256(p.read_bytes()) for p in RAW.iterdir() if p.is_file()}
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / 'processed'
            report = Path(temp) / 'report.md'
            manifest = normalizer.run(RAW, output, report)
            for path in output.iterdir():
                self.assertEqual(path.read_bytes(), (PROCESSED / path.name).read_bytes(), path.name)
            self.assertEqual(report.read_bytes(), (ROOT / 'docs/DATA_QUALITY.md').read_bytes())
            for name, info in manifest['files'].items():
                data = (output / name).read_bytes()
                self.assertEqual(normalizer.sha256(data), info['sha256'])
                content = gzip.decompress(data) if name.endswith('.gz') else data
                self.assertEqual(len(content.splitlines()), info['records'])
        after = {p.name: normalizer.sha256(p.read_bytes()) for p in RAW.iterdir() if p.is_file()}
        self.assertEqual(before, after)


if __name__ == '__main__':
    unittest.main()
