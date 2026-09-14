"""Offline correctness tests. Synthetic vectors test plumbing, not Gemini quality."""
import gzip
import importlib.util
import io
import json
from pathlib import Path
import re
import tempfile
import unittest
from unittest.mock import patch
import urllib.error
import zipfile

from qdrant_client import QdrantClient

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('index_llis', ROOT / 'scripts/index_llis.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def lesson(identifier, body, title='ALPHA test'):
    sections = {name: {'text': None, 'source': None} for name in m.SECTION_ORDER}
    sections['lesson'] = {'text': body, 'source': {
        'field': 'lesson', 'raw_pointer': f'/hits/hits/{identifier}/_source/lesson',
        'raw_utf8_sha256': m.digest(body.encode())}}
    return {'lesson_id': str(identifier), 'title': {'text': title}, 'display_title': title,
            'text_extraction_succeeded': True, 'has_core_body_text': True,
            'sections': sections, 'quality_flags': [], 'lesson_date': '2001-01-01',
            'source': {'public_lesson_url': f'https://llis.nasa.gov/lesson/{identifier}'},
            'attachments': []}


class FakeClient:
    dimensions = m.DIMENSIONS

    def __init__(self, fail_call=None):
        self.calls = []
        self.fail_call = fail_call

    def embed(self, texts):
        self.calls.append(texts)
        if len(self.calls) == self.fail_call:
            raise RuntimeError('Simulated interruption')
        vectors = []
        for text in texts:
            vector = [0.0] * self.dimensions
            vector[0 if 'ALPHA' in text else 1] = 1.0
            vectors.append(vector)
        return vectors, {'promptTokenCount': 10}


class IndexTests(unittest.TestCase):
    def test_short_case_whole_and_reference_only_section(self):
        item = lesson(1, 'Cause, consequence and corrective action.')
        item['sections']['recommendation'] = {'text': 'Same as in Lesson Learned', 'source': {}}
        plan = m.make_plan([item], 'a' * 64)
        self.assertEqual(len(plan['chunks']), 1)
        chunk = plan['chunks'][0]
        self.assertTrue(chunk['whole_lesson'])
        self.assertEqual(chunk['omitted_reference_sections'], ['recommendation'])
        self.assertNotIn('Same as in Lesson Learned', chunk['embedding_input'])
        self.assertIn('Same as in Lesson Learned', m.lesson_context([item], '1')['text'])

    def test_long_paragraph_splits_without_losing_content(self):
        body = ' '.join(f'term{i}' for i in range(600))
        item = lesson(2, body)
        plan = m.make_plan([item], 'b' * 64, max_words=70, max_input_bytes=1000)
        spans = [s for c in plan['chunks'] for s in c['spans']]
        rebuilt = ''.join(body[s['start']:s['end']] for s in spans)
        self.assertEqual(re.sub(r'\s', '', rebuilt), re.sub(r'\s', '', body))
        self.assertTrue(all(len(c['embedding_input'].split()) <= 70 for c in plan['chunks']))
        self.assertTrue(all(len(c['embedding_input'].encode()) <= 1000 for c in plan['chunks']))

    def test_table_stays_together_and_overflow_is_flagged(self):
        table = '[Table 1]\nHeader | Value\nA | 17\n[End table 1]'
        body = ('Introduction. ' * 100) + '\n\n' + table + '\n\n' + ('Explanation. ' * 100)
        plan = m.make_plan([lesson(2, body)], 'b' * 64, max_words=70, max_input_bytes=1000)
        self.assertTrue(any(table in c['text'] for c in plan['chunks']))
        large = '[Table 1]\n' + 'row | value\n' * 100 + '[End table 1]'
        large_plan = m.make_plan([lesson(2, large)], 'b' * 64, max_words=70, max_input_bytes=1000)
        self.assertGreater(large_plan['manifest']['table_fragments'], 0)

    def test_policy_and_source_change_run_identity(self):
        data = [lesson(1, 'Some source text.')]
        a = m.make_plan(data, 'a' * 64)
        b = m.make_plan(data, 'b' * 64)
        c = m.make_plan(data, 'a' * 64, max_words=1000)
        self.assertEqual(len({p['manifest']['run_id'] for p in (a, b, c)}), 3)
        self.assertNotEqual(a['chunks'][0]['id'], b['chunks'][0]['id'])

    def test_resume_real_qdrant_export_restore_and_search(self):
        data = [lesson(i, f'Unique detail {i}', title='ALPHA' if i == 0 else 'BETA') for i in range(4)]
        plan = m.make_plan(data, 'c' * 64)
        with tempfile.TemporaryDirectory() as temp:
            run = m.save_plan(plan, Path(temp) / 'persistent')
            failing = FakeClient(fail_call=2)
            with self.assertRaisesRegex(RuntimeError, 'interruption'):
                m.embed_pending(plan, failing, run, batch_size=2)
            self.assertEqual(len(m.read_cache(plan, run)), 2)
            with self.assertRaisesRegex(ValueError, 'incomplete'):
                m.build_index(plan, run, Path(temp) / 'db')
            resumed = FakeClient()
            status = m.embed_pending(plan, resumed, run, batch_size=2)
            self.assertTrue(status['complete'])
            self.assertEqual(sum(len(c) for c in resumed.calls), 2)
            m.embed_pending(plan, resumed, run, batch_size=2)
            self.assertEqual(len(resumed.calls), 1)
            db_dir = m.build_index(plan, run, Path(temp) / 'db')
            m.build_index(plan, run, Path(temp) / 'db')  # Idempotent IDs, not duplicates.
            hits = m.search(plan, FakeClient(), db_dir, 'ALPHA', top_k=2)
            self.assertEqual(hits[0]['lesson_id'], '0')
            self.assertEqual(len({h['lesson_id'] for h in hits}), 2)
            self.assertEqual(hits[0]['spans'][0]['source']['raw_pointer'], '/hits/hits/0/_source/lesson')
            saved = json.loads((run / 'index_status.json').read_text())
            archive = run / 'qdrant-index.zip'
            self.assertEqual(saved['archive_sha256'], m.digest(archive.read_bytes()))
            restored = Path(temp) / 'restore'
            with zipfile.ZipFile(archive) as zipped:
                self.assertIsNone(zipped.testzip())
                zipped.extractall(restored)  # Archive just produced by this test.
            db = QdrantClient(path=str(restored / 'qdrant'))
            try:
                self.assertEqual(db.count('llis', exact=True).count, 4)
            finally:
                db.close()

    def test_cache_corruption_fails_before_more_api_calls(self):
        plan = m.make_plan([lesson(1, 'Text')], 'd' * 64)
        with tempfile.TemporaryDirectory() as temp:
            run = m.save_plan(plan, temp)
            client = FakeClient()
            m.embed_pending(plan, client, run)
            path = next((run / 'cache').glob('*.json.gz'))
            envelope = json.loads(gzip.decompress(path.read_bytes()))
            envelope['data']['items'][0]['vector'][0] = 0.5
            path.write_bytes(gzip.compress(json.dumps(envelope).encode()))
            with self.assertRaisesRegex(ValueError, 'checksum mismatch'):
                m.embed_pending(plan, client, run)
            self.assertEqual(len(client.calls), 1)

    def test_rest_batch_contract_and_independent_inputs(self):
        response = {'embeddings': [{'values': [1.0] + [0.0] * (m.DIMENSIONS - 1)}] * 2,
                    'usageMetadata': {'promptTokenCount': 22}}
        captured = []

        def transport(request, timeout):
            captured.append(request)
            return io.BytesIO(json.dumps(response).encode())

        with patch.object(m.urllib.request, 'urlopen', side_effect=transport):
            vectors, usage = m.GeminiClient('test-secret', min_interval=0).embed(['A', 'B'])
        body = json.loads(captured[0].data)
        self.assertEqual(len(vectors), 2)
        self.assertEqual(usage['promptTokenCount'], 22)
        self.assertTrue(captured[0].full_url.endswith('/gemini-embedding-2:batchEmbedContents'))
        self.assertNotIn('test-secret', captured[0].full_url)
        self.assertEqual([r['content']['parts'][0]['text'] for r in body['requests']], ['A', 'B'])
        for request in body['requests']:
            self.assertEqual(request['embedContentConfig'], {'autoTruncate': False, 'outputDimensionality': 3072})
            self.assertNotIn('taskType', request)

    def test_invalid_vectors_and_aggregation_are_rejected(self):
        for value in ([0.0] * m.DIMENSIONS, [float('nan')] * m.DIMENSIONS, [1.0]):
            with self.assertRaises(ValueError):
                m.validate_vector(value, m.DIMENSIONS)
        response = {'embeddings': [{'values': [1.0] * m.DIMENSIONS}]}
        with patch.object(m.urllib.request, 'urlopen', return_value=io.BytesIO(json.dumps(response).encode())):
            with self.assertRaisesRegex(ValueError, 'one embedding per input'):
                m.GeminiClient('test-secret', min_interval=0).embed(['A', 'B'])

    def test_http_key_redaction_and_bounded_retry(self):
        secret = 'SECRET-DO-NOT-PRINT'
        failure = urllib.error.HTTPError('https://example.invalid', 403, 'Forbidden', {},
                                         io.BytesIO(json.dumps({'error': {'message': secret}}).encode()))
        with patch.object(m.urllib.request, 'urlopen', side_effect=failure):
            with self.assertRaises(RuntimeError) as caught:
                m.GeminiClient(secret, min_interval=0).embed(['A'])
        self.assertNotIn(secret, str(caught.exception))
        failure = urllib.error.HTTPError('https://example.invalid', 429, 'Quota', {},
                                         io.BytesIO(b'{"error":{"message":"Quota"}}'))
        success = io.BytesIO(json.dumps({'embeddings': [{'values': [1.0] * m.DIMENSIONS}]}).encode())
        with patch.object(m.urllib.request, 'urlopen', side_effect=[failure, success]) as transport, patch.object(m.time, 'sleep'):
            m.GeminiClient(secret, min_interval=0).embed(['A'])
            self.assertEqual(transport.call_count, 2)


class CorpusTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.lessons, cls.sha = m.load_lessons(ROOT / 'data/processed/llis/2026-09-12T082701Z')
        cls.plan = m.make_plan(cls.lessons, cls.sha)

    def test_all_lessons_covered_with_lossless_source_spans(self):
        plan = self.plan
        self.assertEqual(plan['manifest']['indexed_lessons'], 2117)
        self.assertEqual(plan['manifest']['excluded'], [])
        self.assertEqual(plan['manifest']['whole_lessons'], 1905)
        self.assertEqual(plan['manifest']['chunks'], 2394)
        grouped = {}
        for chunk in plan['chunks']:
            grouped.setdefault(chunk['lesson_id'], []).append(chunk)
            self.assertLessEqual(len(chunk['embedding_input'].split()), 1200)
            self.assertLessEqual(len(chunk['embedding_input'].encode()), 20000)
        for item in self.lessons:
            chunks = grouped[item['lesson_id']]
            for name in m.SECTION_ORDER:
                text = item['sections'][name]['text']
                if not text or name in chunks[0]['omitted_reference_sections']:
                    continue
                spans = [s for c in chunks for s in c['spans'] if s['section'] == name]
                rebuilt = ''.join(text[s['start']:s['end']] for s in spans)
                self.assertEqual(re.sub(r'\s', '', text), re.sub(r'\s', '', rebuilt), f"{item['lesson_id']} {name}")
                self.assertTrue(all(s['source'] == item['sections'][name]['source'] for s in spans))

    def test_plan_reproducibility_and_smoke_sources(self):
        self.assertEqual(self.plan, m.make_plan(self.lessons, self.sha))
        ids = {item['lesson_id'] for item in self.lessons}
        cases = json.loads((ROOT / 'tests/retrieval_cases.json').read_text())
        self.assertEqual(len(cases), 6)
        for case in cases:
            self.assertTrue(set(case['expected_lesson_ids']) <= ids)

    def test_notebook_code_and_source_pins(self):
        notebook = json.loads((ROOT / 'notebooks/LLIS_RAGrets_Index.ipynb').read_text())
        self.assertEqual(notebook['nbformat'], 4)
        for cell in notebook['cells']:
            if cell['cell_type'] == 'code':
                compile(cell['source'], '<notebook>', 'exec')
                self.assertEqual(cell['outputs'], [])
                self.assertIsNone(cell['execution_count'])
        source = ''.join(cell['source'] for cell in notebook['cells'])
        self.assertIn(m.digest((ROOT / 'scripts/index_llis.py').read_bytes()), source)
        self.assertIn("userdata.get('GEMINI_API_KEY')", source)


if __name__ == '__main__':
    unittest.main()
