"""Unit tests for retrieval/store.py"""
import unittest
import tempfile
from pathlib import Path

from retrieval.store import VectorStore, cosine_similarity


class CosineSimilarityTests(unittest.TestCase):
    def test_identical_vectors_score_one(self):
        self.assertAlmostEqual(cosine_similarity([1, 2, 3], [1, 2, 3]), 1.0)

    def test_orthogonal_vectors_score_zero(self):
        self.assertAlmostEqual(cosine_similarity([1, 0], [0, 1]), 0.0)

    def test_opposite_vectors_score_negative_one(self):
        self.assertAlmostEqual(cosine_similarity([1, 0], [-1, 0]), -1.0)

    def test_zero_vector_returns_zero(self):
        self.assertEqual(cosine_similarity([0, 0], [1, 2]), 0.0)

    def test_mismatched_lengths_raise(self):
        with self.assertRaises(ValueError):
            cosine_similarity([1, 2], [1, 2, 3])


class VectorStoreTests(unittest.TestCase):
    def setUp(self):
        self.store = VectorStore(":memory:")

    def tearDown(self):
        self.store.close()

    def test_add_and_search_returns_closest_first(self):
        self.store.add("a.md", 1, 5, "about cats", [1, 0, 0])
        self.store.add("b.md", 1, 5, "about dogs", [0, 1, 0])
        self.store.add("c.md", 1, 5, "about cats too", [0.9, 0.1, 0])

        results = self.store.search([1, 0, 0], top_k=2)

        self.assertEqual(len(results), 2)
        self.assertEqual(results[0]["source_file"], "a.md")
        self.assertEqual(results[1]["source_file"], "c.md")

    def test_search_respects_top_k(self):
        for i in range(5):
            self.store.add(f"f{i}.md", 1, 1, f"chunk {i}", [1, 0, 0])
        results = self.store.search([1, 0, 0], top_k=3)
        self.assertEqual(len(results), 3)

    def test_search_on_empty_store_returns_empty(self):
        self.assertEqual(self.store.search([1, 0, 0]), [])

    def test_round_trip_preserves_embedding_values(self):
        original = [0.1, 0.2, 0.3, -0.5]
        self.store.add("x.md", 1, 1, "content", original)
        results = self.store.search(original, top_k=1)
        self.assertAlmostEqual(results[0]["score"], 1.0, places=5)

    def test_add_without_metadata_defaults_to_empty_dict(self):
        self.store.add("a.md", 1, 1, "content", [1, 0, 0])
        results = self.store.search([1, 0, 0], top_k=1)
        self.assertEqual(results[0]["metadata"], {})

    def test_add_with_metadata_is_stored_and_returned(self):
        self.store.add("a.md", 1, 1, "content", [1, 0, 0], metadata={"version": "v2"})
        results = self.store.search([1, 0, 0], top_k=1)
        self.assertEqual(results[0]["metadata"], {"version": "v2"})

    def test_filter_excludes_non_matching_metadata(self):
        self.store.add("a.md", 1, 1, "v1 content", [1, 0, 0], metadata={"version": "v1"})
        self.store.add("b.md", 1, 1, "v2 content", [1, 0, 0], metadata={"version": "v2"})

        results = self.store.search([1, 0, 0], top_k=5, filters={"version": "v2"})

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["source_file"], "b.md")

    def test_filter_with_no_matches_returns_empty(self):
        self.store.add("a.md", 1, 1, "content", [1, 0, 0], metadata={"version": "v1"})
        results = self.store.search([1, 0, 0], top_k=5, filters={"version": "v99"})
        self.assertEqual(results, [])

    def test_no_filter_returns_all_regardless_of_metadata(self):
        self.store.add("a.md", 1, 1, "content", [1, 0, 0], metadata={"version": "v1"})
        self.store.add("b.md", 1, 1, "content", [1, 0, 0], metadata={"version": "v2"})
        results = self.store.search([1, 0, 0], top_k=5)
        self.assertEqual(len(results), 2)

    def test_collection_configuration_and_isolation(self):
        self.store.create_collection(
            "course",
            name="Course notes",
            embedding_model="test-embedder",
            chunk_size=10,
            chunk_overlap=2,
            relevance_threshold=0.8,
        )
        self.store.add("course.md", 1, 1, "course content", [1, 0, 0],
                       collection_id="course")

        self.assertEqual(self.store.get_collection("course")["embedding_model"], "test-embedder")
        self.assertEqual(self.store.get_all_for_collection("course")[0]["collection_id"], "course")
        self.assertEqual(self.store.get_all(), [])

    def test_collection_rejects_incompatible_embedding_dimension(self):
        self.store.add("a.md", 1, 1, "content", [1, 0, 0])
        with self.assertRaises(ValueError):
            self.store.add("b.md", 1, 1, "content", [1, 0])

    def test_duplicate_chunk_is_upserted_without_duplication(self):
        first_id = self.store.add("a.md", 1, 1, "content", [1, 0, 0])
        second_id = self.store.add("a.md", 1, 1, "content", [1, 0, 0])

        self.assertEqual(first_id, second_id)
        self.assertEqual(len(self.store.get_all()), 1)

    def test_threshold_rejects_low_similarity_and_accepts_boundary(self):
        self.store.add("a.md", 1, 1, "relevant", [1, 0, 0])
        self.store.add("b.md", 1, 1, "unrelated", [0, 1, 0])

        self.assertEqual(
            [item["source_file"] for item in self.store.search(
                [1, 0, 0], threshold=1.0
            )],
            ["a.md"],
        )
        with self.assertRaises(ValueError):
            self.store.search([1, 0, 0], threshold=1.1)

    def test_persistent_store_preserves_collection_and_chunks(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "vectors.db")
            first = VectorStore(path)
            first.create_collection("persisted")
            first.add("notes.md", 1, 1, "persistent content", [1, 0, 0],
                      collection_id="persisted")
            first.close()

            second = VectorStore(path)
            self.addCleanup(second.close)
            self.assertEqual(second.get_collection("persisted")["name"], "persisted")
            self.assertEqual(len(second.get_all_for_collection("persisted")), 1)


if __name__ == '__main__':
    unittest.main()