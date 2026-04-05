import unittest
from unittest.mock import patch

from mobifliks_url_resolver import ParsedMovie, build_candidates, parse_detail_url, resolve_download_url, validate_detail_url


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
            "luganda/Avatar- Fire and Ash by Vj Junior - Mobifliks.com.mp4",
            candidates,
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
                "Avatar:%20Fire%20and%20Ash%20(2025%20-%20VJ%20Junior%20-%20Luganda)&cat_id=4"
            )

        self.assertTrue(result["ok"])
        self.assertEqual(result["download_url"], ok_url)


if __name__ == "__main__":
    unittest.main()
