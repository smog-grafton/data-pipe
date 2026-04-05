import unittest
from unittest.mock import patch

from mobifliks_url_resolver import (
    ParsedMovie,
    ParsedSeries,
    build_candidates,
    build_series_candidates,
    parse_detail_url,
    parse_series_episodes_from_html,
    resolve_download_url,
    validate_detail_url,
)


ART_OF_SARAH_HTML = """
<div class="s-12 center">
  <h3><a href="https://www.mobifliks.com/downloadepisode2.php?eps_id=52553&series_id=1474&series_name=The%20Art%20of%20Sarah%20(2026%20-%20VJ%20Ivo%20-%20Luganda)" class="text-primary-hover">1. The Art of Sarah (2026 - VJ Ivo - Luganda) (Season 1, Episode 1 - The Fake Brand)</a></h3>
</div>
<div class="s-12 center">
  <h3><a href="https://www.mobifliks.com/downloadepisode2.php?eps_id=52554&series_id=1474&series_name=The%20Art%20of%20Sarah%20(2026%20-%20VJ%20Ivo%20-%20Luganda)" class="text-primary-hover">2. The Art of Sarah (2026 - VJ Ivo - Luganda) (Season 1, Episode 2 - The Investor)</a></h3>
</div>
"""

TEHRAN_HTML = """
<div class="s-12 center">
  <h3><a href="https://www.mobifliks.com/downloadepisode2.php?eps_id=40037&series_id=551&series_name=Tehran%20(Luganda%20Translated)" class="text-primary-hover">14. Tehran (Luganda Translated) (Season 2, Episode 6 - Broken Signal)</a></h3>
</div>
"""


class MobifliksResolverTests(unittest.TestCase):
    def test_validate_detail_url_accepts_numbered_downloadvideo_paths(self) -> None:
        valid_urls = [
            "https://www.mobifliks.com/downloadvideo.php?vid_id=1&vid_name=Movie",
            "https://www.mobifliks.com/downloadvideo2.php?vid_id=1&vid_name=Movie",
            "https://mobifliks.com/downloadvideo9.php?vid_id=1&vid_name=Movie",
            "https://mobifliks.com/downloadvideo12.php?vid_id=1&vid_name=Movie",
        ]

        for url in valid_urls:
            with self.subTest(url=url):
                validate_detail_url(url)

    def test_validate_detail_url_rejects_other_paths(self) -> None:
        with self.assertRaisesRegex(
            ValueError,
            r"URL path must end with /downloadvideo\.php or /downloadvideo<number>\.php\.",
        ):
            validate_detail_url("https://www.mobifliks.com/downloadmovie.php?vid_id=1&vid_name=Movie")

    def test_parse_detail_url_supports_downloadvideo2(self) -> None:
        movie = parse_detail_url(
            "https://www.mobifliks.com/downloadvideo2.php?vid_id=10374&vid_name="
            "Avatar:%20Fire%20and%20Ash%20(2025%20-%20VJ%20Junior%20-%20Luganda)&cat_id=4"
        )

        self.assertEqual(
            movie,
            ParsedMovie(
                title="Avatar: Fire and Ash",
                year="2025",
                vj_name="Junior",
                language="luganda",
                raw_vid_name="Avatar: Fire and Ash (2025 - VJ Junior - Luganda)",
            ),
        )

    def test_parse_detail_url_strips_language_from_vj_name(self) -> None:
        movie = parse_detail_url(
            "https://www.mobifliks.com/downloadvideo2.php?vid_id=8565&vid_name="
            "Baghead%20(2023%20-%20VJ%20Junior%20%20Luganda)&cat_id=4"
        )

        self.assertEqual(movie.vj_name, "Junior")
        self.assertEqual(movie.language, "luganda")

    def test_build_candidates_keeps_expected_direct_download_pattern(self) -> None:
        candidates = build_candidates(
            ParsedMovie(
                title="Avatar: Fire and Ash",
                year="2025",
                vj_name="Junior",
                language="luganda",
                raw_vid_name="Avatar: Fire and Ash (2025 - VJ Junior - Luganda)",
            )
        )

        self.assertIn(
            "luganda/Avatar: Fire and Ash by Vj Junior - Mobifliks.com.mp4",
            candidates,
        )
        self.assertIn(
            "luganda/Avatar: Fire and Ash by Vj Junior.mp4",
            candidates,
        )
        self.assertIn(
            "luganda/Avatar- Fire and Ash by Vj Junior - Mobifliks.com.mp4",
            candidates,
        )
        self.assertIn(
            "luganda/Avatar- Fire and Ash by Vj Junior.mp4",
            candidates,
        )

    def test_build_candidates_adds_junior_1_alias_as_fallback(self) -> None:
        candidates = build_candidates(
            ParsedMovie(
                title="Baghead",
                year="2023",
                vj_name="Junior",
                language="luganda",
                raw_vid_name="Baghead (2023 - VJ Junior Luganda)",
            )
        )

        self.assertIn(
            "luganda/Baghead by Vj Junior - Mobifliks.com.mp4",
            candidates,
        )
        self.assertIn(
            "luganda/Baghead by Vj Junior 1 - Mobifliks.com.mp4",
            candidates,
        )
        self.assertIn(
            "luganda/Baghead by Vj Junior 1- Mobifliks.com.mp4",
            candidates,
        )

    def test_parse_series_episodes_from_public_html(self) -> None:
        episodes = parse_series_episodes_from_html(ART_OF_SARAH_HTML)

        self.assertEqual(len(episodes), 2)
        self.assertEqual(episodes[0].eps_id, "52553")
        self.assertEqual(episodes[0].episode_number, 1)
        self.assertIn("The Fake Brand", episodes[0].episode_title)
        self.assertEqual(episodes[1].eps_id, "52554")
        self.assertEqual(episodes[1].episode_number, 2)

    def test_build_series_candidates_include_article_and_case_variants(self) -> None:
        art_candidates = build_series_candidates(
            ParsedSeries(
                title="The Art of Sarah",
                year="2026",
                vj_name="Ivo",
                language="luganda",
                raw_series_name="The Art of Sarah (2026 - VJ Ivo - Luganda)",
                series_id="1474",
            ),
            parse_series_episodes_from_html(ART_OF_SARAH_HTML)[0],
        )
        art_files = [candidate.file_path for candidate in art_candidates]

        self.assertIn(
            "luganda/The Art of Sarah/The Art of Sarah 1 by Vj Ivo.mp4",
            art_files,
        )
        self.assertIn(
            "luganda/Art of Sarah/Art of Sarah 1 by Vj Ivo.mp4",
            art_files,
        )

        mobland_candidates = build_series_candidates(
            ParsedSeries(
                title="MobLand",
                year="2025",
                vj_name="Ulio",
                language="luganda",
                raw_series_name="MobLand (2025 - VJ Ulio - Luganda)",
                series_id="1319",
            ),
            parse_series_episodes_from_html(
                """
                <h3><a href="https://www.mobifliks.com/downloadepisode2.php?eps_id=49712&series_id=1319&series_name=MobLand%20(2025%20-%20VJ%20Ulio%20-%20Luganda)">1. MobLand (2025 - VJ Ulio - Luganda) (Season 1, Episode 1 - New Deal)</a></h3>
                """
            )[0],
        )
        mobland_files = [candidate.file_path for candidate in mobland_candidates]

        self.assertIn(
            "luganda/MobLand/MobLand 1 by Vj Ulio - Mobifliks.com.mp4",
            mobland_files,
        )
        self.assertIn(
            "luganda/Mobland/Mobland 1 by Vj Ulio - Mobifliks.com.mp4",
            mobland_files,
        )

    def test_build_series_candidates_add_missing_vj_fallbacks(self) -> None:
        candidates = build_series_candidates(
            ParsedSeries(
                title="Tehran",
                year=None,
                vj_name=None,
                language="luganda",
                raw_series_name="Tehran (Luganda Translated)",
                series_id="551",
            ),
            parse_series_episodes_from_html(TEHRAN_HTML)[0],
        )
        files = [candidate.file_path for candidate in candidates]

        self.assertIn(
            "luganda/Tehran/Tehran 14 by Vj Ice P.mp4",
            files,
        )
        self.assertIn(
            "luganda/Tehran/Tehran 14 by Vj Junior.mp4",
            files,
        )

    def test_resolve_download_url_accepts_filename_safe_title_variant(self) -> None:
        ok_url = (
            "https://mobifliks.info/downloadmp4.php?file="
            "luganda/Avatar-%20Fire%20and%20Ash%20by%20Vj%20Junior%20-%20Mobifliks.com.mp4"
        )

        def fake_status(url: str, timeout: int, method: str) -> int:
            return 200 if url == ok_url else 500

        with patch("mobifliks_url_resolver.get_status_code", side_effect=fake_status):
            result = resolve_download_url(
                "https://www.mobifliks.com/downloadvideo2.php?vid_id=10374&vid_name="
                "Avatar:%20Fire%20and%20Ash%20(2025%20-%20VJ%20Junior%20-%20Luganda)&cat_id=4",
                retries=0,
            )

        self.assertTrue(result["ok"])
        self.assertEqual(result["content_kind"], "movie")
        self.assertEqual(result["download_url"], ok_url)

    def test_resolve_download_url_accepts_junior_1_alias_variant(self) -> None:
        ok_url = (
            "https://mobifliks.info/downloadmp4.php?file="
            "luganda/Baghead%20by%20Vj%20Junior%201-%20Mobifliks.com.mp4"
        )

        def fake_status(url: str, timeout: int, method: str) -> int:
            return 200 if url == ok_url else 500

        with patch("mobifliks_url_resolver.get_status_code", side_effect=fake_status):
            result = resolve_download_url(
                "https://www.mobifliks.com/downloadvideo2.php?vid_id=8565&vid_name="
                "Baghead%20(2023%20-%20VJ%20Junior%20%20Luganda)&cat_id=4",
                retries=0,
            )

        self.assertTrue(result["ok"])
        self.assertEqual(result["download_url"], ok_url)

    def test_resolve_download_url_accepts_unbranded_filename_variant(self) -> None:
        ok_url = (
            "https://mobifliks.info/downloadmp4.php?file="
            "luganda/Mike%20and%20Nick%20and%20Nick%20and%20Alice%20by%20Vj%20Junior.mp4"
        )

        def fake_status(url: str, timeout: int, method: str) -> int:
            return 200 if url == ok_url else 500

        with patch("mobifliks_url_resolver.get_status_code", side_effect=fake_status):
            result = resolve_download_url(
                "https://www.mobifliks.com/downloadvideo2.php?vid_id=10380&vid_name="
                "Mike%20and%20Nick%20and%20Nick%20and%20Alice%20(2026%20-%20VJ%20Junior%20-%20Luganda)&cat_id=4",
                retries=0,
            )

        self.assertTrue(result["ok"])
        self.assertEqual(result["download_url"], ok_url)

    def test_resolve_download_url_supports_series_page_and_returns_episodes(self) -> None:
        expected_urls = {
            "https://mobifliks.info/downloadserie.php?file=luganda/Art%20of%20Sarah/Art%20of%20Sarah%201%20by%20Vj%20Ivo.mp4&eps_id=52553",
            "https://mobifliks.info/downloadserie.php?file=luganda/Art%20of%20Sarah/Art%20of%20Sarah%202%20by%20Vj%20Ivo.mp4&eps_id=52554",
        }

        def fake_status(url: str, timeout: int, method: str) -> int:
            return 200 if url in expected_urls else 500

        with patch("mobifliks_url_resolver.fetch_html", return_value=ART_OF_SARAH_HTML), patch(
            "mobifliks_url_resolver.get_status_code",
            side_effect=fake_status,
        ):
            result = resolve_download_url(
                "https://www.mobifliks.com/downloadseries2.php?series_id=1474&series_name="
                "The%20Art%20of%20Sarah%20(2026%20-%20VJ%20Ivo%20-%20Luganda)",
                retries=0,
            )

        self.assertTrue(result["ok"])
        self.assertEqual(result["content_kind"], "series")
        self.assertIsNone(result["download_url"])
        self.assertEqual(len(result["episodes"]), 2)
        self.assertEqual(result["episodes"][0]["download_url"], next(url for url in expected_urls if "52553" in url))
        self.assertEqual(result["episodes"][1]["download_url"], next(url for url in expected_urls if "52554" in url))
        self.assertEqual(result["parsed"]["public_series_url"], "https://www.mobifliks.com/downloadseries1.php?series_id=1474&series_name=The%20Art%20of%20Sarah%20%282026%20-%20VJ%20Ivo%20-%20Luganda%29")

    def test_resolve_download_url_supports_episode_input_via_series_lookup(self) -> None:
        ok_url = (
            "https://mobifliks.info/downloadserie.php?file="
            "luganda/Tehran/Tehran%2014%20by%20Vj%20Ice%20P.mp4&eps_id=40037"
        )

        def fake_status(url: str, timeout: int, method: str) -> int:
            return 200 if url == ok_url else 500

        with patch("mobifliks_url_resolver.fetch_html", return_value=TEHRAN_HTML), patch(
            "mobifliks_url_resolver.get_status_code",
            side_effect=fake_status,
        ):
            result = resolve_download_url(
                "https://www.mobifliks.com/downloadepisode2.php?eps_id=40037&series_id=551&series_name="
                "Tehran%20(Luganda%20Translated)",
                retries=0,
            )

        self.assertTrue(result["ok"])
        self.assertEqual(result["content_kind"], "episode")
        self.assertEqual(result["download_url"], ok_url)
        self.assertEqual(result["episodes"][0]["episode_number"], 14)
        self.assertEqual(result["parsed"]["public_series_url"], "https://www.mobifliks.com/downloadseries1.php?series_id=551&series_name=Tehran%20%28Luganda%20Translated%29")


if __name__ == "__main__":
    unittest.main()
