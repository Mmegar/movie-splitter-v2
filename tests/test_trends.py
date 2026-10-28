import os
import sys
import unittest
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import trends  # noqa: E402


class TrendsTest(unittest.TestCase):
    def test_season(self):
        self.assertIn("Horror", trends.season(date(2026, 10, 28))[0]["genres"])
        self.assertTrue(any(s["moment"] == "Christmas holidays" for s in trends.season(date(2026, 12, 20))))
        self.assertTrue(any(s["moment"] == "Friday the 13th" for s in trends.season(date(2026, 11, 13))))

    def test_is_screen(self):
        self.assertTrue(trends.is_screen("2025 film by Zach Cregger"))
        self.assertTrue(trends.is_screen("American science fiction horror television series"))
        self.assertFalse(trends.is_screen("American actor (born 1980)"))
        self.assertFalse(trends.is_screen("2024 video game"))
        self.assertFalse(trends.is_screen(None))

    def test_wikipedia_trending_keeps_films_and_shows(self):
        def get(url):
            if "pageviews" in url:
                return {"items": [{"articles": [{"article": "Main_Page", "views": 9e6}, {"article": "Special:Search", "views": 1e6},
                                                {"article": "Weapons_(2025_film)", "views": 300}, {"article": "Taylor_Swift", "views": 500},
                                                {"article": "Stranger_Things", "views": 200}]}]}
            return {"query": {"pages": [{"title": "Weapons (2025 film)", "description": "2025 film by Zach Cregger"},
                                        {"title": "Taylor Swift", "description": "American singer-songwriter"},
                                        {"title": "Stranger Things", "description": "American television series"}]}}
        out = trends.wikipedia_trending(days=2, today=date(2026, 10, 28), get=get)
        self.assertEqual([t["title"] for t in out], ["Weapons (2025 film)", "Stranger Things"])
        self.assertEqual(out[0]["views"], 600)

    def test_context_survives_no_network(self):
        def get(url):
            raise OSError("offline")
        c = trends.context(date(2026, 10, 28), get=get)
        self.assertEqual(c["trending"], [])
        self.assertTrue(c["season"])


if __name__ == "__main__":
    unittest.main()
