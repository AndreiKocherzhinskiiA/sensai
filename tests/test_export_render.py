import os
import unittest
from unittest.mock import patch

from starlette.requests import Request

from export.render import blocks_to_html, parse_blocks, render_pdf
from web.app import public_base_url


def make_request(host):
    return Request({'type': 'http', 'scheme': 'http', 'server': (host, 80), 'path': '/', 'query_string': b'',
                    'headers': [(b'host', host.encode())]})


class RenderTests(unittest.TestCase):
    def test_markdown_is_escaped_and_links_restricted(self):
        html_out = blocks_to_html(parse_blocks('<script>x</script> **b** [ok](https://a.io) [bad](javascript:alert(1))'))
        self.assertNotIn('<script>', html_out)
        self.assertIn('<strong>b</strong>', html_out)
        self.assertIn('href="https://a.io"', html_out)
        self.assertNotIn('javascript:', html_out)

    def test_code_list_and_quote_blocks(self):
        kinds = [b[0] for b in parse_blocks('- a\n- b\n\n```py\nx = 1\n```\n\n> q')]
        self.assertEqual(kinds, ['ul', 'code', 'quote'])

    def test_pdf_paginates_long_content(self):
        pdf = render_pdf('Long', [('Assistant', '\n\n'.join(['word ' * 80] * 40))], roles=True)
        self.assertTrue(pdf.startswith(b'%PDF-1.4'))
        self.assertGreater(pdf.count(b'/Type /Page '), 1)

    def test_public_base_url(self):
        with patch.dict(os.environ, {'SENSAI_PUBLIC_URL': ''}):
            self.assertEqual(public_base_url(make_request('localhost:8000'))[1], False)
            self.assertEqual(public_base_url(make_request('abc.trycloudflare.com'))[1], True)
        with patch.dict(os.environ, {'SENSAI_PUBLIC_URL': 'https://x.example.com/'}):
            self.assertEqual(public_base_url(make_request('localhost:8000')), ('https://x.example.com', True))


if __name__ == '__main__':
    unittest.main()
