import unittest

from forms.service import (
    FormError,
    FormValidationError,
    prompt_form_cli,
    validate_form_answers,
    validate_form_schema,
)


class FormTests(unittest.TestCase):
    def sample_schema(self):
        return {
            'form_id': 'req-clarification',
            'title': 'Project Constraints',
            'description': 'Clarify requirements before code generation.',
            'fields': [
                {'name': 'language', 'label': 'Language', 'type': 'select', 'options': ['python', 'rust', 'go']},
                {'name': 'max_retries', 'label': 'Max Retries', 'type': 'number', 'default': 3},
                {'name': 'strict_mode', 'label': 'Strict Mode', 'type': 'boolean'},
                {'name': 'notes', 'label': 'Notes', 'type': 'text', 'required': False, 'default': 'none'},
            ],
        }

    def test_valid_schema_accepted(self):
        schema = self.sample_schema()
        self.assertEqual(validate_form_schema(schema), schema)

    def test_invalid_schemas_rejected(self):
        with self.assertRaises(FormValidationError):
            validate_form_schema({'title': 'No ID', 'fields': [{'name': 'a'}]})

        with self.assertRaises(FormValidationError):
            validate_form_schema({'form_id': 'f1', 'title': 'Empty fields', 'fields': []})

        with self.assertRaises(FormValidationError):
            validate_form_schema({
                'form_id': 'f1',
                'title': 'Duplicate fields',
                'fields': [{'name': 'x'}, {'name': 'x'}],
            })

        with self.assertRaises(FormValidationError):
            validate_form_schema({
                'form_id': 'f1',
                'title': 'Bad select',
                'fields': [{'name': 'choice', 'type': 'select', 'options': []}],
            })

    def test_validate_answers_coerces_types_and_applies_defaults(self):
        schema = self.sample_schema()
        raw_answers = {
            'language': 'python',
            'max_retries': '5',
            'strict_mode': 'yes',
        }
        validated = validate_form_answers(schema, raw_answers)
        self.assertEqual(validated['language'], 'python')
        self.assertEqual(validated['max_retries'], 5)
        self.assertIs(validated['strict_mode'], True)
        self.assertEqual(validated['notes'], 'none')

    def test_validate_answers_rejects_invalid_values(self):
        schema = self.sample_schema()

        with self.assertRaises(FormValidationError):
            validate_form_answers(schema, {
                'language': 'java',
                'max_retries': 3,
                'strict_mode': True,
            })

        with self.assertRaises(FormValidationError):
            validate_form_answers(schema, {
                'language': 'python',
                'max_retries': 'not-a-number',
                'strict_mode': False,
            })

        with self.assertRaises(FormValidationError):
            validate_form_answers(schema, {
                'max_retries': 3,
                'strict_mode': True,
            })

    def test_prompt_form_cli_collects_and_retries_invalid_input(self):
        schema = {
            'form_id': 'cli-test',
            'title': 'CLI Form',
            'fields': [
                {'name': 'env', 'label': 'Environment', 'type': 'select', 'options': ['dev', 'prod']},
                {'name': 'workers', 'label': 'Workers', 'type': 'number', 'default': 2},
            ],
        }
        inputs = iter(['staging', 'dev', ''])
        outputs = []
        answers = prompt_form_cli(
            schema,
            input_func=lambda prompt: next(inputs),
            output_func=outputs.append,
        )
        self.assertEqual(answers, {'env': 'dev', 'workers': 2})
        self.assertTrue(any('Error:' in line for line in outputs))


if __name__ == '__main__':
    unittest.main()

