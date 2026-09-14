# Everything Passed at the Factory

*My first RAG project: LLIS-RAGrets, an engineering assistant with access to other people's lessons learned. Experiment run: September 12, 2026, Pacific time.*

“Everything passed at the factory.”

Apparently, the customer site was not informed.

I spend enough time dealing with systems after delivery to have a complicated relationship with that sentence. I want to believe it. I would also like the system in front of me to participate.

A passing result matters. So do the configuration, test conditions, acceptance criteria, and everything that happened between the test bench and installation. When something fails at the customer site, “we tested it” is the beginning of a conversation I still need to have.

While taking an IBM RAG course, I decided to build something I would actually want to question. I used public engineering cases from NASA's Lessons Learned Information System, or LLIS, and called the project **LLIS-RAGrets**. The company documents stayed out of it. Engineering has produced quite enough public material to get started.

My first useful surprise was that I did not need to know which NASA lesson to ask about. I could describe a familiar situation and let the application find relevant cases.

## Giving the model something to read

RAG stands for **Retrieval-Augmented Generation**. In this project, that means finding relevant records before asking a language model to answer. The retrieved text becomes part of the input for that particular answer.

I prepared a searchable collection and connected it to the answering model. The model's weights stay unchanged; each answer gets a fresh selection of records to read.

There are two phases:

| Phase | What my notebook does |
| --- | --- |
| Prepare the collection | Load the saved LLIS records, clean the text, choose retrieval units, generate embeddings, and store vectors with source information in Qdrant |
| Answer a question | Embed the question, search for similar vectors, retrieve the corresponding lesson text, and ask an LLM to answer with references |

Document preparation happens once for a given dataset and configuration. After that, each question needs its own embedding and an answer-generation request.

### How similarity finds a case I have never heard of

An embedding model converts text into a vector: a list of numbers arranged so that related meanings can occupy nearby positions in the model's learned space. In my configuration, each vector contains **3,072 numbers**. Those numbers are useful together; I cannot point at number 417 and label it “questionable factory acceptance.”

I used **Gemini Embedding 2**, through the Gemini API, to embed the documents and questions. The request formatting distinguishes documents from search queries. Using the same embedding model and compatible settings keeps them in the same space. [Google's embedding guide](https://ai.google.dev/gemini-api/docs/embeddings)

Qdrant compares them using **cosine similarity**. Imagine the vectors as arrows starting at the same point. Cosine similarity measures how closely their directions align: a smaller angle gives a higher score, regardless of arrow length. That is all the trigonometry I needed for this demo. [Qdrant documentation](https://qdrant.tech/documentation/manage-data/collections/)

This lets a question about equipment passing acceptance and failing in service retrieve a case about pressure transducers, even when the question never mentions transducers, water, or NASA.

The score is a ranking signal. One of my searches returned **0.828** for the leading case. That does **not** mean an 82.8% chance that the answer is correct. A nearby vector can point to a related topic without containing the answer I need. The search still returns nearest candidates when the collection has nothing sufficient to answer the question.

Vector search is the retrieval method I chose. RAG can also use keyword search or combine both.

## Short articles, several small complications

LLIS looked manageable. Many records describe an event, explain what was learned, and finish with recommendations, all in a short article. NASA makes these reviewed cases publicly accessible. [LLIS dataset description](https://catalog.data.gov/dataset/nasa-engineering-network-lessons-learned)

The download gave me **2,127 records**. Ten were not lessons at all, leaving **2,117 identified lessons** in the September 12, 2026 snapshot.

Then came the cleanup. Some fields contained HTML; others were missing, or had a legacy version with different text. Tables and image references needed handling too. I kept the raw response so I could trace a cleaned passage back to what was actually downloaded. The images and linked attachments are still references only. I put the full accounting in the [data quality report](DATA_QUALITY.md).

Chunking was a separate decision. I had no particular desire to cut the explanation of a failure away from its corrective action just to produce smaller pieces. Most of these cases were short enough to keep together, so I did.

| Result of the indexing policy | Count |
| --- | ---: |
| Lessons kept whole | 1,905 |
| Longer lessons split into multiple units | 212 |
| Total vector inputs | 2,394 |

For the longer records, the code groups paragraphs and tables while keeping section labels and source positions. A search can match one of those smaller passages, but the answering model gets the **full normalized text of the lesson**. Up to five distinct lessons go into an answer.

That choice fits this collection. The event and recommendations can usually sit in the same prompt without much fuss; I would reconsider it if I were working with thousand-page manuals instead.

## Colab, an API key, and a pause at 1,007

With the retrieval units decided, I needed somewhere to run the code. I used a CPU Colab runtime. Google would do the embedding through its API; Colab would prepare the requests, save the results, and run Qdrant locally. My laptop did not need to host either language model.

The [notebook opens directly in Colab](https://colab.research.google.com/github/Trina0224/LLIS-RAGrets/blob/main/notebooks/LLIS_RAGrets_Index.ipynb). To follow the same run, save a copy and choose **Runtime → Change runtime type → None** for the hardware accelerator. The repo was private when I wrote this, so access is required; downloading the `.ipynb` from GitHub and using **File → Upload notebook** is another way to open it.

I created my key in [Google AI Studio](https://aistudio.google.com/apikey). On its API Keys page, create a key in a Google Cloud project, or select an existing project. Keep track of that project: its quota and billing are what the notebook will use. Google's [key setup guide](https://ai.google.dev/gemini-api/docs/api-key) covers the account and project options.

Then I put the key in Colab's **Secrets** panel, using the key icon in the left sidebar. The name must be `GEMINI_API_KEY`, and **Notebook access** must be enabled for the notebook being used. That was the detail I had to get straight: saving a secret does not give every notebook access to it.

The code reads the secret with `userdata.get('GEMINI_API_KEY')`. The key stays out of the source code.

For the data, I downloaded **Code → Download ZIP** from GitHub and ran notebook sections **1–4**. These mount Google Drive, accept the uploaded ZIP, verify the required file hashes, install dependencies, and prepare the indexing plan. The raw response and normalized records are already in the repo. Anyone following this snapshot can use those files; the [preparation script and instructions](DATA_PREPARATION.md) are there for reproducing the cleaning stage too.

Section 4 gave me the expected 2,117 lessons and 2,394 vector inputs. Section 5 sent a two-input pilot request. Once that worked, section 6 started the rest.

For a while, the progress counter was reassuring. Then came HTTP 429.

Slowing the requests helped initially. Later, with 1,007 vectors saved, an error explicitly named a free-tier quota with a limit of 1,000. The saved-vector count and the provider's quota counter were clearly not the same thing. The error linked to [AI Studio's rate-limit view](https://ai.dev/rate-limit), where the limits for the key's project can be checked. Limits can apply to requests or tokens over different time windows, so a 429 alone does not tell me whether I should slow down or wait for quota to reset. [Google's rate-limit documentation](https://ai.google.dev/gemini-api/docs/rate-limits)

At that point I added **$5 of credit** and finished using paid Gemini API quota. To take that route, open billing setup for the key's project in AI Studio, link a billing account, and complete the payment or prepayment steps offered for the account. That $5 was a balance top-up; I have not isolated the experiment's actual cost. [Billing setup](https://ai.google.dev/gemini-api/docs/billing)

Fortunately, each successful batch was already saved to Drive. I reran the embedding cell and it continued from the cache. The notebook retains the slower recovery cells I used after section 6, with `api.min_interval = 15` and `batch_size=4`; they can be skipped if the main embedding cell completes successfully.

When the output reached **`2394/2394`** and **`complete: True`**, I ran section 7. It built Qdrant on Colab's local disk, checked the point count, and saved a closed database archive to Drive. No further document embedding was needed.

## A few tests before I trusted the interface

I could finally try a search. Section 8 of the notebook shows the nearest results and lets me expand the matching passage and full lesson text. For section 9, I started with six questions and checked where the expected lessons appeared:

| Question topic | Expected lesson | Position in the results |
| --- | --- | ---: |
| STS-27 OMS carrier panel separation | 6 | 1 |
| Mars Climate Orbiter software unit mismatch | 740 | 1 |
| Genesis gravity-switch redundancy | 1733 | 1 |
| Deep Space 1 risk management | 1033 | 1 |
| Adjacent identical cable connectors | 431 | 1 |
| Wiring damage after manufacturing | 30101 | 2 |

Five came back first. The wiring case came back second.

That gave me **Hit@5 = 1.0**, meaning every expected lesson appeared in the first five results, and **MRR@5 = 0.9167**, the average reciprocal rank. Another lesson, 1336, took first place for the wiring question. I would need to read it before calling that a mistake.

Six questions tell me something about retrieval. They leave plenty untested. The 25 offline software tests covered a different concern: whether preparation, source tracking, caching, interruption recovery, and Qdrant persistence worked. Some used synthetic vectors to exercise the code.

## An answer was a separate step

Up to that point, I had a working search. Section 10 adds the generation call: it takes the retrieved lessons, supplies them with the question, and asks an LLM to answer with lesson references.

I used **Gemini 3.6 Flash** for that job. The first attempt had actually named `gemini-2.5-flash`, which returned a 404 saying it was unavailable to new users and recommended `gemini-3.6-flash`. I changed `ANSWER_MODEL` and reran the cell. The vectors stayed exactly where they were.

That small interruption also demonstrated why the two models are separate choices. **Gemini Embedding 2** creates the vectors used to search my index. The answering model receives ordinary text after that search. I could replace it with an OpenAI model through its API by changing the generation call and credentials. Replacing the embedding model, on the other hand, would mean rebuilding a compatible document index and using matching query embeddings.

I kept Gemini for answers because its API key was already working. The saved notebook uses `gemini-3.6-flash`; check availability if reproducing this later.

Sections 11 and 12 are where I tried the answers and revised the evidence rules.

I wanted to see what happened with a question that sounded more like work. So I described rotating acceleration-switch boards, keeping two identical copies for redundancy, and skipping dynamic testing because the circuit had worked on an earlier vehicle. No mission name. It found Genesis anyway, plus cases about redundancy analysis and late design changes.

Then I asked for an exact mounting-bolt torque while withholding the drawing and fastener specifications. It refused to give me a number. Fair enough; I had made the missing information painfully obvious.

The explanations needed a closer look, though. The first version turned a historical 10% observation into a general statement about redundancy designs and supplied a torque equation absent from the retrieved text. I tightened the prompt: keep claims within their original scope, leave out outside formulas, and distinguish recorded recommendations from suggestions for my situation.

The percentage and equation disappeared on the next run. But the wording still slipped. Uncertainty about whether a preload could be reached became a claim that it could not be reached, and some recommendations became broad requirements.

I stopped there. The version was useful enough for me to work with, and I could open the source beside an answer. Another round on the same two questions might make those answers look better; I was more interested in what it would do with the next question.

After those tests, I ran section 13 to get a question box and an **Ask LLIS-RAGrets** button. The response-language selector offers English, Japanese, and Traditional Chinese. Retrieval was tested with English questions; the selector changes the answer language and does not add a query-translation stage. Each submission starts fresh, without previous conversation history.

That was the point where I could stop editing a Python string every time I wanted to ask something.

## The factory question

Once the notebook worked, I briefly ran out of things to ask. I thought I needed questions about known LLIS entries. Then I tried describing the sort of situation that made me want the tool in the first place:

> A supplier says every unit passed factory testing, but multiple failures appear after delivery and installation at the customer site.
>
> What LLIS cases could help us investigate the gap between factory acceptance and field performance? What evidence should we request before concluding whether the cause lies in manufacturing, test coverage, transportation, installation, or operating conditions?

The results included **[LLIS 528: Improperly Conceived Acceptance Tests and Original Specifications](https://llis.nasa.gov/lesson/528)**.

Thirteen pressure transducers had passed acceptance tests and met or exceeded the original specifications. When fresh water was first introduced as the test medium, two produced erratic outputs and another leaked through welded seams. The lesson identifies problems with the acceptance tests and original specifications.

That is a very useful case to find when someone has just sent me a passing report. It gives me a concrete reason to ask how acceptance conditions compare with actual use, without first deciding whose fault the failure must be.

Other retrieved cases covered supplier quality, shipping procedures, installation checks, and facility conditions. **[LLIS 1227](https://llis.nasa.gov/lesson/1227)** was particularly memorable: sixteen missing fasteners were discovered two days before shipment, despite operator and inspection sign-offs indicating that installation had occurred.

The paperwork had installed the bolts. The hardware was still waiting.

The generated answer organized the investigation into evidence I could request: acceptance procedures, source inspection records, packaging instructions, receiving checks, and installation records. That is the sort of help I wanted. A broad complaint had become a set of things to examine.

Then I opened the source records.

### Reading the sources

| Wording in the generated answer | What the retrieved text actually established |
| --- | --- |
| LLIS 528 passed **factory** acceptance tests | It passed acceptance tests; the record did not explicitly identify the test location |
| LLIS 1100 hardware **failed in use** | Defective soldering caused failures to meet performance and quality requirements, with schedule impacts; the passage did not establish an in-service failure |
| LLIS 1211 hardware integrity **was compromised in transit** | The record described packaging and monitoring discrepancies; it did not establish that transit damage had occurred |

The original records are [528](https://llis.nasa.gov/lesson/528), [1100](https://llis.nasa.gov/lesson/1100), and [1211](https://llis.nasa.gov/lesson/1211).

These changes are easy to miss because they make the answer fit my question more neatly. They also change the evidence. An omission in a shipping process is a reason to investigate damage, not proof that damage happened.

This was the most useful finding in the project. The retrieval stage could find a relevant record, and the generation stage could still add a few words that the record did not support. Checking that a citation exists would not catch that.

## Closing the notebook

The question interface saves each successful answer as JSON and Markdown under `My Drive/LLIS-RAGrets/runs/<run_id>/qa-history/`. I can read the `.md` later or use it for a discussion without rerunning the question.

The other files matter just as much. The `cache/` directory holds the document vectors I paid to generate, `plan.json` and `chunks.jsonl.gz` preserve the index settings and inputs, and `qdrant-index.zip` is the closed database archive. Section 14 checks the stored vectors and archive, includes the test answers still in memory, and downloads a complete backup ZIP.

Mine was about **120 MB**, including a Qdrant archive of about **40 MB**. I uploaded the downloaded backup to Drive and used Colab's **File → Save a copy in GitHub** for the notebook. **File → Download → Download .ipynb** also saves the code locally. I check the saved outputs before sharing because they can include my questions and answers.

On a fresh runtime, I can mount the same Drive, rerun setup, and rebuild the local Qdrant database from the saved cache. Completed document embeddings are skipped. New questions and answers still use the API, but the collection itself does not have to be embedded again.

The [README](../README.md#run-the-notebook-in-colab) keeps the cell order, recovery notes, and file locations together for the next time I open this and forget where I left off.

I did not run a controlled comparison against the same model without retrieval, so I cannot claim this beats an ordinary chat response on every question. What I can inspect here is which records were retrieved, what text was supplied, and whether the answer stayed faithful to it.

A brief note on the source material: NASA's published AI guidance allows factual disclosure that a tool uses NASA source material; it does not amount to a blanket prohibition on an LLIS RAG demo. The generated answers belong to this independent application and have not been reviewed or endorsed by NASA. I use source links without NASA branding. Third-party content can have separate rights, so public access is not a universal reuse license. [NASA's usage and AI guidance](https://www.nasa.gov/nasa-brand-center/images-and-media/)

I started this project expecting most of the difficulty to be in embeddings and the vector database. There was work in preparing the records, and the API supplied a few interruptions of its own. Once the questions started working, my attention moved to the gap between a source and the sentence written about it.

The tool is already useful to me. I can describe an engineering situation, find cases I did not know existed, and bring something more specific than frustration to the discussion. I still read the source before turning a suggestion into a requirement.

Next time I hear “everything passed,” I have a follow-up: “Can you show me what the test actually covered?”
