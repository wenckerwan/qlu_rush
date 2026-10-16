"""Offline regression tests: no requests to the course-selection service."""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app


class OutboundSecurityTest(unittest.TestCase):
    def test_untrusted_origins_rejected_before_network(self):
        for url in ('http://127.0.0.1:8000', 'http://172.17.0.1',
                    'https://jw.qlu.edu.cn#', 'https://jw.qlu.edu.cn?x=1',
                    'https://jw.qlu.edu.cn@127.0.0.1',
                    'https://jw.qlu.edu.cn.evil.test', 'https://jw.qlu.edu.cn/path',
                    'http://jw.qlu.edu.cn', 'https://jw.qlu.edu.cn:444'):
            with self.subTest(url=url), self.assertRaises(app.HTTPException):
                app._normalize_config({'base_url': url})

    def test_default_origin_accepted(self):
        self.assertEqual(app._normalize_config({})['base_url'], 'https://jw.qlu.edu.cn')

    def test_session_disables_environment_proxies_and_redirects(self):
        session = app._build_session(app._normalize_config({}))
        self.assertFalse(session.trust_env)
        response = app.requests.Response()
        response.status_code = 302
        response.headers['Location'] = 'http://127.0.0.1/private'
        with patch.object(app.requests.Session, 'send', return_value=response) as send:
            session.post('https://jw.qlu.edu.cn/test')
        self.assertFalse(send.call_args.kwargs['allow_redirects'])

    def test_registration_closed_by_default(self):
        with patch.dict(app.os.environ, {}, clear=True), patch.object(app, 'load_users', return_value={}), patch.object(app, 'save_users'):
            with self.assertRaises(app.HTTPException) as caught:
                app.api_register({'username': 'security_test', 'password': 'unused123'})
        self.assertEqual(caught.exception.status_code, 403)


if __name__ == '__main__':
    unittest.main()
