import shutil
import tempfile
import unittest
from pathlib import Path

from persona.registry import (
    PersonaError, create_persona, delete_persona, load_personas, update_persona,
)

DEFAULT = Path(__file__).resolve().parent.parent / 'personas'
FIELDS = {'name': 'Code Reviewer', 'role': 'reviews code', 'tone': 'direct', 'scope': 'bugs only'}


class PersonaManageTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        shutil.copytree(DEFAULT, self.dir, dirs_exist_ok=True)
        self.addCleanup(shutil.rmtree, self.dir, True)

    def test_create_derives_unique_id(self):
        first = create_persona(FIELDS, self.dir)
        second = create_persona(FIELDS, self.dir)
        self.assertEqual(first['id'], 'code-reviewer')
        self.assertEqual(second['id'], 'code-reviewer-2')
        self.assertIn('code-reviewer-2', load_personas(self.dir))

    def test_create_rejects_missing_or_long_fields(self):
        with self.assertRaises(PersonaError):
            create_persona({**FIELDS, 'tone': ' '}, self.dir)
        with self.assertRaises(PersonaError):
            create_persona({**FIELDS, 'scope': 'x' * 401}, self.dir)

    def test_update_and_delete_custom(self):
        persona = create_persona(FIELDS, self.dir)
        updated = update_persona(persona['id'], {**FIELDS, 'name': 'Strict'}, self.dir)
        self.assertEqual(updated['name'], 'Strict')
        delete_persona(persona['id'], self.dir)
        self.assertNotIn(persona['id'], load_personas(self.dir))
        with self.assertRaises(KeyError):
            delete_persona(persona['id'], self.dir)

    def test_builtins_are_protected(self):
        with self.assertRaises(PersonaError):
            delete_persona('explainer', self.dir)
        with self.assertRaises(PersonaError):
            update_persona('reviewer', FIELDS, self.dir)


if __name__ == '__main__':
    unittest.main()
