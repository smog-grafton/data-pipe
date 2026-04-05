import unittest
from unittest.mock import patch

from app import EpisodeResult, ResolveRequest, resolve


class AppResolveTests(unittest.TestCase):
    def test_resolve_normalizes_series_episode_results(self) -> None:
        fake_result = {
            "ok": True,
            "download_url": None,
            "status_code": None,
            "content_kind": "series",
            "parsed": {"raw_series_name": "Heroes Next Door (2025 - VJ Ivo - Luganda)"},
            "checks": [],
            "episodes": [
                {
                    "episode_number": 5,
                    "episode_title": "Heroes Next Door (2025 - VJ Ivo - Luganda) (Season 1, Episode 5 - House Rules)",
                    "eps_id": "52529",
                    "watch_url": "https://www.mobifliks.com/downloadepisode2.php?eps_id=52529&series_id=1473&series_name=Heroes%20Next%20Door%20%282025%20-%20VJ%20Ivo%20-%20Luganda%29",
                    "download_url": "https://mobifliks.info/downloadserie.php?file=luganda/Heroes%20Next%20Door/Heroes%20Next%20Door%205%20by%20Vj%20Ivo.mp4&eps_id=52529",
                    "ok": True,
                    "status_code": 200,
                    "checks": [
                        {
                            "file": "luganda/Heroes Next Door/Heroes Next Door 5 by Vj Ivo.mp4",
                            "url": "https://mobifliks.info/downloadserie.php?file=luganda/Heroes%20Next%20Door/Heroes%20Next%20Door%205%20by%20Vj%20Ivo.mp4&eps_id=52529",
                            "status": 200,
                            "attempts": 1,
                            "methods": ["HEAD"],
                            "accepted": True,
                        }
                    ],
                }
            ],
        }

        with patch("app.mobifliks_resolve", return_value=fake_result):
            result = resolve(
                ResolveRequest(
                    url="https://www.mobifliks.com/downloadseries2.php?series_id=1473&series_name=Heroes%20Next%20Door%20(2025%20-%20VJ%20Ivo%20-%20Luganda)"
                )
            )

        self.assertEqual(result["content_kind"], "series")
        self.assertEqual(result["resolver"], "mobifliks")
        self.assertEqual(len(result["episodes"]), 1)
        self.assertIsInstance(result["episodes"][0], EpisodeResult)
        self.assertEqual(result["episodes"][0].eps_id, "52529")
        self.assertEqual(result["episodes"][0].status_code, 200)


if __name__ == "__main__":
    unittest.main()
