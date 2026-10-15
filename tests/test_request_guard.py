"""The local-only request guard: other websites must not be able to drive the
backend, while Lemur's own page and Electron keep working."""
from tests import sandbox  # noqa: F401  (must be first: isolates user folders)

import unittest

import app as lemur_app

OWN = 'http://127.0.0.1:5000'


class RequestGuard(unittest.TestCase):
    def setUp(self):
        lemur_app.app.config['TESTING'] = True
        self.client = lemur_app.app.test_client()

    def get(self, path, **headers):
        return self.client.get(path, base_url=headers.pop('base', OWN), headers=headers)

    def post(self, path, **headers):
        return self.client.post(path, base_url=headers.pop('base', OWN),
                                headers=headers, json={})

    # ── legitimate traffic ────────────────────────────────────────────────
    def test_own_page_can_read(self):
        self.assertEqual(self.get('/healthz', **{'Sec-Fetch-Site': 'same-origin'}).status_code, 200)

    def test_top_level_navigation_is_allowed(self):
        # Electron loading the page sends Sec-Fetch-Site: none.
        self.assertEqual(self.get('/healthz', **{'Sec-Fetch-Site': 'none'}).status_code, 200)

    def test_electron_main_process_without_browser_headers(self):
        self.assertEqual(self.get('/healthz').status_code, 200)

    def test_localhost_hostname_is_allowed(self):
        self.assertEqual(self.get('/healthz', base='http://localhost:5000').status_code, 200)

    def test_same_origin_post_passes_the_guard(self):
        r = self.post('/api/collections', Origin=OWN, **{'Sec-Fetch-Site': 'same-origin'})
        self.assertNotEqual(r.status_code, 403)

    # ── attacks ───────────────────────────────────────────────────────────
    def test_dns_rebinding_host_is_refused(self):
        r = self.get('/api/library', base='http://evil.example:5000')
        self.assertEqual(r.status_code, 403)
        self.assertEqual(r.get_json()['reason'], 'host')

    def test_cross_site_read_is_refused(self):
        r = self.get('/api/library', **{'Sec-Fetch-Site': 'cross-site'})
        self.assertEqual(r.status_code, 403)

    def test_same_site_is_refused(self):
        r = self.get('/api/library', **{'Sec-Fetch-Site': 'same-site'})
        self.assertEqual(r.status_code, 403)

    def test_cross_site_form_post_is_refused(self):
        # e.g. a hidden <form method=post action="http://127.0.0.1:5000/api/book/delete/x">
        r = self.client.post('/api/book/delete/x', base_url=OWN,
                             headers={'Origin': 'https://evil.example',
                                      'Sec-Fetch-Site': 'cross-site'})
        self.assertEqual(r.status_code, 403)

    def test_foreign_origin_post_without_fetch_metadata_is_refused(self):
        # Browsers that don't send Sec-Fetch-Site still send Origin on POST.
        r = self.post('/api/collections', Origin='https://evil.example')
        self.assertEqual(r.status_code, 403)
        self.assertEqual(r.get_json()['reason'], 'origin')

    def test_null_origin_post_is_refused(self):
        r = self.post('/api/collections', Origin='null')
        self.assertEqual(r.status_code, 403)


if __name__ == '__main__':
    unittest.main()
