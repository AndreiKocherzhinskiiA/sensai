import unittest

from artifacts.service import (
    ArtifactError,
    ArtifactPatch,
    ArtifactStore,
    VersionConflictError,
)


class ArtifactTests(unittest.TestCase):
    def setUp(self):
        self.store = ArtifactStore(':memory:')

    def tearDown(self):
        self.store.close()

    def test_create_and_get_artifact(self):
        art = self.store.create_artifact(
            artifact_id='spec-1',
            title='Project Specification',
            initial_sections={'intro': 'Initial intro', 'goals': 'Initial goals'},
        )
        self.assertEqual(art['artifact_id'], 'spec-1')
        self.assertEqual(art['title'], 'Project Specification')
        self.assertEqual(art['version'], 1)
        self.assertEqual(art['sections'], {'intro': 'Initial intro', 'goals': 'Initial goals'})

    def test_update_section_preserves_other_sections(self):
        self.store.create_artifact(
            artifact_id='spec-1',
            title='Spec',
            initial_sections={'sec1': 'Keep me', 'sec2': 'Old content'},
        )
        patch = ArtifactPatch(
            artifact_id='spec-1',
            expected_version=1,
            section_id='sec2',
            operation='update',
            value='New updated content',
        )
        updated = self.store.apply_patch(patch)
        self.assertEqual(updated['version'], 2)
        self.assertEqual(updated['sections']['sec1'], 'Keep me')
        self.assertEqual(updated['sections']['sec2'], 'New updated content')

    def test_insert_and_delete_sections(self):
        self.store.create_artifact('doc-1', 'Doc', {'a': 'Section A'})

        # Insert section 'b'
        res1 = self.store.apply_patch(ArtifactPatch(
            artifact_id='doc-1',
            expected_version=1,
            section_id='b',
            operation='insert',
            value='Section B',
        ))
        self.assertEqual(res1['version'], 2)
        self.assertEqual(res1['sections'], {'a': 'Section A', 'b': 'Section B'})

        # Delete section 'a'
        res2 = self.store.apply_patch(ArtifactPatch(
            artifact_id='doc-1',
            expected_version=2,
            section_id='a',
            operation='delete',
        ))
        self.assertEqual(res2['version'], 3)
        self.assertEqual(res2['sections'], {'b': 'Section B'})

    def test_version_conflict_rejected_atomically(self):
        self.store.create_artifact('doc-1', 'Doc', {'a': 'Original'})

        with self.assertRaises(VersionConflictError):
            self.store.apply_patch(ArtifactPatch(
                artifact_id='doc-1',
                expected_version=99,
                section_id='a',
                operation='update',
                value='Should fail',
            ))

        # Verify document remains untouched at version 1
        art = self.store.get_artifact('doc-1')
        self.assertEqual(art['version'], 1)
        self.assertEqual(art['sections']['a'], 'Original')

    def test_history_and_markdown_rendering(self):
        self.store.create_artifact('doc-1', 'Architecture', {'overview': 'V1 overview'})
        self.store.apply_patch(ArtifactPatch(
            artifact_id='doc-1',
            expected_version=1,
            section_id='overview',
            operation='update',
            value='V2 overview',
        ))

        history = self.store.get_history('doc-1')
        self.assertEqual(len(history), 2)
        self.assertEqual(history[0]['version'], 1)
        self.assertIsNone(history[0]['patch'])
        self.assertEqual(history[1]['version'], 2)
        self.assertEqual(history[1]['patch']['operation'], 'update')

        md = self.store.render_markdown('doc-1')
        self.assertIn('# Architecture', md)
        self.assertIn('## overview', md)
        self.assertIn('V2 overview', md)

    def test_invalid_inputs_raise_artifact_error(self):
        with self.assertRaises(ArtifactError):
            self.store.create_artifact('', 'Title')
        with self.assertRaises(ArtifactError):
            self.store.create_artifact('id-1', '   ')

        self.store.create_artifact('id-1', 'Valid Title', {'s1': 'text'})
        with self.assertRaises(ArtifactError):
            self.store.create_artifact('id-1', 'Duplicate ID')

        with self.assertRaises(ArtifactError):
            self.store.apply_patch(ArtifactPatch(
                artifact_id='id-1',
                expected_version=1,
                section_id='s1',
                operation='invalid_op',
            ))


if __name__ == '__main__':
    unittest.main()

