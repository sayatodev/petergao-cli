from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

from petergao.client import PastPaperClient, unwrap_outlook_safelink
from petergao.parsing import build_paper, course_exists, parse_course_codes, parse_papers, parse_quota
from petergao.paths import default_output_dir
from petergao.pdf import looks_like_solution_text
from petergao.storage import StoredSession


INDEX_HTML = """
Account: test&nbsp;&nbsp;&nbsp;&nbsp;Downloads this Semester/Quota Per Semester: 44/50&nbsp;&nbsp;&nbsp;&nbsp; <a href="login.php?action=logout">logout</a><br><hr>
<a href="index.php?course=COMP1021"><font face="Courier">COMP1021</font></a>
<a href="index.php?course=COMP2011"><font face="Courier">COMP2011</font></a>
"""

COURSE_HTML = """
Back to <a href="index.php">index</a><br><br>
<a href="down.php?course=COMP1021&id=0">(COMP1021)[2014](f)midterm1~yhuag^_13677.pdf</a> (<a href="down.php?course=COMP1021&id=0&down=true">Direct Download</a>)
<br><a href="down.php?course=COMP1021&id=1">(COMP1021)[2014](f)midterm_review~klchiuab^_34730.pdf</a> (<a href="down.php?course=COMP1021&id=1&down=true">Direct Download</a>)
<br><a href="down.php?course=COMP1021&id=2">(COMP1021)[2014](s)final~=3b1u6^_45658.pdf</a> (<a href="down.php?course=COMP1021&id=2&down=true">Direct Download</a>)
"""


class ParsingTests(unittest.TestCase):
    def test_parse_quota(self) -> None:
        quota = parse_quota(INDEX_HTML)
        self.assertIsNotNone(quota)
        assert quota is not None
        self.assertEqual(quota.used, 44)
        self.assertEqual(quota.total, 50)
        self.assertEqual(quota.remaining, 6)

    def test_parse_course_codes(self) -> None:
        self.assertEqual(parse_course_codes(INDEX_HTML), ["COMP1021", "COMP2011"])

    def test_parse_papers(self) -> None:
        papers = parse_papers(COURSE_HTML, "COMP1021")
        self.assertEqual(len(papers), 3)
        self.assertEqual(papers[0].paper_type, "midterm1")
        self.assertEqual(papers[1].paper_type, "midterm_review")
        self.assertEqual(papers[2].paper_type, "final")
        self.assertEqual(papers[2].semester, "Spring")

    def test_suggested_filename(self) -> None:
        paper = build_paper("COMP2011", 1, "(COMP2011)[2013](f)final~=b0bi1ldd^_44837.pdf", "down.php?course=COMP2011&id=1")
        self.assertEqual(paper.suggested_filename(), "COMP2011_13F_final.pdf")

    def test_normalizes_broken_year_token(self) -> None:
        paper = build_paper("COMP2011", 0, "(COMP2011)[20016](f)midterm~xfengag^_77321.pdf", "down.php?course=COMP2011&id=0")
        self.assertEqual(paper.display_year, "2016")
        self.assertEqual(paper.suggested_filename(), "COMP2011_16F_midterm.pdf")

    def test_invalid_course_page(self) -> None:
        self.assertFalse(course_exists("wrong course number"))

    def test_quota_estimator_respects_per_course_cap(self) -> None:
        stored = StoredSession(
            email="user",
            domain="@connect.ust.hk",
            cookies={"PHPSESSID": "abc"},
            php_session_id="abc",
            course_download_counts={"COMP2011": 10},
            last_known_quota_used=20,
        )
        client = PastPaperClient(stored=stored)
        self.assertEqual(client.estimate_quota_delta("COMP2011", 14), 2)
        self.assertEqual(client.estimate_quota_delta("COMP2011", 3), 2)
        self.assertEqual(client.estimate_quota_delta("COMP1021", 5), 5)

    def test_solution_text_heuristic(self) -> None:
        self.assertTrue(looks_like_solution_text("COMP2011 Final Exam Sample Solution\nQuestion 1"))
        self.assertFalse(looks_like_solution_text("Please show all your solution steps and answer all questions."))

    def test_unwrap_outlook_safelink(self) -> None:
        wrapped = (
            "https://apc01.safelinks.protection.outlook.com/?url="
            "http%3A%2F%2Fpetergao.cc%2Fustpastpaper%2Flogin.php%3Fticket%3D12345"
            "&data=05%7C02%7Cfoobar%40connect.ust.hk%7Cabc&sdata=xyz&reserved=0"
        )
        self.assertEqual(
            unwrap_outlook_safelink(wrapped),
            "http://petergao.cc/ustpastpaper/login.php?ticket=12345",
        )

    def test_normalize_verification_url_supports_outlook_safelink(self) -> None:
        client = PastPaperClient()
        wrapped = (
            "https://apc01.safelinks.protection.outlook.com/?url="
            "http%3A%2F%2Fpetergao.cc%2Fustpastpaper%2Flogin.php%3Fticket%3D12345"
            "&data=05%7C02%7Cfoobar%40connect.ust.hk%7Cabc&sdata=xyz&reserved=0"
        )
        self.assertEqual(
            client.normalize_verification_url(wrapped),
            "http://petergao.cc/ustpastpaper/login.php?ticket=12345",
        )

    def test_default_output_dir_prefers_downloads(self) -> None:
        fake_home = r"C:\Users\tester"
        fake_downloads = Path(fake_home) / "Downloads"
        with patch("petergao.paths.Path.home", return_value=Path(fake_home)):
            with patch.object(Path, "exists", return_value=True):
                with patch.object(Path, "is_dir", return_value=True):
                    self.assertEqual(default_output_dir(), fake_downloads)


if __name__ == "__main__":
    unittest.main()
