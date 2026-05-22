from __future__ import annotations

from html.parser import HTMLParser
import re
from urllib.parse import urljoin

from petergao.models import Paper, Quota


BASE_URL = "https://petergao.cc/ustpastpaper/"
QUOTA_RE = re.compile(r"Downloads this Semester/Quota Per Semester:\s*(\d+)/(\d+)", re.IGNORECASE)
COURSE_LINK_RE = re.compile(r"index\.php\?course=([A-Z0-9]+)$")
PAPER_LINK_RE = re.compile(r"down\.php\?course=([^&]+)&id=(\d+)$")
PAPER_NAME_RE = re.compile(
    r"^\((?P<course>[^)]+)\)\[(?P<year>[^\]]+)\]\((?P<semester>[^)]+)\)(?P<body>.+?)(?P<ext>\.[A-Za-z0-9]+)$"
)


class AnchorCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[tuple[str, str]] = []
        self._href: str | None = None
        self._chunks: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "a":
            return
        attr_map = dict(attrs)
        href = attr_map.get("href")
        if href:
            self._href = href
            self._chunks = []

    def handle_data(self, data: str) -> None:
        if self._href is not None:
            self._chunks.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() != "a" or self._href is None:
            return
        text = "".join(self._chunks).strip()
        self.links.append((self._href, text))
        self._href = None
        self._chunks = []


def parse_quota(html: str) -> Quota | None:
    match = QUOTA_RE.search(html)
    if not match:
        return None
    used, total = match.groups()
    return Quota(used=int(used), total=int(total))


def is_authenticated(html: str) -> bool:
    return parse_quota(html) is not None and "login.php?action=logout" in html


def parse_course_codes(html: str) -> list[str]:
    parser = AnchorCollector()
    parser.feed(html)
    codes: list[str] = []
    seen: set[str] = set()
    for href, text in parser.links:
        match = COURSE_LINK_RE.match(href)
        if not match:
            continue
        code = match.group(1).upper()
        if code in seen:
            continue
        if not text or text.upper() != code:
            continue
        seen.add(code)
        codes.append(code)
    return codes


def parse_papers(html: str, fallback_course: str) -> list[Paper]:
    parser = AnchorCollector()
    parser.feed(html)
    papers: list[Paper] = []
    for href, text in parser.links:
        match = PAPER_LINK_RE.match(href)
        if not match:
            continue
        if not text or text == "Direct Download":
            continue
        course, site_id_text = match.groups()
        if course.upper() != fallback_course.upper():
            continue
        site_id = int(site_id_text)
        papers.append(build_paper(course.upper(), site_id, text, href))
    return papers


def build_paper(course: str, site_id: int, raw_filename: str, href: str) -> Paper:
    match = PAPER_NAME_RE.match(raw_filename)
    extension = ".pdf"
    year = "unknown"
    semester_code = "unknown"
    paper_type = "paper"
    if match:
        year = match.group("year").strip()
        semester_code = match.group("semester").strip().lower()
        body = match.group("body").strip()
        extension = match.group("ext")
        paper_type = extract_paper_type(body)
        course = match.group("course").strip().upper()
    else:
        suffix_match = re.search(r"(\.[A-Za-z0-9]+)$", raw_filename)
        if suffix_match:
            extension = suffix_match.group(1)
        paper_type = raw_filename.rsplit(".", 1)[0]
    preview_url = urljoin(BASE_URL, href)
    download_url = urljoin(BASE_URL, f"down.php?course={course}&id={site_id}&down=true")
    return Paper(
        course=course,
        site_id=site_id,
        raw_filename=raw_filename,
        preview_url=preview_url,
        download_url=download_url,
        year=year,
        semester_code=semester_code,
        paper_type=paper_type,
        extension=extension,
    )


def extract_paper_type(body: str) -> str:
    before_suffix = re.split(r"[~^]", body, maxsplit=1)[0]
    return before_suffix.rstrip("=_- ").strip().lower() or "paper"


def course_exists(html: str) -> bool:
    return "wrong course number" not in html.lower()
