"""Complete, observable Ollama and ChromaDB RAG defence."""
from __future__ import annotations

import argparse
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.chat import inject_retrieved_context
from retrieval.rag import ingest, retrieve, retrieve_advanced
from retrieval.store import VectorStore


def show_results(label: str, results: list[dict]) -> None:
    print(f"{label}: {len(results)} result(s)")
    for result in results:
        print(
            f"  - {result['source_file']} lines "
            f"{result['start_line']}-{result['end_line']} "
            f"score={result['score']:.4f}"
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--embedding-models",
        "--models",
        nargs="+",
        default=["nomic-embed-text"],
        help="one or more installed Ollama embedding models to compare",
    )
    parser.add_argument("--chunk-size", type=int, default=4)
    parser.add_argument("--chunk-overlap", type=int, default=1)
    parser.add_argument("--threshold", type=float, default=0.53)
    parser.add_argument("--rerank-model", default="qwen3:0.6b")
    parser.add_argument("--rerank-threshold", type=float, default=3.0)
    args = parser.parse_args()
    if args.chunk_size <= 0 or not 0 <= args.chunk_overlap < args.chunk_size:
        parser.error("--chunk-overlap must be non-negative and smaller than --chunk-size")
    if not 0.0 <= args.threshold <= 1.0:
        parser.error("--threshold must be between 0 and 1")

    with tempfile.TemporaryDirectory(prefix="sensai-rag-defence-") as workdir:
        root = Path(workdir) / "documents"
        root.mkdir()
        (root / "architecture.md").write_text(
            "# Sensai architecture\n"
            "Sensai stores document embeddings in ChromaDB.\n"
            "Cosine similarity accepts relevant chunks at the collection threshold.\n"
            "Retrieved text is untrusted reference material, not instructions.\n"
            "The embedding model and chunk settings belong to each collection.\n",
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
            print("=== Configuration ===")
            print(f"Embedding models: {', '.join(args.embedding_models)}")
            print(f"Chunk size/overlap: {args.chunk_size}/{args.chunk_overlap}")
            print(f"Cosine acceptance threshold: {args.threshold}")
            print(f"Rerank model/threshold: {args.rerank_model}/{args.rerank_threshold}")

            for index, model in enumerate(args.embedding_models, start=1):
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
                print(f"\n=== Collection {collection_id} ===")
                print(
                    f"model={config['embedding_model']} "
                    f"chunk_size={config['chunk_size']} "
                    f"overlap={config['chunk_overlap']} "
                    f"threshold={config['relevance_threshold']}"
                )

                print("\n--- Ingestion pipeline ---")
                print(f"1. Read supported files from {root}")
                print("2. Decode UTF-8 and split into line chunks")
                count = ingest(
                    str(root),
                    store,
                    model=model,
                    chunk_size=args.chunk_size,
                    chunk_overlap=args.chunk_overlap,
                    collection_id=collection_id,
                )
                print(f"3. Embed chunks with Ollama model {model}: {count} chunk(s)")
                print("4. Upsert IDs, text, embeddings, and metadata into ChromaDB")
                if count == 0:
                    raise RuntimeError(f"No chunks were ingested for model {model}.")

                chunks = store.get_all_for_collection(collection_id)
                for chunk in chunks:
                    store.add(
                        chunk["source_file"], chunk["start_line"], chunk["end_line"],
                        chunk["content"], chunk["vector"],
                        metadata={
                            "topic": "architecture"
                            if chunk["source_file"] == "architecture.md"
                            else "office",
                        },
                        collection_id=collection_id,
                    )
                print(f"5. ChromaDB inspection: {len(chunks)} stored chunk(s)")
                for chunk in chunks:
                    print(
                        f"  - id={chunk['id'][:12]}... "
                        f"source={chunk['source_file']} "
                        f"lines={chunk['start_line']}-{chunk['end_line']} "
                        f"vector_dimension={len(chunk['vector'])}"
                    )

                print("\n--- Retrieval pipeline ---")
                query = "Which vector database does Sensai use?"
                print(f"1. Query: {query}")
                accepted = retrieve(
                    query, store, model=model, top_k=5, collection_id=collection_id
                )
                show_results("2. Accepted results (score >= threshold)", accepted)
                if not accepted or accepted[0]["source_file"] != "architecture.md":
                    raise RuntimeError(f"Relevant retrieval failed for model {model}.")
                print("3. Context injection: accepted chunks are marked as untrusted reference data")
                pending = [{"role": "user", "content": query}]
                injected, context = inject_retrieved_context(pending, accepted)
                print(f"4. Injected context characters: {len(context)}")
                if "<retrieved_context>" not in injected[-1]["content"]:
                    raise RuntimeError("Retrieved context was not injected.")

                unrelated_query = "What is the capital of Mars?"
                unrelated = retrieve(
                    unrelated_query,
                    store,
                    model=model,
                    top_k=5,
                    collection_id=collection_id,
                )
                show_results(
                    "5. Unrelated results after threshold (must be zero)", unrelated
                )
                if unrelated:
                    raise RuntimeError("Unrelated content passed the acceptance threshold.")

                diagnostic = retrieve(
                    unrelated_query,
                    store,
                    model=model,
                    top_k=5,
                    collection_id=collection_id,
                    threshold=0.0,
                )
                show_results("6. Diagnostic candidates before threshold", diagnostic)
                rejected = [item for item in diagnostic if item["score"] < args.threshold]
                print(f"7. Rejected by threshold: {len(rejected)}")
                if diagnostic and not rejected:
                    raise RuntimeError("Threshold did not reject any low-score candidate.")

                reranked = retrieve_advanced(
                    query,
                    store,
                    model=model,
                    top_k=5,
                    rerank_model=args.rerank_model,
                    rejection_threshold=args.rerank_threshold,
                    collection_id=collection_id,
                    use_rewriting=False,
                    use_reranking=True,
                    strict_reranking=True,
                )
                show_results("8. Reranked results", reranked)
                for result in reranked:
                    print(f"  rerank_score={result['rerank_score']}")
                if not reranked or any(
                    result.get("rerank_score") is None for result in reranked
                ):
                    raise RuntimeError("Reranking did not produce scores for every result.")

                filtered = retrieve_advanced(
                    query,
                    store,
                    model=model,
                    top_k=5,
                    rerank_model=args.rerank_model,
                    rejection_threshold=args.rerank_threshold,
                    filters={"topic": "architecture"},
                    collection_id=collection_id,
                    use_rewriting=False,
                    use_reranking=True,
                    strict_reranking=True,
                )
                show_results("9. Metadata-filtered results (topic=architecture)", filtered)
                if any(result["source_file"] != "architecture.md" for result in filtered):
                    raise RuntimeError("Metadata filter allowed an office chunk.")

                duplicate_count = ingest(
                    str(root),
                    store,
                    model=model,
                    chunk_size=args.chunk_size,
                    chunk_overlap=args.chunk_overlap,
                    collection_id=collection_id,
                )
                after_duplicate = len(store.get_all_for_collection(collection_id))
                print(
                    f"\n--- Upsert check ---\n"
                    f"Re-ingested={duplicate_count}; stored after upsert={after_duplicate}"
                )
                if after_duplicate != count:
                    raise RuntimeError("Duplicate ingestion created extra chunks.")
        finally:
            store.close()

        print("\n=== Persistence check ===")
        reopened = VectorStore(db_path)
        try:
            for collection_id in collection_ids:
                chunks = reopened.get_all_for_collection(collection_id)
                print(f"{collection_id}: {len(chunks)} chunk(s) restored from ChromaDB")
                if not chunks:
                    raise RuntimeError(f"Collection {collection_id} was not persisted.")
        finally:
            reopened.close()

    print("\nRAG defence passed: configuration, ingestion, retrieval, thresholding,")
    print("context injection, ChromaDB inspection, upsert, and persistence verified.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
