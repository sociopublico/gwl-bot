from __future__ import annotations

import unittest

from pipeline.http import _looks_like_speaker_html, _unusable_get


class HttpResponseTest(unittest.TestCase):
    def test_202_without_ficha_html_is_unusable(self) -> None:
        self.assertTrue(_unusable_get(202, b""))
        self.assertTrue(_unusable_get(202, b"<html><title>Access Denied</title></html>"))
        self.assertTrue(_unusable_get(200, b""))
        self.assertFalse(_unusable_get(200, b"<html><title>Brazil</title></html>"))
        self.assertFalse(_unusable_get(404, b""))

    def test_202_with_statement_html_is_usable(self) -> None:
        html = b"<h3>Full statement</h3><a href='/sites/default/files/gastatements/80/br_pt.pdf'>"
        self.assertTrue(_looks_like_speaker_html(html))
        self.assertFalse(_unusable_get(202, html))


if __name__ == "__main__":
    unittest.main()
