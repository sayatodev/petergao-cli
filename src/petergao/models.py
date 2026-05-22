from __future__ import annotations

from dataclasses import dataclass
import re


SEMESTER_NAMES = {
    "f": "Fall",
    "s": "Spring",
    "sum": "Summer",
    "w": "Winter",
}

SEMESTER_FILENAME_CODES = {
    "f": "F",
    "s": "S",
    "sum": "SUM",
    "w": "W",
}


def prettify_paper_type(value: str) -> str:
    normalized = value.strip().lower()
    known = {
        "midterm": "Midterm",
        "midterm1": "Midterm-1",
        "midterm2": "Midterm-2",
        "midterm_review": "Midterm-Review",
        "final": "Final",
        "quiz": "Quiz",
    }
    if normalized in known:
        return known[normalized]
    words = re.split(r"[^a-z0-9]+", normalized)
    return "-".join(word.capitalize() for word in words if word) or "Paper"


def slugify_filename_part(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9-]+", "-", value).strip("-")
    return cleaned or "paper"


def normalize_year_token(value: str) -> str:
    year = value.strip()
    if re.fullmatch(r"200\d{2}", year):
        return f"20{year[-2:]}"
    return year


@dataclass(slots=True)
class Quota:
    used: int
    total: int

    @property
    def remaining(self) -> int:
        return max(self.total - self.used, 0)

    def summary(self) -> str:
        return f"{self.remaining} remaining ({self.used}/{self.total} used)"

    def project(self, additional_used: int) -> "Quota":
        return Quota(used=self.used + max(additional_used, 0), total=self.total)


@dataclass(slots=True)
class CourseCatalog:
    courses: list[str]
    quota: Quota | None
    authenticated: bool


@dataclass(slots=True)
class Paper:
    course: str
    site_id: int
    raw_filename: str
    preview_url: str
    download_url: str
    year: str
    semester_code: str
    paper_type: str
    extension: str

    @property
    def semester(self) -> str:
        return SEMESTER_NAMES.get(self.semester_code.lower(), self.semester_code.upper())

    @property
    def display_year(self) -> str:
        return normalize_year_token(self.year)

    @property
    def paper_label(self) -> str:
        return prettify_paper_type(self.paper_type)

    @property
    def filename_paper_type(self) -> str:
        known = {
            "midterm": "midterm",
            "midterm1": "midterm1",
            "midterm2": "midterm2",
            "midterm_review": "midterm-review",
            "final": "final",
            "quiz": "quiz",
        }
        normalized = self.paper_type.strip().lower()
        if normalized in known:
            return known[normalized]
        return slugify_filename_part(normalized).lower()

    @property
    def filename_term(self) -> str:
        year = self.display_year
        if re.fullmatch(r"\d{4}", year):
            year = year[-2:]
        semester = SEMESTER_FILENAME_CODES.get(self.semester_code.lower(), self.semester_code.upper())
        return f"{year}{semester}"

    @property
    def is_midterm(self) -> bool:
        return self.paper_type.lower().startswith("mid")

    @property
    def is_final(self) -> bool:
        return self.paper_type.lower().startswith("final")

    @property
    def is_quiz(self) -> bool:
        return self.paper_type.lower().startswith("quiz")

    @property
    def is_exam(self) -> bool:
        return self.is_midterm or self.is_final

    def matches_semester(self, value: str) -> bool:
        normalized = value.strip().lower()
        return normalized in {self.semester_code.lower(), self.semester.lower()}

    def matches_type(self, value: str) -> bool:
        normalized = value.strip().lower()
        if normalized == "exam":
            return self.is_exam
        if normalized == "exams":
            return self.is_exam
        if normalized == "midterm":
            return self.is_midterm
        if normalized == "final":
            return self.is_final
        if normalized == "quiz":
            return self.is_quiz
        return normalized == self.paper_type.lower()

    def suggested_stem(self, is_solution: bool = False) -> str:
        parts = [
            slugify_filename_part(self.course.upper()),
            slugify_filename_part(self.filename_term),
            slugify_filename_part(self.filename_paper_type).lower(),
        ]
        if is_solution:
            parts.append("sol")
        return "_".join(parts)

    def suggested_filename(self, is_solution: bool = False, duplicate_index: int | None = None) -> str:
        stem = self.suggested_stem(is_solution=is_solution)
        if duplicate_index is not None:
            stem = f"{stem}_{duplicate_index}"
        extension = self.extension.lower() or ".pdf"
        return stem + extension


@dataclass(slots=True)
class CoursePage:
    course: str
    papers: list[Paper]
    quota: Quota | None
    authenticated: bool
    exists: bool
