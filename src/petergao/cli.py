from __future__ import annotations

import argparse
from collections import Counter
from pathlib import Path
import sys
from typing import Iterable

from petergao.client import PastPaperClient, PastPaperError
from petergao.models import CourseCatalog, CoursePage, Paper, Quota
from petergao.paths import default_output_dir
from petergao.storage import SessionStore
from petergao.tui import run_textual_tui


DOMAIN_CHOICES = ("@connect.ust.hk", "@connect.hkust-gz.edu.cn")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="petergao",
        description="CLI for browsing and downloading HKUST past papers from petergao.cc.",
    )
    subparsers = parser.add_subparsers(dest="command")

    auth_parser = subparsers.add_parser("auth", help="Authenticate with HKUST email")
    auth_parser.add_argument("--username", help="ITSC username without domain")
    auth_parser.add_argument(
        "--domain",
        choices=DOMAIN_CHOICES,
        help="HKUST email domain",
    )
    auth_parser.add_argument("--verification-url", help="Verification URL from the login email")

    subparsers.add_parser("logout", help="Clear the stored session")
    subparsers.add_parser("quota", help="Show current download quota")

    courses_parser = subparsers.add_parser("courses", help="List courses, optionally filtered by query")
    courses_parser.add_argument("query", nargs="?", default="", help="Substring filter")

    list_parser = subparsers.add_parser("list", help="List available papers for a course")
    list_parser.add_argument("course", help="Course code, for example COMP2011")

    download_parser = subparsers.add_parser("download", help="Download papers for a course")
    download_parser.add_argument("course", help="Course code, for example COMP2011")
    download_parser.add_argument("-a", "--all", action="store_true", help="Download all papers")
    download_parser.add_argument("-m", "--midterm", action="store_true", help="Download all midterms")
    download_parser.add_argument("-f", "--finals", action="store_true", help="Download all finals")
    download_parser.add_argument("-e", "--exams", action="store_true", help="Download all midterms and finals")
    download_parser.add_argument("-q", "--quiz", action="store_true", help="Download all quizzes")
    download_parser.add_argument("--type", action="append", default=[], help="Download a specific paper type")
    download_parser.add_argument("--year", help="Only download a specific year token, for example 2014")
    download_parser.add_argument(
        "--semester",
        choices=["f", "fall", "s", "spring", "sum", "summer", "w", "winter"],
        help="Only download a specific semester",
    )
    download_parser.add_argument("-o", "--outdir", "--output", dest="outdir", type=Path, help="Destination directory")
    download_parser.add_argument("--dry-run", action="store_true", help="Preview files without downloading them")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    store = SessionStore()
    client = PastPaperClient.from_store(store)
    try:
        if args.command is None:
            run_textual_tui(client, store)
            return 0
        if args.command == "auth":
            quota = run_auth(
                client,
                store,
                username=args.username,
                domain=args.domain,
                verification_url=args.verification_url,
            )
            print_quota(quota)
            client.save(store)
            return 0
        if args.command == "logout":
            quota = try_quota(client)
            client.logout()
            store.clear()
            if quota is not None:
                print(f"Logged out. Quota before logout: {quota.summary()}")
            else:
                print("Logged out.")
            return 0
        if args.command == "quota":
            ensure_authenticated(client, store)
            quota = require_quota(client)
            print_quota(quota)
            client.save(store)
            return 0
        if args.command == "courses":
            ensure_authenticated(client, store)
            catalog = client.fetch_catalog()
            quota = require_quota_value(catalog.quota)
            show_courses(catalog, args.query)
            print_quota(quota)
            client.save(store)
            return 0
        if args.command == "list":
            ensure_authenticated(client, store)
            page = client.fetch_course(args.course)
            quota = require_quota_from_page(page)
            print_course_listing(page)
            print_quota(quota)
            client.save(store)
            return 0
        if args.command == "download":
            ensure_authenticated(client, store)
            page = client.fetch_course(args.course)
            page_quota = require_quota_from_page(page)
            if not page.exists:
                print_quota(page_quota)
                raise PastPaperError(f"{page.course} does not exist on the website.")
            papers = select_papers(page.papers, args)
            if not papers:
                print_quota(page_quota)
                raise PastPaperError("No papers matched that filter.")
            print_quota_projection(client, page.course, page_quota, len(papers))
            if args.dry_run:
                print_download_preview(page.course, papers, args.outdir or default_output_dir())
                print_quota(page_quota)
                save_tracking_state(client, store)
                return 0
            output_dir = args.outdir or default_output_dir()
            completed = 0
            for paper in papers:
                path = client.download_paper(paper, output_dir)
                completed += 1
                client.record_download_action(page.course, 1)
                save_tracking_state(client, store)
                print(f"Saved {paper.raw_filename} -> {path}")
            refreshed_quota = require_quota(client)
            if completed != len(papers):
                print(f"Completed {completed} of {len(papers)} downloads.")
            print_quota(refreshed_quota)
            save_tracking_state(client, store)
            return 0
    except KeyboardInterrupt:
        print("\nCancelled.", file=sys.stderr)
        return 130
    except PastPaperError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # pragma: no cover - user-facing guardrail
        print(f"Unexpected error: {exc}", file=sys.stderr)
        return 1
    return 0


def run_auth(
    client: PastPaperClient,
    store: SessionStore,
    username: str | None = None,
    domain: str | None = None,
    verification_url: str | None = None,
) -> Quota:
    chosen_username = username or prompt("HKUST ITSC username")
    chosen_domain = domain or prompt_domain()
    result = client.start_login(chosen_username, chosen_domain)
    print(result.message)
    chosen_verification_url = verification_url or prompt("Paste the verification URL")
    quota = client.finish_login(chosen_verification_url)
    client.save(store)
    return quota


def ensure_authenticated(client: PastPaperClient, store: SessionStore) -> None:
    if try_quota(client) is not None:
        return
    if not sys.stdin.isatty():
        raise PastPaperError("No valid session found. Run `petergao auth` in a terminal first.")
    quota = run_auth(client, store)
    print(f"Authenticated. Quota: {quota.summary()}")


def require_quota(client: PastPaperClient) -> Quota:
    quota = client.get_quota()
    if quota is None:
        raise PastPaperError("Unable to read quota from the website.")
    return quota


def try_quota(client: PastPaperClient) -> Quota | None:
    try:
        return client.get_quota()
    except Exception:
        return None


def require_quota_value(quota: Quota | None) -> Quota:
    if quota is None:
        raise PastPaperError("Unable to read quota from the website.")
    return quota


def require_quota_from_page(page: CoursePage) -> Quota:
    if page.quota is not None:
        return page.quota
    raise PastPaperError("Unable to read quota from the website.")


def prompt(label: str) -> str:
    value = input(f"{label}: ").strip()
    if not value:
        raise PastPaperError(f"{label} cannot be empty.")
    return value


def prompt_domain() -> str:
    print("Email domain:")
    for index, domain in enumerate(DOMAIN_CHOICES, start=1):
        print(f"  {index}. {domain}")
    value = input("Choose domain [1]: ").strip() or "1"
    try:
        choice = int(value)
    except ValueError as exc:
        raise PastPaperError("Please enter 1 or 2 for the email domain.") from exc
    if choice < 1 or choice > len(DOMAIN_CHOICES):
        raise PastPaperError("Please enter 1 or 2 for the email domain.")
    return DOMAIN_CHOICES[choice - 1]


def print_quota(quota: Quota) -> None:
    print(f"Quota remaining: {quota.summary()}")


def print_quota_projection(client: PastPaperClient, course: str, quota_before: Quota, requested_count: int) -> None:
    delta = client.estimate_quota_delta(course, requested_count)
    quota_after = quota_before.project(delta)
    print(f"Quota before action: {quota_before.summary()}")
    print(f"Estimated quota cost for this action: {delta}")
    print(f"Estimated quota after action: {quota_after.summary()}")
    warning = client.tracking_warning()
    if warning:
        print(f"Note: {warning}")


def show_courses(catalog: CourseCatalog, query: str) -> None:
    matches = search_courses(catalog.courses, query)
    if not matches:
        raise PastPaperError(f"No courses matched '{query}'.")
    print(f"Courses: {len(matches)} match(es)")
    for course in matches:
        print(course)


def search_courses(courses: Iterable[str], query: str) -> list[str]:
    normalized = query.strip().upper()
    items = sorted(set(course.upper() for course in courses))
    if not normalized:
        return items
    exact = [course for course in items if course == normalized]
    if exact:
        return exact
    starts = [course for course in items if course.startswith(normalized)]
    contains = [course for course in items if normalized in course and course not in starts]
    return starts + contains


def print_course_listing(page: CoursePage) -> None:
    if not page.exists:
        quota = require_quota_from_page(page)
        print_quota(quota)
        raise PastPaperError(f"{page.course} does not exist on the website.")
    print(f"{page.course}: {len(page.papers)} paper(s)")
    print_type_summary(page.papers)
    print_papers_table(page.papers)


def print_type_summary(papers: list[Paper]) -> None:
    counts = Counter(paper.paper_label for paper in papers)
    summary = ", ".join(f"{label}: {counts[label]}" for label in sorted(counts))
    print(f"Types: {summary}")


def print_papers_table(papers: list[Paper]) -> None:
    rows = [
        ["#", "Year", "Sem", "Type", "Suggested filename"],
    ]
    for paper in papers:
        rows.append(
            [
                str(paper.site_id),
                paper.display_year,
                paper.semester,
                paper.paper_label,
                paper.suggested_filename(),
            ]
        )
    widths = [max(len(row[index]) for row in rows) for index in range(len(rows[0]))]
    for index, row in enumerate(rows):
        print("  ".join(value.ljust(widths[col]) for col, value in enumerate(row)))
        if index == 0:
            print("  ".join("-" * widths[col] for col in range(len(widths))))


def select_papers(papers: list[Paper], args: argparse.Namespace) -> list[Paper]:
    chosen = papers
    selectors_applied = any([args.all, args.midterm, args.finals, args.exams, args.quiz, args.type])
    if not args.all:
        if not selectors_applied:
            args.exams = True
        filters: list[Paper] = []
        for paper in papers:
            include = False
            if args.midterm and paper.is_midterm:
                include = True
            if args.finals and paper.is_final:
                include = True
            if args.exams and paper.is_exam:
                include = True
            if args.quiz and paper.is_quiz:
                include = True
            if args.type and any(paper.matches_type(value) for value in args.type):
                include = True
            if include:
                filters.append(paper)
        chosen = filters
    if args.year:
        chosen = [paper for paper in chosen if paper.year == args.year]
    if args.semester:
        chosen = [paper for paper in chosen if paper.matches_semester(args.semester)]
    return chosen


def print_download_preview(course: str, papers: list[Paper], output_dir: Path) -> None:
    print(f"Dry run for {course}: {len(papers)} file(s) -> {output_dir}")
    print_papers_table(papers)


def save_tracking_state(client: PastPaperClient, store: SessionStore) -> None:
    client.save(store)
