from __future__ import annotations

import unittest

from app.webtv import entry_id_from_webtv_url, webtv_url_at


class WebtvUrlAtTest(unittest.TestCase):
    def test_appends_kaltura_start_time_seconds(self) -> None:
        self.assertEqual(
            webtv_url_at("https://webtv.un.org/en/asset/k10/k10h1p03zp", 2845.4),
            "https://webtv.un.org/en/asset/k10/k10h1p03zp?kalturaStartTime=2845",
        )

    def test_replaces_existing_start_time(self) -> None:
        url = "https://webtv.un.org/en/asset/k10/k10h1p03zp?kalturaStartTime=1&lang=en"
        self.assertEqual(
            webtv_url_at(url, 3600),
            "https://webtv.un.org/en/asset/k10/k10h1p03zp?lang=en&kalturaStartTime=3600",
        )

    def test_rejects_empty(self) -> None:
        with self.assertRaises(ValueError):
            webtv_url_at("", 10)


class EntryIdFromWebtvUrlTest(unittest.TestCase):
    def test_maps_drupal_slug_to_kaltura_id(self) -> None:
        self.assertEqual(
            entry_id_from_webtv_url("https://webtv.un.org/en/asset/k10/k10h1p03zp"),
            "1_0h1p03zp",
        )
        self.assertEqual(
            entry_id_from_webtv_url("https://webtv.un.org/en/asset/k1g/k1gb6tjmle"),
            "1_gb6tjmle",
        )


if __name__ == "__main__":
    unittest.main()
