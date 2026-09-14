from __future__ import annotations

import unittest

from pipeline.http import _looks_like_speaker_html, _unusable_get, is_waf_challenge


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

    def test_waf_challenge_is_unusable_even_if_url_leaks_into_html(self) -> None:
        body = (
            b"<!DOCTYPE html><html><script>window.awsWafCookieDomainList=[];"
            b"window.gokuProps={};</script>"
            b"https://gadebate.un.org/sites/default/files/gastatements/80/ao_en.pdf"
            b"</html>"
        )
        self.assertTrue(is_waf_challenge(body))
        self.assertTrue(_unusable_get(202, body))
        self.assertTrue(_looks_like_speaker_html(body))


if __name__ == "__main__":
    unittest.main()
