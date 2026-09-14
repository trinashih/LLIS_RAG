# LLIS-RAGrets
LLIS RAGrets: Ask NASA Before You Break It

A RAG learning project using NASA's public Lessons Learned Information System (LLIS).

[Read the project article: Everything Passed at the Factory](docs/EVERYTHING_PASSED_AT_THE_FACTORY.md)

## Run the notebook in Colab

The notebook uses **Gemini Embedding 2 (`gemini-embedding-2`)**, **Qdrant local**, and **Gemini 3.6 Flash (`gemini-3.6-flash`)** for answers. Use a CPU runtime; the embedding and answer models run through Google's API. Document embedding, query embedding, and answer generation consume API quota.

The [notebook](notebooks/LLIS_RAGrets_Index.ipynb) includes the executed experiment and the later question interface. The numbers below refer to its section headings and code comments; there are also two extra quota-recovery cells after section 6.

### 1. Create the API key

Open [Google AI Studio's API Keys page](https://aistudio.google.com/apikey), sign in, and create a key in a Google Cloud project. New users may already have a default project; existing users can select or import one. Note which project owns the key because that is where billing and quota apply. Google's [key setup guide](https://ai.google.dev/gemini-api/docs/api-key) covers the current screens.

To use paid API quota, open the project's billing setup in AI Studio, link a billing account, and complete the payment or prepayment steps shown for the account. The documented run started on free quota and finished after a **$5 credit top-up**. That deposit is not a measurement of the experiment's actual cost. [Google's billing instructions](https://ai.google.dev/gemini-api/docs/billing)

### 2. Open the notebook and give it access

Use [Open in Colab](https://colab.research.google.com/github/Trina0224/LLIS-RAGrets/blob/main/notebooks/LLIS_RAGrets_Index.ipynb). The repo was private when this walkthrough was written, so readers need repository access. If Colab cannot open it directly, download the `.ipynb` from GitHub and choose **File → Upload notebook** in Colab.

Choose **Runtime → Change runtime type**, with the hardware accelerator set to **None**. In the left sidebar, open **Secrets** using the key icon. Add a secret named exactly `GEMINI_API_KEY`, paste the key as its value, and enable **Notebook access** for this notebook. Access is granted separately for each notebook. Enable it again when opening a different copy.

The code reads it with `userdata.get('GEMINI_API_KEY')`. There is no reason to paste the key into a cell or commit it to GitHub.

### 3. Load the data and prepare the index

Download the repository through **Code → Download ZIP** on GitHub. Run notebook sections **1–4** in order. Authorize the Drive mount and upload the repository ZIP when prompted. The notebook saves that source package in Drive and checks the required file hashes before loading project code.

The ZIP already contains the saved raw response and normalized dataset. Readers can reproduce this snapshot without downloading NASA again. The earlier cleaning stage can also be rerun with `scripts/normalize_llis.py`; its inputs and commands are documented in [Data Preparation](docs/DATA_PREPARATION.md).

Section 4 should report 2,117 lessons and 2,394 vector inputs for this snapshot.

### 4. Embed, then build Qdrant

Run **section 5** for the two-input pilot, then **section 6** for the remaining inputs. Successful batches are saved to Drive before the next batch begins. Rerunning skips the vectors already there.

During the documented run, slowing requests allowed progress after an initial HTTP 429; a later error explicitly named a free-tier quota with a limit of 1,000. Sending more slowly does not solve every quota problem. Check the key's project on the [rate-limit page](https://ai.dev/rate-limit) before repeatedly restarting. Google's limits can apply to requests or tokens over different time windows. [Rate-limit documentation](https://ai.google.dev/gemini-api/docs/rate-limits)

The saved notebook contains the slower recovery settings used during that run, `api.min_interval = 15` and `batch_size=4`. Use those cells if pacing needs adjustment; skip them after a successful full run. Exhausted quota needs its own resolution.

Wait for **`2394/2394`** and **`complete: True`**, then run **section 7**. It builds Qdrant on Colab's local disk, checks the point count, closes the database, and archives it to Drive. This step does not need another document-embedding request.

### 5. Search, compare, and ask something of your own

**Section 8** displays search results and expandable original text. **Section 9** runs the six retrieval questions. Sections **10–12** add answer generation, run the two scenarios, and compare the revised evidence rules. They consume additional API usage for questions and answers.

The initial request to `gemini-2.5-flash` returned a 404 saying it was unavailable to new users and recommended `gemini-3.6-flash`. The notebook now uses that model for `ANSWER_MODEL`; this change did not require rebuilding the embeddings or database. Check model availability if reproducing this later.

Run **section 13** for the personal interface. Enter a complete question and choose the answer language. Retrieval was tested with English questions. The Japanese and Traditional Chinese options set the response language; this version does not add an automatic English query-translation stage. Each submission is independent and does not include previous conversation history.

Try the factory question in the [project article](docs/EVERYTHING_PASSED_AT_THE_FACTORY.md#the-factory-question), or describe a concern about an allegedly equivalent replacement part. There is no need to supply a lesson number. Expand the source records when an answer gives you something worth following up.

### 6. Save and restore the completed work

The default project folder is `My Drive/LLIS-RAGrets`. The run documented here is `922c0500b73b3811d81e116c`.

| Location under the project folder | Contents |
| --- | --- |
| `source.zip` | The verified repository source package used for the run |
| `runs/<run_id>/plan.json` and `chunks.jsonl.gz` | Index settings and the exact inputs |
| `runs/<run_id>/cache/` | Completed document vectors, with checksums |
| `runs/<run_id>/qdrant-index.zip` and `index_status.json` | Closed database archive and its verification metadata |
| `runs/<run_id>/retrieval-evaluation.json` | Measured six-question retrieval results |
| `runs/<run_id>/qa-history/` | JSON and Markdown saved by the question interface |

**Section 14** verifies the stored vectors and database archive, captures the test answers still in memory, and downloads a complete backup ZIP. The documented backup was about **120 MB**, including a Qdrant archive of about **40 MB**. These files contain data and vectors; Gemini itself runs at the API provider.

Keep the large backup in Drive and the code in GitHub. The export cell downloads to your computer; upload the complete ZIP to Drive separately if you want a second copy there. Use Colab's **File → Save a copy in GitHub** to preserve notebook changes, or **File → Download → Download .ipynb** for a local copy. Review cell outputs before sharing, since they can contain questions and answers.

After a runtime reset, mounting the same Drive and rerunning setup recovers the source and embedding cache. The embedding steps skip completed inputs, and section 7 can rebuild the local database from the cache without paying to embed the documents again. Search and answer cells still make API calls when rerun. Keep the live database on local disk and the closed archive on Drive.

## Current data status

The September 12, 2026 snapshot contains **2,127 index records: 2,117 identified lessons and 10 quarantined non-lesson records**. The complete raw search response is backed up; separately linked files are outside that backup's scope.

Normalization and its audit are complete. Missing fields, differing legacy text, tables and unloaded media remain explicitly recorded. This is not a claim that every lesson is complete or verified.

- [Raw snapshot and backup scope](data/raw/llis/README.md)
- [Data quality report](docs/DATA_QUALITY.md)
- [Preparation rules, schema and local commands](docs/DATA_PREPARATION.md)
- [Normalized dataset and manifest](data/processed/llis/2026-09-12T082701Z/)

```sh
python -m pip install -r requirements-data.txt -r requirements-index.txt
python scripts/normalize_llis.py
python -m unittest discover -s tests -v
```

Preparation runs offline on a regular computer. The indexing policy keeps 1,905 lessons whole and splits 212 longer lessons into multiple retrieval units, producing 2,394 vector inputs. Source variants and unloaded media stay flagged.

The completed Colab run includes retrieval, answer generation, and a personal question interface. Six retrieval smoke questions produced Hit@5 = 1.0 and MRR@5 = 0.9167. These small checks do not establish overall answer accuracy; source comparisons and observed limitations are discussed in the article. Paid document vectors and the Qdrant archive are stored in the owner's Google Drive; they are not included in a repository download.

[Detailed indexing policy, recovery and verification](docs/INDEXING.md)
