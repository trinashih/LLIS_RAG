# Colab indexing and retrieval

[Open the notebook in Colab](https://colab.research.google.com/github/Trina0224/LLIS-RAGrets/blob/main/notebooks/LLIS_RAGrets_Index.ipynb)

The code is implemented and tested offline. **Real Gemini embeddings and retrieval scores are not precomputed in this repo.** The owner runs the notebook using their Gemini API key; the two-vector pilot checks the live API before the full corpus runs.

## Run it

1. Open the notebook above. Because this repository is private, Colab may ask you to authorize GitHub access. Alternatively, download `notebooks/LLIS_RAGrets_Index.ipynb` from GitHub and use **File → Upload notebook** in Colab.
2. In the notebook's left **Secrets** panel, enable **Notebook access** for `GEMINI_API_KEY`. Use a key created in [Google AI Studio](https://aistudio.google.com/apikey). Never put it in a notebook cell or repo file.
3. Download the private repository ZIP from **Code → Download ZIP** on GitHub. No GitHub token is required by the notebook.
4. Select a **CPU runtime** and run the notebook from the top. Respond to the Google Drive permission dialog and upload the repo ZIP when asked. The ZIP is saved to Drive for subsequent sessions.
5. Inspect the two-vector pilot, full embedding progress, Qdrant completion, search results and the six-question retrieval report.

The notebook verifies hashes of the exact source code, dependencies, dataset and evaluation questions before running project code. After an update, use the matching notebook and repo ZIP. To regenerate the notebook's pins after changing a required source file, run `python scripts/build_colab_notebook.py`.

## What is indexed

Input: the normalized `2026-09-12T082701Z` snapshot, SHA-256 `a42936920af13c250c009c1e7592fdd1a479f8e85b6983d4cf81ba1ebb38dbf9`.

The default offline plan produces:

| Item | Count |
| --- | ---: |
| Identified lessons included | 2,117 |
| Lessons kept as one unit | 1,905 |
| Longer lessons split | 212 |
| Total vector inputs | 2,394 |
| Excluded identified lessons | 0 |
| Oversized tables fragmented | 0 |

The ten non-lesson query records remain quarantined upstream. The indexing script validates the normalized file's checksum, size, count, IDs and source snapshot identity.

The document input is `title: {NASA title} | text: LLIS lesson {id}` followed by the available abstract, event, lesson, recommendation, evidence and related-policy sections. Missing NASA titles use `none` in the embedding title field; the generated display title remains labeled upstream. Exact reference-only sections such as “Same as in Lesson Learned” are omitted from embedding input but remain in the full lesson returned for inspection. Raw metadata and alternate legacy fields are not blindly appended.

Short lessons stay whole. Longer lessons are packed from paragraphs and tables, preserving section labels and character offsets into the normalized text. Tables stay together when they fit. An exceptionally oversized block can split at line, sentence, whitespace or character boundaries; table fragments are explicitly marked and retain access to their full source. The default snapshot creates no table fragments. Ordinary long articles can still contain multiple subtopics; this is a first policy to evaluate, not an assertion of optimal segmentation.

The default limits are 1,200 whitespace-separated units and 20,000 UTF-8 bytes **including the document prefix**. These are local chunking budgets, not exact Gemini token counts. The largest actual input in this plan is 9,189 bytes. The API requests set `autoTruncate: false`; the provider enforces its token limit and an oversized input stops the run rather than losing its tail. Usage metadata is retained when returned by the API. No surrogate model's tokenizer is presented as Gemini's exact tokenizer.

Paragraph spans retain their source field name, raw JSON Pointer and original field hash from normalization. Source HTML remains in the raw backup. The full normalized lesson is available alongside every result, including original field-reference text omitted from the vector input.

## Model and vector database

- Model: `gemini-embedding-2` for both documents and queries.
- Vector size: **3,072**, float vectors, explicitly normalized and validated.
- Query input: `task: search result | query: {English question}`.
- API: synchronous `batchEmbedContents`, with one separate request object per document. Several chunks are never accidentally aggregated into one vector.
- Request config: `embedContentConfig.outputDimensionality = 3072`, `autoTruncate = false`. The `taskType` field is not used with Embedding 2.
- Database: **Qdrant local**, pinned client version 1.15.1, cosine similarity. Retrieval requests exact search for this small corpus.
- Results: unique lesson IDs ranked by their best matching chunk. Candidate depth increases when needed so a long lesson does not consume all result slots.

This is a real Qdrant database, not a mock vector store. No hosted Qdrant account, GPU or Colab paid subscription is needed for the supplied workflow. The pinned dependencies target Python 3.11 or newer; development tests ran with Python 3.12.

The API implementation follows [Google's embedding guide](https://ai.google.dev/gemini-api/docs/embeddings) and [REST reference](https://ai.google.dev/api/embeddings). The live pilot is still required to validate account access and any service changes. Temporary HTTP failures and rate limits get bounded retries; persistent authentication, quota or request errors stop with a useful message. The key is carried in a header, never in URLs, caches or logs.

## Persistence and resume

The default Drive folder is `My Drive/LLIS-RAGrets`. Under it:

| Artifact | Purpose |
| --- | --- |
| `source.zip` | Verified source package; reuse after a Colab reset |
| `runs/<run_id>/plan.json` | Model, dimensions, dataset hash, input fingerprint and counts |
| `runs/<run_id>/chunks.jsonl.gz` | Exact vector inputs and source spans |
| `runs/<run_id>/cache/*.json.gz` | Checksummed successful API batches, vectors and reported usage |
| `runs/<run_id>/embedding_status.json` | Most recently completed embedding invocation's status |
| `runs/<run_id>/qdrant-index.zip` | Closed, portable Qdrant database with the matching plan |
| `runs/<run_id>/index_status.json` | Verified point count and database-archive checksum |
| `runs/<run_id>/retrieval-evaluation.json` | Actual query results and measured smoke-test scores |

Every successful embedding batch is written to Drive and read back before the next batch starts. The cache files, rather than the summary status file, determine what is complete after an interruption. Changing the dataset, model, dimension or chunking policy creates a separate run. Different batch sizes or a two-vector pilot do not prevent reuse of completed vectors.

Keep only one active notebook writing a given run. Corrupted or duplicate cache entries stop processing for diagnosis instead of silently causing re-embedding. A request completed by Google but lost before its checkpoint was saved can be repeated; no client can guarantee exactly-once billing in that interval.

The live Qdrant directory is on Colab's local disk, not the Drive mount. On completion the client is closed and the directory is archived to Drive with a checksum. If Colab's runtime is reset, run the notebook again: cached embeddings are reused and Qdrant is rebuilt offline. No extra document embedding requests are needed for that rebuild. Query requests and the evaluation still use API quota when rerun.

The archive can also be extracted onto local disk and opened with the pinned Qdrant client. Verify `index_status.json`'s archive checksum first. The source ZIP and caches are the reproducible recovery path; avoid opening the live SQLite-backed store on a mounted cloud drive.

## Verification and limits

Run offline tests with:

```sh
python -m pip install -r requirements-data.txt -r requirements-index.txt
python scripts/build_colab_notebook.py
python -m unittest discover -s tests -v
```

Tests cover full-corpus source-span coverage, deterministic IDs, interrupted embedding/resume, cache corruption, real Qdrant persistence/export/reopen/search, vector validation, independent API inputs, retry behavior and key redaction. They use synthetic vectors and a mocked HTTP transport to test correctness. **Those tests do not measure Gemini retrieval accuracy.**

The notebook's six English questions come from inspected lessons 6, 431, 740, 1033, 1733 and 30101. Hit@5 and MRR@5 are measured only when the owner runs them against real embeddings. These are smoke checks, not a representative benchmark, and other relevant lessons may exist beyond the listed expected IDs. Scores are not fabricated or assumed to pass.

Retrieval always returns nearest candidates; similarity is not a probability that the source answers the question. This version does not implement answer generation, automatic abstention, reranking, multilingual translation or a public application. Source text still contains missing fields, variants and possible inconsistencies: lesson 431, for example, has an abstract/body disagreement about the mission associated with 26 damaged transistors. Images and attachments have not been fetched or interpreted. Historical standards cited in a lesson must not be presented as current requirements without checking the standards themselves.

The next step after inspecting real retrieval results is grounded answer generation using the retrieved original text and source links.
