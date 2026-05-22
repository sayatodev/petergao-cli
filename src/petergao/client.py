from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
import tempfile
from urllib.parse import parse_qs, urljoin, urlparse

import requests

from petergao.models import CourseCatalog, CoursePage, Paper, Quota
from petergao.parsing import BASE_URL, course_exists, is_authenticated, parse_course_codes, parse_papers, parse_quota
from petergao.pdf import infer_solution_marker
from petergao.storage import SessionStore, StoredSession


INDEX_URL = urljoin(BASE_URL, "index.php")
LOGIN_URL = urljoin(BASE_URL, "login.php")
LOGOUT_URL = urljoin(BASE_URL, "login.php?action=logout")
ALLOWED_HOSTS = {"petergao.cc", "www.petergao.cc"}


class PastPaperError(RuntimeError):
    pass


@dataclass(slots=True)
class LoginRequestResult:
    email: str
    message: str


class PastPaperClient:
    def __init__(self, stored: StoredSession | None = None, timeout: int = 30) -> None:
        self.session = requests.Session()
        self.timeout = timeout
        self.email = stored.email if stored else None
        self.domain = stored.domain if stored else None
        self.php_session_id = stored.php_session_id if stored else None
        self.course_download_counts = dict(stored.course_download_counts) if stored else {}
        self.last_known_quota_used = stored.last_known_quota_used if stored else None
        self.expected_quota_used = stored.last_known_quota_used if stored else None
        self.tracking_uncertain = False
        if stored and stored.cookies:
            self.session.cookies.update(stored.cookies)
        self._sync_tracking_session()

    @classmethod
    def from_store(cls, store: SessionStore, timeout: int = 30) -> "PastPaperClient":
        return cls(store.load(), timeout=timeout)

    def export_session(self) -> StoredSession:
        cookies = requests.utils.dict_from_cookiejar(self.session.cookies)
        return StoredSession(
            email=self.email,
            domain=self.domain,
            cookies=cookies,
            php_session_id=self.php_session_id,
            course_download_counts=dict(self.course_download_counts),
            last_known_quota_used=self.last_known_quota_used,
        )

    def save(self, store: SessionStore) -> None:
        store.save(self.export_session())

    def start_login(self, username: str, domain: str) -> LoginRequestResult:
        username, domain = normalize_login_identity(username, domain)
        if not username:
            raise PastPaperError("ITSC username cannot be empty.")
        if domain not in {"@connect.ust.hk", "@connect.hkust-gz.edu.cn"}:
            raise PastPaperError("Unsupported HKUST email domain.")
        self.session = requests.Session()
        self.reset_download_tracking()
        response = self.session.post(
            LOGIN_URL,
            data={"action": "login", "username": username, "domain": domain},
            timeout=self.timeout,
        )
        response.raise_for_status()
        text = response.text
        if "Invalid address" in text:
            raise PastPaperError("The website rejected that HKUST email address.")
        if "A mail is sent" not in text:
            raise PastPaperError("Unexpected login response from the website.")
        self.email = username
        self.domain = domain
        return LoginRequestResult(
            email=f"{username}{domain}",
            message="Login email sent. Paste the verification URL from your inbox into the CLI.",
        )

    def finish_login(self, verification_url: str) -> Quota:
        url = self.normalize_verification_url(verification_url)
        response = self.session.get(url, timeout=self.timeout, allow_redirects=True)
        response.raise_for_status()
        quota = parse_quota(response.text)
        if quota is None:
            quota = self.get_quota()
        if quota is None:
            raise PastPaperError("The verification link did not produce an authenticated session.")
        return quota

    def normalize_verification_url(self, value: str) -> str:
        cleaned = value.strip().strip("<>").strip("'").strip('"')
        if not cleaned:
            raise PastPaperError("Verification URL cannot be empty.")
        cleaned = unwrap_outlook_safelink(cleaned)
        parsed = urlparse(cleaned)
        if parsed.scheme and parsed.netloc:
            if parsed.hostname not in ALLOWED_HOSTS:
                raise PastPaperError("Verification URL must point to petergao.cc.")
            return cleaned
        if cleaned.startswith("/"):
            return urljoin(BASE_URL, cleaned.lstrip("/"))
        return urljoin(BASE_URL, cleaned)

    def logout(self) -> None:
        try:
            self.session.get(LOGOUT_URL, timeout=self.timeout)
        except requests.RequestException:
            pass
        self.session.cookies.clear()
        self.email = None
        self.domain = None
        self.reset_download_tracking()

    def get_index_html(self) -> str:
        response = self.session.get(INDEX_URL, timeout=self.timeout)
        response.raise_for_status()
        return response.text

    def get_quota(self) -> Quota | None:
        quota = parse_quota(self.get_index_html())
        if quota is not None:
            self.note_quota(quota)
        return quota

    def authenticated(self) -> bool:
        return self.get_quota() is not None

    def fetch_catalog(self) -> CourseCatalog:
        html = self.get_index_html()
        return CourseCatalog(
            courses=parse_course_codes(html),
            quota=parse_quota(html),
            authenticated=is_authenticated(html),
        )

    def fetch_course(self, course: str) -> CoursePage:
        normalized = course.strip().upper()
        if not normalized:
            raise PastPaperError("Course code cannot be empty.")
        response = self.session.get(INDEX_URL, params={"course": normalized}, timeout=self.timeout)
        response.raise_for_status()
        html = response.text
        exists = course_exists(html)
        papers = parse_papers(html, normalized) if exists else []
        return CoursePage(
            course=normalized,
            papers=papers,
            quota=parse_quota(html),
            authenticated=is_authenticated(html),
            exists=exists,
        )

    def download_paper(self, paper: Paper, destination_dir: Path) -> Path:
        destination_dir.mkdir(parents=True, exist_ok=True)
        suffix = (paper.extension.lower() or ".pdf") + ".part"
        temporary_handle = tempfile.NamedTemporaryFile(
            dir=destination_dir,
            prefix=f".{paper.course}_{paper.site_id}_",
            suffix=suffix,
            delete=False,
        )
        temporary_handle.close()
        partial = Path(temporary_handle.name)
        try:
            with self.session.get(paper.download_url, stream=True, timeout=self.timeout) as response:
                response.raise_for_status()
                iterator = response.iter_content(chunk_size=65536)
                first_chunk = next(iterator, b"")
                if looks_like_html_response(response.headers.get("Content-Type", ""), first_chunk):
                    snippet = first_chunk.decode("utf-8", errors="ignore")
                    message = sniff_html_error(snippet) or "Server returned HTML instead of a file. Your session may have expired."
                    raise PastPaperError(message)
                with partial.open("wb") as handle:
                    if first_chunk:
                        handle.write(first_chunk)
                    for chunk in iterator:
                        if chunk:
                            handle.write(chunk)
            is_solution = infer_solution_marker(partial, paper)
            target = ensure_unique_download_path(
                destination_dir=destination_dir,
                stem=paper.suggested_stem(is_solution=is_solution),
                extension=paper.extension.lower() or ".pdf",
            )
            partial.replace(target)
        except Exception:
            if partial.exists():
                partial.unlink()
            raise
        return target

    def estimate_quota_delta(self, course: str, requested_count: int) -> int:
        self._sync_tracking_session()
        normalized = course.upper()
        current = self.course_download_counts.get(normalized, 0)
        remaining_metered = max(12 - current, 0)
        return min(max(requested_count, 0), remaining_metered)

    def record_download(self, course: str, successful_count: int = 1) -> None:
        self._sync_tracking_session()
        normalized = course.upper()
        current = self.course_download_counts.get(normalized, 0)
        remaining_metered = max(12 - current, 0)
        metered = min(max(successful_count, 0), remaining_metered)
        self.course_download_counts[normalized] = min(current + max(successful_count, 0), 12)
        if self.expected_quota_used is not None:
            self.expected_quota_used += metered

    def record_download_action(self, course: str, requested_count: int) -> None:
        self._sync_tracking_session()
        normalized = course.upper()
        current = self.course_download_counts.get(normalized, 0)
        successful = max(requested_count, 0)
        metered = min(successful, max(12 - current, 0))
        self.course_download_counts[normalized] = min(current + successful, 12)
        if self.expected_quota_used is not None:
            self.expected_quota_used += metered

    def note_quota(self, quota: Quota) -> None:
        self._sync_tracking_session()
        if self.last_known_quota_used is not None:
            if quota.used < self.last_known_quota_used:
                self.reset_download_tracking()
                self.last_known_quota_used = quota.used
                self.expected_quota_used = quota.used
                return
            expected = self.expected_quota_used if self.expected_quota_used is not None else self.last_known_quota_used
            if quota.used != expected and quota.used != self.last_known_quota_used:
                self.tracking_uncertain = True
        self.last_known_quota_used = quota.used
        self.expected_quota_used = quota.used

    def tracking_warning(self) -> str | None:
        if self.tracking_uncertain:
            return "Quota estimate may drift because this login session was also used outside this CLI."
        return None

    def reset_download_tracking(self) -> None:
        self.course_download_counts = {}
        self.last_known_quota_used = None
        self.expected_quota_used = None
        self.tracking_uncertain = False
        self.php_session_id = self.current_php_session_id()

    def current_php_session_id(self) -> str | None:
        return self.session.cookies.get("PHPSESSID")

    def _sync_tracking_session(self) -> None:
        current = self.current_php_session_id()
        if self.php_session_id is None:
            self.php_session_id = current
            return
        if current is None:
            self.reset_download_tracking()
            return
        if current != self.php_session_id:
            self.reset_download_tracking()
            self.php_session_id = current


def looks_like_html_response(content_type: str, first_chunk: bytes) -> bool:
    lowered = content_type.lower()
    if "text/html" in lowered:
        return True
    return bool(re.match(rb"\s*<(?:!doctype|html|head|body)\b", first_chunk.lower()))


def sniff_html_error(snippet: str) -> str | None:
    lowered = snippet.lower()
    if "login" in lowered and "email" in lowered:
        return "Authentication is required. Run `petergao auth` again."
    if "quota" in lowered and "loss of quota" in lowered:
        return "The website returned an HTML page instead of a download."
    return None


def ensure_unique_download_path(destination_dir: Path, stem: str, extension: str) -> Path:
    path = destination_dir / f"{stem}{extension}"
    if not path.exists():
        return path
    counter = 1
    while True:
        candidate = destination_dir / f"{stem}_{counter}{extension}"
        if not candidate.exists():
            return candidate
        counter += 1


def normalize_login_identity(username: str, domain: str) -> tuple[str, str]:
    cleaned = username.strip()
    lowered = cleaned.lower()
    for known_domain in ("@connect.ust.hk", "@connect.hkust-gz.edu.cn"):
        if lowered.endswith(known_domain):
            return cleaned[: -len(known_domain)], known_domain
    return cleaned, domain


def unwrap_outlook_safelink(value: str) -> str:
    parsed = urlparse(value)
    hostname = (parsed.hostname or "").lower()
    if not hostname.endswith("safelinks.protection.outlook.com"):
        return value
    query = parse_qs(parsed.query)
    nested = query.get("url")
    if not nested or not nested[0].strip():
        raise PastPaperError("Outlook Safe Link is missing the nested verification URL.")
    return nested[0].strip()
