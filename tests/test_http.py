from __future__ import annotations

import unittest

from pipeline.http import _unusable_get


class HttpResponseTest(unittest.TestCase):
    def test_202_and_empty_200_are_unusable(self) -> None:
        self.assertTrue(_unusable_get(202, b""))
        self.assertTrue(_unusable_get(202, b"<html>"))
        self.assertTrue(_unusable_get(200, b""))
        self.assertFalse(_unusable_get(200, b"<html><title>Brazil</title></html>"))
        self.assertFalse(_unusable_get(404, b""))


if __name__ == "__main__":
    unittest.main()
