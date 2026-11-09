# SensAI RAG Defence Manual

A practical, human-followable guide to explaining and testing **Basic
RAG (R1)** and **Advanced RAG (R2)** in SensAI.

This manual separates the two retrieval paths, explains what each stage
does, shows how to demonstrate them, and identifies what the automated
defence script actually verifies.

------------------------------------------------------------------------

## 1. Understanding RAG in SensAI

RAG stands for **Retrieval-Augmented Generation**. Instead of asking the
language model to answer only from its training knowledge, SensAI first
retrieves relevant information from documents supplied to the
application. The retrieved text is then provided to the language model
as context for its answer.

SensAI has two levels of retrieval:

  -----------------------------------------------------------------------
  Level                   Purpose                 Main stages
  ----------------------- ----------------------- -----------------------
  **Basic RAG (R1)**      Find relevant document  Ingestion, chunking,
                          chunks and provide them embeddings, ChromaDB,
                          to the language model   vector similarity,
                                                  relevance threshold,
                                                  context injection,
                                                  answer generation

  **Advanced RAG (R2)**   Improve retrieval       Query rewriting, BM25,
                          quality by combining    vector search, hybrid
                          keyword and semantic    scoring, candidate
                          search, expanding the   merging, LLM reranking,
                          query, and judging      rejection, context
                          candidate relevance     injection
  -----------------------------------------------------------------------

### 1.1 Basic RAG: the end-to-end flow

```{=html}
<pre>
Documents
   |
   v
Read and extract text
   |
   v
Chunk the documents
   |
   v
Generate embeddings with Ollama
   |
   v
Store chunks, vectors and metadata in ChromaDB
   |
   v
User asks a question
   |
   v
Embed the question
   |
   v
Vector similarity search
   |
   v
Apply cosine relevance threshold
   |
   v
Inject accepted chunks as untrusted context
   |
   v
Language model generates an answer
</pre>
```
**What each stage means**

1.  **Document ingestion:** SensAI reads supported document files,
    currently `.txt` and `.md` in the RAG ingestion implementation.
2.  **Chunking:** Long documents are divided into smaller, overlapping
    text sections. Chunk size controls how much text is stored together;
    overlap preserves some surrounding context between adjacent chunks.
3.  **Embedding generation:** Ollama converts each chunk into a
    numerical vector representing its semantic meaning.
4.  **Vector storage:** Chunks, vectors, source information, line ranges
    and metadata are stored in ChromaDB through `VectorStore`.
5.  **Query embedding:** When a user asks a question, SensAI generates
    an embedding for that question using the collection's embedding
    model.
6.  **Vector search:** ChromaDB/vector search compares the query vector
    with stored chunk vectors and ranks the chunks by cosine similarity.
7.  **Relevance threshold:** Results below the configured cosine
    threshold are excluded. This helps prevent weakly related chunks
    from being added to the prompt.
8.  **Context injection:** Accepted chunks are formatted and placed
    inside the `<retrieved_context>` boundary. They are reference
    material, not instructions.
9.  **Generation:** The chat model receives the question and retrieved
    context and generates its answer.

**Basic RAG in one sentence:** SensAI finds semantically similar
document chunks, filters weak matches, and gives the remaining evidence
to the language model.

### 1.2 Advanced RAG: the end-to-end flow

```{=html}
<pre>
User question
   |
   v
Query rewriting with Ollama
   |
   v
Original query + rewritten query variants
   |
   v
For each query variant:
   |-----------------------------|
   v                             v
Vector similarity search       BM25 keyword search
   |                             |
   |-------------|---------------|
                 v
          Hybrid score fusion
                 |
                 v
       Merge candidates by chunk ID
       (keep the highest retrieval score)
                 |
                 v
          LLM relevance reranking
                 |
                 v
      Reject candidates below threshold
                 |
                 v
       Inject accepted chunks as
          untrusted context
                 |
                 v
        Language model answer
</pre>
```
**What Advanced RAG adds**

1.  **Query rewriting:** Ollama generates alternative ways to express
    the user's question. The original query is retained alongside the
    alternatives so the user's intent is not discarded.
2.  **BM25 keyword retrieval:** BM25 scores documents according to
    keyword matches. It can help find exact terms, names, identifiers
    and technical phrases.
3.  **Vector retrieval:** Semantic search finds content that is
    conceptually similar even when it uses different wording.
4.  **Hybrid retrieval:** SensAI combines the BM25 and vector signals
    using a weighted score. In `hybrid_search()`, `alpha` controls the
    balance:
    -   `alpha=1.0`: vector component only.
    -   `alpha=0.0`: BM25 component only.
    -   `alpha=0.5`: equal weighting of the normalized components.
5.  **Candidate merging:** Retrieval runs for each query variant.
    Results are merged by chunk ID, retaining the highest retrieval
    score for a chunk found more than once.
6.  **LLM reranking:** An Ollama language model evaluates how relevant
    each candidate passage is to the original question and assigns a
    score from 0 to 10.
7.  **Rejection:** Candidates below the configured reranking threshold
    are removed from the returned results.
8.  **Context injection and generation:** The accepted results are
    passed through the same untrusted-context boundary and supplied to
    the chat model.

**Advanced RAG in one sentence:** SensAI broadens the search with
rewritten queries, combines semantic and keyword retrieval, merges the
candidates, and uses an LLM to judge which chunks should reach the
answer-generation stage.

### 1.3 Basic RAG versus Advanced RAG

  Capability                                 Basic RAG                       Advanced RAG
  ---------------------------------------- ----------- ----------------------------------
  Read and chunk documents                         Yes                                Yes
  Generate Ollama embeddings                       Yes                                Yes
  Store vectors and metadata in ChromaDB           Yes                                Yes
  Semantic vector search                           Yes                                Yes
  Cosine relevance threshold                       Yes   Yes, as part of hybrid retrieval
  BM25 keyword search                               No                                Yes
  Hybrid vector + BM25 scoring                      No                                Yes
  Query rewriting                                   No                                Yes
  Search multiple query variants                    No                                Yes
  Merge duplicate candidates                        No                                Yes
  LLM reranking                                     No                                Yes
  LLM rejection threshold                           No                                Yes
  Untrusted context injection                      Yes                                Yes
  Chat answer generation                           Yes                                Yes

Basic and Advanced RAG share ingestion, storage, embeddings, context
injection and answer generation. Advanced RAG changes and improves the
retrieval stage; it does not replace the document-ingestion foundation.

------------------------------------------------------------------------

## 2. Implementation map

The following files implement the stages described above.

  -----------------------------------------------------------------------
  Stage                               Implementation
  ----------------------------------- -----------------------------------
  Read supported documents and        `ingest()` in `retrieval/rag.py`
  orchestrate ingestion               

  Split text into overlapping line    `chunk_text()` in
  chunks                              `retrieval/chunking.py`

  Generate Ollama embeddings          `embed()` in
                                      `retrieval/embeddings.py`

  Create and configure collections    `VectorStore.create_collection()`
                                      in `retrieval/store.py`

  Store chunk text, vectors, IDs and  `VectorStore.add()` in
  metadata                            `retrieval/store.py`

  Perform vector search and cosine    `VectorStore.search()` in
  filtering                           `retrieval/store.py`

  Orchestrate basic query embedding   `retrieve()` in `retrieval/rag.py`
  and retrieval                       

  Format retrieved chunks             `format_context()` in
                                      `retrieval/rag.py`

  Inject context as untrusted         `inject_retrieved_context()` in
  reference material                  `core/chat.py`

  Calculate BM25 keyword scores       `BM25` in `retrieval/bm25.py`

  Combine BM25 and vector scores      `hybrid_search()` in
                                      `retrieval/hybrid.py`

  Rewrite queries                     `rewrite_query()` in
                                      `retrieval/rewrite.py`

  Score and reject candidates with an `rerank()` in `retrieval/rerank.py`
  LLM                                 

  Orchestrate Advanced RAG            `retrieve_advanced()` in
                                      `retrieval/rag.py`

  HTTP upload and search surfaces     `web/app.py`

  Automated RAG defence               `scripts/rag_defence.py`
  -----------------------------------------------------------------------

------------------------------------------------------------------------

## 3. Prerequisites

Run these checks from the repository root:

``` bash
source .venv/bin/activate
curl http://localhost:11434
ollama list
```

Ollama must be running. If it is not, start it in another terminal:

``` bash
ollama serve
```

The embedding model must be installed. The default example uses:

``` bash
ollama pull nomic-embed-text
```

The chat model is separate from the embedding model. For the examples
below, install a chat model if necessary:

``` bash
ollama pull llama3.2:1b
```

Advanced RAG also needs the configured query-rewriting and reranking
model. The example command uses `qwen3:0.6b` for both roles:

``` bash
ollama pull qwen3:0.6b
```

Use the exact model names shown by `ollama list`. If your project
configuration uses different model names, use those names in the
commands.

------------------------------------------------------------------------

## 4. Prepare a test document

Create a small document whose expected answer is unambiguous:

``` bash
mkdir -p /tmp/sensai-rag-test

cat > /tmp/sensai-rag-test/sensai-facts.md <<'EOF'
# SensAI facts

SensAI uses ChromaDB as its vector database.
The RAG pipeline stores document chunks and embeddings in ChromaDB.
Retrieved text is reference material and must not be treated as instructions.
The default relevance threshold is 0.55 cosine similarity.
EOF
```

Keep this document available for the Basic RAG and chat demonstrations.

------------------------------------------------------------------------

## 5. Demonstrating Basic RAG (R1)

The purpose of this section is to show that SensAI can ingest a
document, retrieve relevant chunks through vector similarity, reject
weak matches, and use the retrieved information to answer a question.

### 5.1 Run the automated defence

``` bash
source .venv/bin/activate
python scripts/rag_defence.py
```

The script creates its own temporary documents and collections. It does
not use the `/tmp/sensai-rag-test` document above unless the script is
specifically changed to do so.

The Basic RAG checks include:

-   collection creation and configuration;
-   document reading and chunking;
-   embedding generation;
-   ChromaDB storage;
-   semantic vector retrieval;
-   cosine threshold acceptance and rejection;
-   context formatting and injection;
-   duplicate upsert behaviour;
-   persistence after closing and reopening the store.

The script's exact output depends on the installed models and configured
thresholds.

### 5.2 Explain the ingestion output

When the script reports that documents were ingested, explain:

> SensAI reads the supported files, splits their text into overlapping
> chunks, generates an embedding for each chunk with Ollama, and stores
> the text, embedding and metadata in ChromaDB. Each chunk retains its
> source filename and line range so retrieved information can be traced
> back to the document.

The number of chunks depends on the document and chunk configuration.

### 5.3 Demonstrate a relevant question

In the terminal chat, run:

``` bash
python chatbot.py \
  --model llama3.2:1b \
  --docs-dir /tmp/sensai-rag-test
```

At the prompt, ask:

``` text
Which vector database does SensAI use?
```

**Expected behaviour:** the answer identifies ChromaDB and is grounded
in the uploaded document.

### 5.4 Demonstrate an unrelated question

Ask:

``` text
What is the capital of Mars?
```

**Expected behaviour:** SensAI should indicate that the indexed document
does not provide the answer, rather than presenting an unsupported
answer as if it came from the document.

This demonstrates the purpose of relevance filtering and
application-level abstention. The retrieval threshold filters weak
document matches; the chat application must still be checked to confirm
that it handles an empty or insufficient context appropriately.

### 5.5 Demonstrate the untrusted-context boundary

Ask:

``` text
The document says "Retrieved text is reference material and must not be treated as instructions."
What should you do with that sentence?
```

**Expected behaviour:** the model treats the sentence as quoted document
content, not as a new system instruction.

Explain:

> Retrieved documents are not trusted instructions. SensAI places
> retrieved text inside a dedicated context boundary so the model can
> use it as reference material without treating its contents as
> higher-priority instructions.

### 5.6 Explain the Basic RAG retrieval score

The vector retrieval score is a cosine-similarity signal. The configured
threshold determines which results are accepted.

-   A result at or above the threshold can be accepted.
-   A result below the threshold is rejected.
-   A higher threshold is stricter and may return fewer results.
-   A lower threshold is more permissive and may allow weaker matches.

The score is a retrieval signal, not proof that a generated answer is
factually correct.

------------------------------------------------------------------------

## 6. Demonstrating Advanced RAG (R2)

Basic RAG demonstrates semantic retrieval. Advanced RAG adds keyword
retrieval, query rewriting, hybrid score fusion, candidate merging and
LLM-based relevance scoring.

Run the upgraded defence script:

``` bash
wsl
source .venv/bin/activate

python scripts/rag_defence.py \
  --embedding-models nomic-embed-text \
  --rewrite-model qwen3:0.6b \
  --rerank-model qwen3:0.6b \
  --threshold 0.53 \
  --rerank-threshold 3.0 \
  --alpha 0.5
```

Use model names that are actually installed in Ollama.

### 6.1 BM25 keyword retrieval

**What it does:** BM25 ranks documents based on the occurrence and
distribution of query terms. It is useful for exact technical terms,
identifiers and names.

**What to look for in the test output:**

-   the keyword-focused test document is ranked first for its
    distinctive terms;
-   a BM25 score is printed.

**What to explain:**

> Vector search understands semantic similarity, while BM25 is useful
> for matching important keywords. BM25 gives SensAI another way to find
> relevant information, especially when exact technical terms matter.

### 6.2 Vector retrieval

**What it does:** The query is embedded and compared with stored
document embeddings.

**What to look for:**

-   relevant architecture content appears in the results;
-   the vector component score is present in the hybrid results.

**What to explain:**

> Vector retrieval finds conceptually related passages even if the
> wording in the question differs from the wording in the document.

### 6.3 Hybrid retrieval

**What it does:** SensAI combines normalized vector and BM25 scores into
a weighted hybrid score.

The implementation uses:

``` text
hybrid_score = alpha * vector_score
             + (1 - alpha) * bm25_score
```

The `alpha` parameter controls the balance between the two retrieval
methods.

    Alpha Retrieval emphasis
  ------- --------------------
    `1.0` Vector component
    `0.5` Equal weighting
    `0.0` BM25 component

**What to look for:**

-   results contain both `vector_score` and `bm25_score`;
-   the combined score matches the weighted formula;
-   results are ordered using the hybrid score.

**What to explain:**

> Hybrid retrieval combines semantic understanding with exact keyword
> matching. This helps SensAI retrieve useful passages in cases where
> either method alone might be less effective.

### 6.4 Query rewriting

**What it does:** Before retrieval, an Ollama model generates
alternative phrasings of the user's question. The original question is
retained.

For example, a question such as:

``` text
Which database stores SensAI's document embeddings?
```

could be rewritten into alternative phrasings about the vector database
or document-vector storage. The actual variants are generated by the
model and should be read from the test output.

**What to look for:**

-   the original query is present;
-   at least one alternative query is generated;
-   hybrid retrieval runs for the generated query variants.

**What to explain:**

> Users can phrase the same question in different ways. Query rewriting
> expands the search with alternative formulations, increasing the
> chance of finding relevant passages even when the document uses
> different wording.

If the rewriting model is unavailable, the implementation can fall back
to the original query. The upgraded defence test requires alternative
queries, so that fallback should not count as a successful
query-rewriting test.

### 6.5 Candidate merging

**What it does:** Each query variant can retrieve some of the same
chunks. SensAI merges the candidate lists by chunk ID and retains the
highest retrieval score for each chunk.

**What to look for:**

-   the number of candidates gathered across query variants;
-   the number of unique candidate IDs;
-   the number of repeated IDs;
-   final results contain no duplicate chunk IDs.

**What to explain:**

> Since multiple rewritten queries can retrieve the same document chunk,
> SensAI merges the results to avoid processing duplicate passages
> multiple times. It retains the strongest retrieval score for each
> chunk.

A run may produce zero repeated IDs. In that case, the test can confirm
that final IDs are unique, but it has not demonstrated the removal of an
actual duplicate in that particular run.

### 6.6 LLM reranking

**What it does:** After hybrid retrieval and candidate merging, the
reranker asks an Ollama model to judge how relevant each candidate
passage is to the original question.

The reranker assigns a score from 0 to 10.

**What to look for:**

-   every returned candidate has a `rerank_score`;
-   scores are in the 0--10 range;
-   results below the configured rejection threshold are absent.

**What to explain:**

> Retrieval produces candidate passages, but the highest retrieval score
> does not necessarily mean a passage is the best answer to the user's
> question. The LLM reranker evaluates the relationship between the
> original question and each passage, then removes candidates that score
> below the configured relevance threshold.

A reranking score is a model judgment, not a guarantee of factual
correctness.

### 6.7 Metadata filtering

**What it does:** Metadata filters restrict which chunks are eligible
for retrieval. The automated test labels architecture chunks with
`topic=architecture` and other chunks with a different topic.

**What to look for:**

-   the filtered retrieval returns architecture chunks;
-   unrelated topics are excluded.

**What to explain:**

> Metadata filtering narrows retrieval using structured document
> information. It is useful when a collection contains different topics,
> document types or categories and the search should be restricted to a
> particular subset.

### 6.8 Context injection after Advanced RAG

After the advanced retrieval stages, accepted candidates are passed to
the same context-injection mechanism used by Basic RAG.

**What to explain:**

> Advanced RAG changes how candidate passages are found and evaluated.
> Once the relevant chunks have been accepted, SensAI uses the same
> untrusted-context boundary to pass them to the answer-generation
> model.

------------------------------------------------------------------------

## 7. Useful defence-script options

The upgraded script supports the following options.

### Compare embedding models

Each embedding model gets its own collection:

``` bash
python scripts/rag_defence.py \
  --embedding-models nomic-embed-text mxbai-embed-large
```

Install any missing model first:

``` bash
ollama pull mxbai-embed-large
```

### Change chunking settings

``` bash
python scripts/rag_defence.py \
  --chunk-size 3 \
  --chunk-overlap 1
```

Smaller chunks can make retrieval more precise, while larger chunks
preserve more surrounding context. The appropriate settings depend on
the documents and use case.

### Change the cosine threshold

``` bash
python scripts/rag_defence.py \
  --threshold 0.60
```

A higher threshold is stricter. A lower threshold is more permissive.

### Change the hybrid balance

``` bash
python scripts/rag_defence.py \
  --alpha 0.7
```

This gives more weight to the vector component than to BM25.

### Change the reranking model and threshold

``` bash
python scripts/rag_defence.py \
  --rewrite-model qwen3:0.6b \
  --rerank-model qwen3:0.6b \
  --rerank-threshold 3.0
```

The reranking threshold uses the LLM's 0--10 relevance scale.

------------------------------------------------------------------------

## 8. Browser UI demonstration

The browser UI demonstrates the user-facing upload and chat flow. The
basic search endpoint is useful for inspecting semantic retrieval, but
it should not be presented as proof of the full Advanced RAG pipeline.

Start the web service:

``` bash
source .venv/bin/activate
python -m uvicorn web.app:app --host 127.0.0.1 --port 8000
```

Open:

``` text
http://localhost:8000
```

Select Ollama mode and an installed chat model.

### 8.1 Upload and index

1.  Open **Documents** in the top toolbar.
2.  Select `/tmp/sensai-rag-test/sensai-facts.md`.
3.  Click **Upload and index**.
4.  Confirm the UI reports the filename and at least one indexed chunk.

### 8.2 Ask a grounded question

``` text
Which vector database does SensAI use?
```

Expected answer: ChromaDB.

### 8.3 Ask an unrelated question

``` text
What is the capital of Mars?
```

Expected behaviour: SensAI indicates that the indexed documents do not
provide relevant information instead of answering as though the document
contains the answer.

### 8.4 Inspect indexed data

Run:

``` bash
curl --fail http://localhost:8000/api/retrieval/sources

curl --fail \
  http://localhost:8000/api/retrieval/collections/default/chunks
```

The sources endpoint should list the uploaded document. The chunks
endpoint should expose stored chunk content and its source and metadata
information.

### 8.5 Optional semantic search API check

``` bash
curl --fail -X POST http://localhost:8000/api/retrieval/search \
  -H 'Content-Type: application/json' \
  -d '{"query":"Which vector database does SensAI use?","top_k":3}'
```

Inspect the returned source, content and similarity score.

**Important:** this endpoint demonstrates basic semantic retrieval. It
does not, by itself, demonstrate query rewriting, BM25/vector hybrid
fusion, candidate merging or LLM reranking. Use the automated defence
script and the actual chat path to demonstrate Advanced RAG.

------------------------------------------------------------------------

## 9. Failure checks and limitations

These checks help confirm that the application reports errors rather
than silently producing successful-looking results.

1.  Upload an unsupported file such as a `.png`: expect an upload error.
2.  Upload an empty text file: expect an empty-document error.
3.  Stop Ollama and attempt ingestion: expect an embedding-service
    error, not a successful upload with fake vectors.
4.  Run the defence script with a missing embedding, rewrite or
    reranking model: expect an explicit failure.
5.  Restart the default web service: if it uses a process-local
    in-memory store, its indexed data will not automatically survive the
    restart. Persistent deployments must configure a persistent
    `VectorStore` path.

The exact HTTP status codes depend on the current upload endpoint's
validation and error handling. Verify the live response rather than
assuming a status code.

------------------------------------------------------------------------

## 10. Pass criteria

### Basic RAG (R1)

The Basic RAG demonstration passes when:

-   at least one supported document chunk is ingested;
-   embeddings are generated and stored;
-   the collection and stored chunks can be inspected;
-   a grounded question retrieves the relevant document;
-   accepted vector results meet the configured cosine threshold;
-   an unrelated query does not produce accepted relevant context;
-   retrieved text is injected inside the untrusted context boundary;
-   duplicate ingestion does not create extra chunks;
-   persistence is verified by closing and reopening the test store.

### Advanced RAG (R2)

The Advanced RAG demonstration passes when:

-   BM25 produces keyword relevance scores and identifies the
    keyword-focused test document;
-   vector retrieval produces semantic results;
-   hybrid results expose both vector and BM25 component scores;
-   hybrid scores match the configured weighted formula;
-   query rewriting preserves the original question and generates
    alternatives;
-   hybrid retrieval runs for the rewritten query variants;
-   final candidate IDs are unique after merging;
-   the live LLM reranker produces valid 0--10 scores;
-   returned candidates meet the configured reranking threshold;
-   metadata filtering excludes chunks outside the requested topic;
-   accepted results can be injected into the untrusted context
    boundary.

The test output should be used as evidence. Do not claim a stage passed
if the corresponding assertion failed or if the model/service was
unavailable.

------------------------------------------------------------------------

## 11. Suggested explanation for the project defence

### Explain Basic RAG

> "SensAI uses Retrieval-Augmented Generation to answer questions from
> documents supplied by the user. First, it reads and splits documents
> into smaller overlapping chunks. Ollama generates embeddings for those
> chunks, which are stored in ChromaDB together with their source
> information and metadata. When the user asks a question, SensAI embeds
> the query and searches for semantically similar chunks. A
> cosine-similarity threshold filters weak matches. The accepted chunks
> are injected into the prompt as untrusted reference material, and the
> language model uses that context to generate its answer."

### Explain Advanced RAG

> "On top of the basic retrieval pipeline, SensAI implements Advanced
> RAG to improve the quality of retrieval. It first uses an LLM to
> rewrite the user's question into alternative queries. For each query,
> it performs both semantic vector search and BM25 keyword search. These
> scores are combined through hybrid retrieval. Results from the
> different query variants are merged by chunk ID to avoid duplicate
> passages. An LLM reranker then evaluates the relevance of each
> candidate on a 0-to-10 scale and rejects candidates below the
> configured threshold. The remaining chunks are passed to the language
> model through the same untrusted-context boundary used by Basic RAG."

### Explain why both approaches exist

> "Basic RAG provides the foundation: document ingestion, embeddings,
> vector storage, semantic retrieval and context injection. Advanced RAG
> improves the retrieval stage by combining keyword and semantic search,
> expanding the query and applying an LLM relevance judge. This gives
> the system more ways to find useful evidence and a further step for
> filtering candidate passages before generation."

------------------------------------------------------------------------

## 12. Recommended demonstration order

For a clear and convincing presentation:

  ------------------------------------------------------------------------
                         Order Demonstration         What it proves
  ---------------------------- --------------------- ---------------------
                             1 Upload the test       Document ingestion
                               document              works

                             2 Show the indexed      Chunking, embeddings
                               chunks and collection and storage

                             3 Ask a grounded        Basic semantic
                               question              retrieval and
                                                     generation

                             4 Ask an unrelated      Relevance filtering
                               question              and abstention
                                                     behaviour

                             5 Show the BM25 test    Keyword retrieval
                               output                

                             6 Show vector and       Hybrid retrieval
                               hybrid component      
                               scores                

                             7 Show generated query  Query rewriting
                               variants              

                             8 Show candidate counts Multi-query retrieval
                               and unique IDs        and merging

                             9 Show `rerank_score`   LLM reranking
                               values                

                            10 Show metadata         Filtering and safe
                               filtering and context context handling
                               injection             

                            11 Show upsert and       Storage reliability
                               persistence checks    
  ------------------------------------------------------------------------

**Final distinction to remember:** the Basic RAG path proves that SensAI
can retrieve and use document context. The Advanced RAG path proves that
SensAI can improve candidate discovery and relevance assessment before
that context reaches the language model.
