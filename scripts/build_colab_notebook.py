"""Generate the reviewed Colab entry point, including hashes of its required repo files."""
import hashlib
import json
from pathlib import Path
import textwrap

ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT = "2026-09-12T082701Z"
REQUIRED = ["scripts/index_llis.py", "requirements-index.txt", "tests/retrieval_cases.json",
            f"data/processed/llis/{SNAPSHOT}/lessons.jsonl.gz",
            f"data/processed/llis/{SNAPSHOT}/manifest.json"]


def build():
    cells = []

    def add(kind, source):
        cell = {"cell_type": kind, "metadata": {},
                "source": textwrap.dedent(source).strip() + "\n",
                "id": f"llis-cell-{len(cells):02d}"}
        if kind == "code":
            cell.update(execution_count=None, outputs=[])
        cells.append(cell)

    add("markdown", """
    # LLIS-RAGrets — Build and Search the NASA Index

    **CPU runtime · Gemini Embedding 2 · Qdrant local database**

    This notebook builds real embeddings in your Google account. It has not been pre-run with your API key.
    It returns NASA source material for inspection; answer generation is a later step.

    Before running:
    1. In Colab's left **Secrets** panel, enable **Notebook access** for `GEMINI_API_KEY`.
    2. Open the [private GitHub repo](https://github.com/Trina0224/LLIS-RAGrets), then **Code → Download ZIP**.
       This avoids needing a GitHub token. You upload this ZIP once below; subsequent sessions reuse it from Drive.
    3. Select **Runtime → Change runtime type → CPU**, then run the cells from top to bottom.
       You can use **Run all** and respond to the Drive and first-time ZIP upload dialogs.

    Calls use your Gemini API quota and may be billed if your API project is on a paid plan.
    Successful embedding batches are saved to Drive before the next batch starts.
    Run only one copy of this notebook against the same output folder at a time.
    """)
    add("markdown", """
    ## 1. Connect Google Drive

    The default project folder is `My Drive/LLIS-RAGrets`. Change it here if desired.
    Drive holds the source ZIP, vector checkpoints, finished database archive and search evaluation.
    The live database runs on Colab's local disk and can be rebuilt from those checkpoints.
    """)
    add("code", """
    from pathlib import Path
    from google.colab import drive
    drive.mount('/content/drive')
    PROJECT_DIR = Path('/content/drive/MyDrive/LLIS-RAGrets')
    PROJECT_DIR.mkdir(parents=True, exist_ok=True)
    REPO_DIR = Path('/content/llis-ragrets-source')
    print('Persistent project folder:', PROJECT_DIR)
    """)
    hashes = {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in REQUIRED}
    add("markdown", """
    ## 2. Load the private repo ZIP

    Select the ZIP downloaded from GitHub if asked. Required files are checksum-verified before any project code runs.
    If you use a newer notebook, download a fresh repo ZIP when prompted.
    """)
    bootstrap = """
    import hashlib, io, json, os, zipfile
    from google.colab import files
    EXPECTED_FILES = __HASHES__

    def verified_files(archive_bytes):
        with zipfile.ZipFile(io.BytesIO(archive_bytes)) as archive:
            roots = [n[:-len('scripts/index_llis.py')] for n in archive.namelist()
                     if n.endswith('/scripts/index_llis.py') or n == 'scripts/index_llis.py']
            if len(roots) != 1:
                raise ValueError('Choose the complete LLIS-RAGrets repository ZIP.')
            verified = {}
            for relative, expected in EXPECTED_FILES.items():
                member = archive.getinfo(roots[0] + relative)
                if member.file_size > 50_000_000:
                    raise ValueError('Unexpected source file size.')
                data = archive.read(member)
                if hashlib.sha256(data).hexdigest() != expected:
                    raise ValueError('Notebook and source ZIP differ. Download the repo ZIP matching this notebook.')
                verified[relative] = data
            return verified

    zip_path = PROJECT_DIR / 'source.zip'
    verified = None
    if zip_path.exists():
        try:
            verified = verified_files(zip_path.read_bytes())
        except (ValueError, KeyError, zipfile.BadZipFile):
            print('The saved ZIP does not match this notebook. Please upload a fresh repo ZIP.')
    if verified is None:
        uploaded = files.upload()
        if len(uploaded) != 1:
            raise ValueError('Upload exactly one repository ZIP, then rerun this cell.')
        archive_bytes = next(iter(uploaded.values()))
        verified = verified_files(archive_bytes)
        temporary = zip_path.with_suffix('.zip.tmp')
        temporary.write_bytes(archive_bytes)
        os.replace(temporary, zip_path)
        del uploaded, archive_bytes
    for relative, data in verified.items():
        destination = REPO_DIR / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)
    del verified
    print('Source files verified and ready.')
    """.replace("__HASHES__", repr(hashes))
    add("code", bootstrap)
    add("markdown", "## 3. Install dependencies and connect the API\n\nThe API key is read from Secrets and never printed or saved.")
    add("code", """
    import subprocess, sys, importlib.util
    subprocess.run([sys.executable, '-m', 'pip', 'install', '-q', '-r', str(REPO_DIR / 'requirements-index.txt')], check=True)
    spec = importlib.util.spec_from_file_location('index_llis', REPO_DIR / 'scripts/index_llis.py')
    indexer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(indexer)
    from google.colab import userdata
    try:
        api = indexer.GeminiClient(userdata.get('GEMINI_API_KEY'), min_interval=3.0)
    except Exception:
        raise RuntimeError('Open Colab Secrets and enable Notebook access for GEMINI_API_KEY, then rerun.') from None
    print('API key loaded. No model request has been made yet.')
    """)
    add("markdown", """
    ## 4. Prepare the indexing plan — offline

    Short lessons stay whole. Longer lessons are grouped by paragraphs with section names and source offsets.
    The 1,200-word and 20,000-byte limits below are **chunking budgets, not token counts**.
    Gemini requests disable automatic truncation, so oversized inputs fail explicitly instead of silently losing text.
    Source media markers and quality flags remain visible; no image, attachment or PDF has been fetched.
    """)
    add("code", f"""
    lessons, dataset_sha = indexer.load_lessons(REPO_DIR / 'data/processed/llis/{SNAPSHOT}')
    plan = indexer.make_plan(lessons, dataset_sha, max_words=1200, max_input_bytes=20000)
    RUN_DIR = indexer.save_plan(plan, PROJECT_DIR / 'runs')
    m = plan['manifest']
    print(f"Lessons: {{m['indexed_lessons']}}; kept whole: {{m['whole_lessons']}}; split: {{m['split_lessons']}}; vectors to build: {{m['chunks']}}")
    print('Excluded lessons:', m['excluded'])
    print('Large table fragments requiring source context:', m['table_fragments'])
    print('Checkpoints:', RUN_DIR)
    """)
    add("markdown", """
    ## 5. Pilot request — up to two new embeddings

    This checks real model access, request format and vector dimensions before the full run.
    If it fails, share the error message, **not your API key**. Do not change the model name to continue:
    query and document embeddings must use the same configured model.
    """)
    add("code", """
    pilot = indexer.embed_pending(plan, api, RUN_DIR, batch_size=2, max_new=2)
    print('Pilot completed; successful vectors are saved.', pilot)
    """)
    add("markdown", """
    ## 6. Embed the remaining lessons

    Rerunning skips saved vectors. HTTP 429 and temporary server failures receive bounded retries.
    If the cell stops because quota is exhausted, resume this cell when quota is available.
    Free-tier speed and completion time depend on your project's current limits.
    A disconnect between a successful API response and saving its checkpoint can repeat that last batch.
    """)
    add("code", """
    embedding_status = indexer.embed_pending(plan, api, RUN_DIR, batch_size=16)
    print(embedding_status)
    """)
    add("markdown", """
    ## 7. Build and back up Qdrant — offline

    This requires all vectors to be present. It rebuilds from the persistent cache, verifies the point count,
    closes the database, and saves `qdrant-index.zip` with a checksum in Drive.
    No additional embedding requests are needed to rebuild after a Colab reset.
    """)
    add("code", """
    INDEX_DIR = indexer.build_index(plan, RUN_DIR, Path('/content/llis-qdrant'))
    print('Qdrant database ready:', INDEX_DIR)
    print('Persistent database archive:', RUN_DIR / 'qdrant-index.zip')
    """)
    add("markdown", """
    ## 8. Search in English

    Edit `QUESTION` and rerun. Results are unique lessons ranked by their best matching chunk.
    Similarity scores are not confidence probabilities. This stage shows source evidence, not a generated answer.
    """)
    add("code", """
    QUESTION = 'Why did redundant gravity switches fail to protect the Genesis sample return capsule?'
    hits = indexer.search(plan, api, INDEX_DIR, QUESTION, top_k=5)
    from IPython.display import HTML, display
    import html
    for rank, hit in enumerate(hits, 1):
        context = indexer.lesson_context(lessons, hit['lesson_id'])
        display(HTML(
            f"<h3>{rank}. <a href='{html.escape(hit['url'], quote=True)}' target='_blank'>{html.escape(hit['title'])}</a></h3>"
            f"<p>Lesson {html.escape(hit['lesson_id'])} · Similarity {hit['score']:.3f}</p>"
            f"<p>Source flags: {html.escape(', '.join(hit['quality_flags']))}</p>"
            f"<details><summary>Matched passage</summary><pre style='white-space:pre-wrap'>{html.escape(hit['text'])}</pre></details>"
            f"<details><summary>Full lesson text</summary><pre style='white-space:pre-wrap'>{html.escape(context['text'])}</pre></details>"
        ))
    """)
    add("markdown", """
    ## 9. Check six source-grounded retrieval questions

    These are smoke checks selected from inspected LLIS records, not an independent benchmark.
    Each question makes one query-embedding request. Results are saved to Drive, including misses.
    No target score is assumed; the report is evidence for the next iteration.
    """)
    add("code", """
    cases = json.loads((REPO_DIR / 'tests/retrieval_cases.json').read_text())
    evaluation = indexer.evaluate(plan, api, INDEX_DIR, cases, RUN_DIR / 'retrieval-evaluation.json')
    print('Hit@5:', evaluation['hit_at_5'], 'MRR@5:', evaluation['mrr_at_5'])
    for result in evaluation['results']:
        print('PASS' if result['hit_at_5'] else 'MISS', result['question'], result['retrieved_ids'])
    print('Saved:', RUN_DIR / 'retrieval-evaluation.json')
    """)
    add("markdown", """
    ## Finished

    Keep `My Drive/LLIS-RAGrets`: it contains the reusable vectors, Qdrant archive, source ZIP and evaluation.
    To resume after a reset, reopen this notebook, enable Secret access if needed, and run from the top.
    Model, dimensions, dataset and chunking settings are fingerprinted: a changed configuration gets a separate run.

    Next project stage: build grounded answer generation on top of the retrieved NASA evidence.
    [Indexing documentation](https://github.com/Trina0224/LLIS-RAGrets/blob/main/docs/INDEXING.md)
    """)
    notebook = {"nbformat": 4, "nbformat_minor": 5, "metadata": {
        "colab": {"name": "LLIS_RAGrets_Index.ipynb", "provenance": []},
        "kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"},
        "language_info": {"name": "python", "version": "3.12"}}, "cells": cells}
    output = ROOT / "notebooks/LLIS_RAGrets_Index.ipynb"
    output.parent.mkdir(exist_ok=True)
    output.write_text(json.dumps(notebook, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(output)


if __name__ == '__main__':
    build()
