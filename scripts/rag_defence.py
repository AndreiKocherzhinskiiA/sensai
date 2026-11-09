"""Complete, observable Ollama and ChromaDB Advanced RAG defence."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import retrieval.rag as rag_module
from core.chat import inject_retrieved_context
from retrieval.bm25 import BM25
from retrieval.embeddings import embed
from retrieval.hybrid import hybrid_search
from retrieval.rag import ingest, retrieve
from retrieval.store import VectorStore


def check(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)
    print(f"[PASS] {message}")


def show_results(label: str, results: list[dict]) -> None:
    print(f"\n{label}: {len(results)} result(s)")

    for result in results:
        print(
            f"  - {result['source_file']} "
            f"lines {result['start_line']}-{result['end_line']} "
            f"score={result['score']:.4f}"
        )

        if "vector_score" in result and "bm25_score" in result:
            print(
                f"      vector={result['vector_score']:.4f} "
                f"bm25={result['bm25_score']:.4f}"
            )

        if "rerank_score" in result:
            print(f"      rerank_score={result['rerank_score']}")


def test_bm25(store: VectorStore, collection_id: str) -> None:
    print("\n=== 1. BM25 KEYWORD RETRIEVAL ===")

    chunks = store.get_all_for_collection(collection_id)
    documents = [chunk["content"] for chunk in chunks]

    query = "quasar telemetry protocol"
    scores = BM25(documents).score_all(query)

    check(bool(scores), "BM25 generated document scores")

    best_index = max(range(len(scores)), key=scores.__getitem__)
    best_chunk = chunks[best_index]

    print(f"Query: {query}")
    print(f"Best match: {best_chunk['source_file']}")
    print(f"BM25 score: {scores[best_index]:.4f}")

    check(
        best_chunk["source_file"] == "bm25-keywords.md",
        "BM25 correctly identifies the keyword-focused document",
    )


def test_hybrid(
    store: VectorStore,
    model: str,
    collection_id: str,
    alpha: float,
    base_url: str,
) -> None:
    print("\n=== 2. HYBRID VECTOR + BM25 RETRIEVAL ===")

    query = "Which vector database stores SensAI document embeddings?"

    embedding_options = (
        {"base_url": base_url}
        if base_url != "http://localhost:11434"
        else {}
    )

    query_vector = embed(
        [query],
        model=model,
        **embedding_options,
    )[0]

    results = hybrid_search(
        query,
        query_vector,
        store,
        top_k=20,
        alpha=alpha,
        collection_id=collection_id,
        threshold=None,
    )

    show_results("Hybrid results", results)

    check(bool(results), "Hybrid retrieval returned candidates")

    check(
        all(
            "vector_score" in item and "bm25_score" in item
            for item in results
        ),
        "Candidates expose both vector and BM25 scores",
    )

    check(
        all(0 <= item["score"] <= 1 for item in results),
        "Hybrid scores are normalized between 0 and 1",
    )

    # Verify the actual weighted fusion formula.
    for item in results:
        expected = (
            alpha * item["vector_score"]
            + (1 - alpha) * item["bm25_score"]
        )

        check(
            abs(item["score"] - expected) < 1e-6,
            "Hybrid score matches the weighted vector/BM25 formula",
        )
        break


def test_advanced_pipeline(
    query: str,
    store: VectorStore,
    model: str,
    collection_id: str,
    rewrite_model: str,
    rerank_model: str,
    rerank_threshold: float,
    alpha: float,
    base_url: str,
    top_k: int,
) -> list[dict]:

    print("\n=== 3. ADVANCED RAG PIPELINE ===")

    # Keep references to the real implementations.
    original_rewrite = rag_module.rewrite_query
    original_hybrid = rag_module.hybrid_search

    observed = {
        "queries": [],
        "candidates": [],
    }

    # Observe the real query rewriting function.
    def tracked_rewrite(text: str, **kwargs) -> list[str]:
        rewritten = original_rewrite(text, **kwargs)
        observed["queries"] = rewritten
        return rewritten

    # Observe the real hybrid retrieval function.
    def tracked_hybrid(text: str, *args, **kwargs) -> list[dict]:
        results = original_hybrid(text, *args, **kwargs)

        observed["candidates"].extend(results)

        print(
            f"Hybrid search for {text!r}: "
            f"{len(results)} candidate(s)"
        )

        return results

    rag_module.rewrite_query = tracked_rewrite
    rag_module.hybrid_search = tracked_hybrid

    try:
        results = rag_module.retrieve_advanced(
            query,
            store,
            top_k=top_k,
            model=model,
            rewrite_model=rewrite_model,
            rerank_model=rerank_model,
            base_url=base_url,
            alpha=alpha,
            rerank_top_n=50,
            rejection_threshold=rerank_threshold,
            use_rewriting=True,
            use_reranking=True,
            strict_reranking=True,
            collection_id=collection_id,
        )

    finally:
        # Restore the original functions even if a test fails.
        rag_module.rewrite_query = original_rewrite
        rag_module.hybrid_search = original_hybrid

    # -------------------------------------------------
    # Query rewriting
    # -------------------------------------------------

    rewritten_queries = observed["queries"]

    print("\nGenerated query variants:")

    for index, rewritten in enumerate(rewritten_queries, start=1):
        print(f"  {index}. {rewritten}")

    check(
        len(rewritten_queries) >= 2,
        "Query rewriting generated alternative queries",
    )

    check(
        query in rewritten_queries,
        "Original query is preserved alongside rewritten queries",
    )

    # -------------------------------------------------
    # Hybrid retrieval for rewritten queries
    # -------------------------------------------------

    raw_candidates = observed["candidates"]

    check(
        bool(raw_candidates),
        "Hybrid retrieval executed for rewritten queries",
    )

    raw_ids = [item["id"] for item in raw_candidates]
    unique_raw_ids = set(raw_ids)

    duplicate_count = len(raw_ids) - len(unique_raw_ids)

    print(
        f"\nCandidates across queries: {len(raw_ids)}"
    )
    print(f"Unique candidate IDs: {len(unique_raw_ids)}")
    print(f"Repeated candidate IDs: {duplicate_count}")

    # -------------------------------------------------
    # Candidate merging
    # -------------------------------------------------

    result_ids = [item["id"] for item in results]

    check(
        len(result_ids) == len(set(result_ids)),
        "Final results contain no duplicate chunk IDs",
    )

    check(
        bool(results),
        "Advanced retrieval returned relevant candidates",
    )

    # -------------------------------------------------
    # LLM reranking
    # -------------------------------------------------

    show_results("Advanced RAG final results", results)

    check(
        all(item.get("rerank_score") is not None for item in results),
        "LLM reranker produced a score for every returned candidate",
    )

    check(
        all(
            0 <= item["rerank_score"] <= 10
            for item in results
        ),
        "LLM reranking scores are within the 0-10 range",
    )

    check(
        all(
            item["rerank_score"] >= rerank_threshold
            for item in results
        ),
        "Returned candidates meet the LLM rejection threshold",
    )

    return results


def main() -> int:

    parser = argparse.ArgumentParser(description=__doc__)

    parser.add_argument(
        "--embedding-models",
        "--models",
        nargs="+",
        default=["nomic-embed-text"],
    )

    parser.add_argument("--chunk-size", type=int, default=4)
    parser.add_argument("--chunk-overlap", type=int, default=1)
    parser.add_argument("--threshold", type=float, default=0.53)

    parser.add_argument(
        "--rewrite-model",
        default="qwen3:0.6b",
    )

    parser.add_argument(
        "--rerank-model",
        default="qwen3:0.6b",
    )

    parser.add_argument(
        "--rerank-threshold",
        type=float,
        default=3.0,
    )

    parser.add_argument("--alpha", type=float, default=0.5)
    parser.add_argument("--base-url", default="http://localhost:11434")
    parser.add_argument("--top-k", type=int, default=5)

    args = parser.parse_args()

    if args.chunk_size <= 0:
        parser.error("--chunk-size must be positive")

    if not 0 <= args.chunk_overlap < args.chunk_size:
        parser.error("--chunk-overlap must be smaller than chunk-size")

    if not 0 <= args.threshold <= 1:
        parser.error("--threshold must be between 0 and 1")

    if not 0 <= args.alpha <= 1:
        parser.error("--alpha must be between 0 and 1")

    if args.top_k <= 0:
        parser.error("--top-k must be positive")

    with tempfile.TemporaryDirectory(
        prefix="sensai-rag-defence-"
    ) as workdir:

        root = Path(workdir) / "documents"
        root.mkdir()

        # -------------------------------------------------
        # Test documents
        # -------------------------------------------------

        (root / "architecture.md").write_text(
            "# SensAI architecture\n"
            "SensAI stores document embeddings in ChromaDB.\n"
            "Cosine similarity accepts relevant chunks at the collection threshold.\n"
            "Retrieved text is untrusted reference material, not instructions.\n"
            "The embedding model and chunk settings belong to each collection.\n",
            encoding="utf-8",
        )

        (root / "bm25-keywords.md").write_text(
            "# Keyword retrieval benchmark\n"
            "The quasar telemetry protocol uses a quasar telemetry identifier.\n"
            "Operators inspect the quasar telemetry protocol during diagnostics.\n"
            "The protocol records telemetry packets and quasar measurements.\n",
            encoding="utf-8",
        )

        (root / "unrelated.md").write_text(
            "# Office note\n"
            "The meeting room is available on Thursday afternoon.\n"
            "Bring the printed agenda to the meeting.\n",
            encoding="utf-8",
        )

        db_path = str(Path(workdir) / "rag.db")

        store = VectorStore(db_path)
        collection_ids: list[str] = []

        try:

            print("\n=== CONFIGURATION ===")
            print(f"Embedding models: {', '.join(args.embedding_models)}")
            print(f"Chunk size/overlap: {args.chunk_size}/{args.chunk_overlap}")
            print(f"Cosine threshold: {args.threshold}")
            print(f"Rewrite model: {args.rewrite_model}")
            print(f"Rerank model: {args.rerank_model}")
            print(f"Rerank threshold: {args.rerank_threshold}")
            print(f"Hybrid alpha: {args.alpha}")

            for index, model in enumerate(
                args.embedding_models,
                start=1,
            ):

                collection_id = f"defence-{index}"
                collection_ids.append(collection_id)

                config = store.create_collection(
                    collection_id,
                    name=f"Defence {model}",
                    embedding_model=model,
                    chunk_size=args.chunk_size,
                    chunk_overlap=args.chunk_overlap,
                    relevance_threshold=args.threshold,
                )

                print(f"\n=== COLLECTION {collection_id} ===")
                print(config)

                # -------------------------------------------------
                # Ingestion
                # -------------------------------------------------

                print("\n=== INGESTION ===")

                count = ingest(
                    str(root),
                    store,
                    model=model,
                    chunk_size=args.chunk_size,
                    chunk_overlap=args.chunk_overlap,
                    collection_id=collection_id,
                )

                check(
                    count > 0,
                    "Document ingestion created chunks",
                )

                chunks = store.get_all_for_collection(collection_id)

                # Add metadata for filtering tests.
                for chunk in chunks:
                    store.add(
                        chunk["source_file"],
                        chunk["start_line"],
                        chunk["end_line"],
                        chunk["content"],
                        chunk["vector"],
                        metadata={
                            "topic": (
                                "architecture"
                                if chunk["source_file"] == "architecture.md"
                                else "other"
                            )
                        },
                        collection_id=collection_id,
                    )

                check(
                    bool(chunks),
                    "Chunks are stored and readable from ChromaDB",
                )

                check(
                    all(chunk["vector"] for chunk in chunks),
                    "Stored chunks contain embeddings",
                )

                # -------------------------------------------------
                # BM25
                # -------------------------------------------------

                test_bm25(store, collection_id)

                # -------------------------------------------------
                # Hybrid retrieval
                # -------------------------------------------------

                test_hybrid(
                    store,
                    model,
                    collection_id,
                    args.alpha,
                    args.base_url,
                )

                # -------------------------------------------------
                # Semantic vector retrieval
                # -------------------------------------------------

                print("\n=== 4. VECTOR RETRIEVAL ===")

                query = "Which vector database does SensAI use?"

                accepted = retrieve(
                    query,
                    store,
                    model=model,
                    top_k=5,
                    collection_id=collection_id,
                    threshold=args.threshold,
                )

                show_results("Accepted vector results", accepted)

                check(
                    bool(accepted),
                    "Semantic retrieval found relevant results",
                )

                check(
                    any(
                        item["source_file"] == "architecture.md"
                        for item in accepted
                    ),
                    "Semantic retrieval found the architecture document",
                )

                check(
                    all(
                        item["score"] >= args.threshold
                        for item in accepted
                    ),
                    "Accepted results meet the cosine threshold",
                )

                # -------------------------------------------------
                # Context injection
                # -------------------------------------------------

                print("\n=== 5. CONTEXT INJECTION ===")

                pending = [
                    {"role": "user", "content": query}
                ]

                injected, context = inject_retrieved_context(
                    pending,
                    accepted,
                )

                check(
                    bool(context),
                    "Retrieved context was formatted",
                )

                check(
                    "<retrieved_context>" in injected[-1]["content"],
                    "Context is injected inside the untrusted boundary",
                )

                check(
                    "</retrieved_context>" in injected[-1]["content"],
                    "Untrusted context boundary is properly closed",
                )

                print(f"Context characters: {len(context)}")

                # -------------------------------------------------
                # Relevance threshold
                # -------------------------------------------------

                print("\n=== 6. IRRELEVANT QUERY REJECTION ===")

                unrelated_query = "What is the capital of Mars?"

                unrelated = retrieve(
                    unrelated_query,
                    store,
                    model=model,
                    top_k=5,
                    collection_id=collection_id,
                    threshold=args.threshold,
                )

                show_results(
                    "Unrelated results after threshold",
                    unrelated,
                )

                check(
                    not unrelated,
                    "Unrelated query returned no accepted results",
                )

                diagnostic = retrieve(
                    unrelated_query,
                    store,
                    model=model,
                    top_k=10,
                    collection_id=collection_id,
                    threshold=0.0,
                )

                rejected = [
                    item
                    for item in diagnostic
                    if item["score"] < args.threshold
                ]

                print(f"Diagnostic candidates: {len(diagnostic)}")
                print(f"Rejected by threshold: {len(rejected)}")

                check(
                    bool(rejected),
                    "Low-relevance candidates are rejected",
                )

                # -------------------------------------------------
                # Complete advanced pipeline
                # -------------------------------------------------

                advanced = test_advanced_pipeline(
                    query=query,
                    store=store,
                    model=model,
                    collection_id=collection_id,
                    rewrite_model=args.rewrite_model,
                    rerank_model=args.rerank_model,
                    rerank_threshold=args.rerank_threshold,
                    alpha=args.alpha,
                    base_url=args.base_url,
                    top_k=args.top_k,
                )

                # -------------------------------------------------
                # Metadata filtering
                # -------------------------------------------------

                print("\n=== 7. METADATA FILTERING ===")

                filtered = rag_module.retrieve_advanced(
                    query,
                    store,
                    model=model,
                    top_k=5,
                    rewrite_model=args.rewrite_model,
                    rerank_model=args.rerank_model,
                    base_url=args.base_url,
                    alpha=args.alpha,
                    rerank_top_n=50,
                    rejection_threshold=args.rerank_threshold,
                    filters={"topic": "architecture"},
                    collection_id=collection_id,
                    use_rewriting=True,
                    use_reranking=True,
                    strict_reranking=True,
                )

                show_results("Filtered advanced results", filtered)

                check(
                    bool(filtered),
                    "Metadata-filtered retrieval returned results",
                )

                check(
                    all(
                        item["source_file"] == "architecture.md"
                        for item in filtered
                    ),
                    "Metadata filtering excluded other documents",
                )

                # -------------------------------------------------
                # Duplicate ingestion
                # -------------------------------------------------

                print("\n=== 8. DUPLICATE INGESTION / UPSERT ===")

                duplicate_count = ingest(
                    str(root),
                    store,
                    model=model,
                    chunk_size=args.chunk_size,
                    chunk_overlap=args.chunk_overlap,
                    collection_id=collection_id,
                )

                after_duplicate = len(
                    store.get_all_for_collection(collection_id)
                )

                print(f"Re-ingested: {duplicate_count}")
                print(f"Stored after upsert: {after_duplicate}")

                check(
                    after_duplicate == count,
                    "Re-ingestion did not create duplicate chunks",
                )

        finally:
            store.close()

        # -------------------------------------------------
        # Persistence
        # -------------------------------------------------

        print("\n=== 9. PERSISTENCE ===")

        reopened = VectorStore(db_path)

        try:
            for collection_id in collection_ids:

                chunks = reopened.get_all_for_collection(collection_id)

                print(
                    f"{collection_id}: "
                    f"{len(chunks)} chunk(s) restored"
                )

                check(
                    bool(chunks),
                    f"Collection {collection_id} persisted after reopening",
                )

        finally:
            reopened.close()

    print("\n========================================")
    print("       ADVANCED RAG DEFENCE PASSED")
    print("========================================")

    print(
        "Verified: ingestion, embeddings, BM25, vector retrieval, "
        "hybrid fusion, query rewriting, multi-query retrieval, "
        "candidate deduplication, LLM reranking, rejection thresholds, "
        "metadata filtering, context injection, upsert, and persistence."
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())