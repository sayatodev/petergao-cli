from __future__ import annotations

import asyncio
from pathlib import Path

from rich.text import Text
from textual import events, on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import DataTable, Input, Static

from petergao.client import PastPaperClient
from petergao.models import CoursePage, Paper, Quota
from petergao.paths import default_output_dir
from petergao.storage import SessionStore


class AuthScreen(ModalScreen[bool]):
    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
    ]

    def compose(self) -> ComposeResult:
        with Container(id="auth-modal"):
            with Vertical():
                yield Static("Login Required")
                yield Static("1. Enter ITSC username and domain.")
                yield Static("2. Press Enter on username to send login email.")
                yield Static("3. Paste the verification URL and press Enter.")
                yield Input(placeholder="ITSC username", id="auth-username")
                yield Input(value="@connect.ust.hk", placeholder="@connect.ust.hk", id="auth-domain")
                yield Input(placeholder="Verification URL from Outlook/Gmail", id="auth-url")
                yield Static("", id="auth-status")

    async def on_mount(self) -> None:
        self.query_one("#auth-username", Input).focus()

    @on(Input.Submitted, "#auth-username")
    async def submit_username(self) -> None:
        username = self.query_one("#auth-username", Input).value.strip()
        domain = self.query_one("#auth-domain", Input).value.strip()
        try:
            result = await asyncio.to_thread(self.app.client.start_login, username, domain)
        except Exception as exc:
            self.query_one("#auth-status", Static).update(f"Error: {exc}")
            return
        self.app.client.save(self.app.store)
        self.query_one("#auth-status", Static).update(result.message)
        self.query_one("#auth-url", Input).focus()

    @on(Input.Submitted, "#auth-url")
    async def submit_url(self) -> None:
        url = self.query_one("#auth-url", Input).value.strip()
        try:
            quota = await asyncio.to_thread(self.app.client.finish_login, url)
        except Exception as exc:
            self.query_one("#auth-status", Static).update(f"Error: {exc}")
            return
        self.app.client.save(self.app.store)
        self.query_one("#auth-status", Static).update(f"Authenticated. Quota: {quota.summary()}")
        self.dismiss(True)

    def action_cancel(self) -> None:
        self.dismiss(False)


class ConfirmDownloadScreen(ModalScreen[bool]):
    BINDINGS = [
        Binding("left", "select_no", "No"),
        Binding("right", "select_yes", "Yes"),
        Binding("enter", "submit", "Submit"),
        Binding("escape", "cancel", "Cancel"),
    ]

    def __init__(self, title: str, lines: list[str]) -> None:
        super().__init__()
        self.title = title
        self.lines = lines
        self.selection = False
        self.ready_to_submit = False

    def compose(self) -> ComposeResult:
        with Container(id="confirm-overlay"):
            with Container(id="confirm-modal"):
                with Vertical():
                    yield Static(self.title)
                    for line in self.lines:
                        yield Static(line)
                    with Horizontal(id="confirm-actions"):
                        yield Static("", id="confirm-no")
                        yield Static("", id="confirm-yes")

    async def on_mount(self) -> None:
        self.refresh_actions()
        self.set_timer(0.15, self.enable_submit)

    def action_cancel(self) -> None:
        self.dismiss(False)

    def action_select_no(self) -> None:
        self.selection = False
        self.refresh_actions()

    def action_select_yes(self) -> None:
        self.selection = True
        self.refresh_actions()

    def action_submit(self) -> None:
        if not self.ready_to_submit:
            return
        self.dismiss(self.selection)

    def refresh_actions(self) -> None:
        no_style = "#111111 on #d8d8d8" if not self.selection else "#d8d8d8"
        yes_style = "#111111 on #d8d8d8" if self.selection else "#d8d8d8"
        self.query_one("#confirm-no", Static).update(Text(" No  ", style=no_style))
        self.query_one("#confirm-yes", Static).update(Text(" Yes ", style=yes_style))

    def enable_submit(self) -> None:
        self.ready_to_submit = True


class PeterGaoTui(App[None]):
    CSS = """
    Screen {
        background: transparent;
    }

    #root {
        height: 1fr;
        layout: vertical;
    }

    #header-line {
        height: auto;
        margin: 0 1;
        border: round #a8a8a8;
        background: transparent;
        color: #d8d8d8;
    }

    #search-box {
        margin: 0 1;
        border: round #a8a8a8;
        background: transparent;
        color: #d8d8d8;
    }

    #paper-table {
        height: 1fr;
        margin: 0 1;
        border: round #a8a8a8;
        background: transparent;
        color: #d8d8d8;
    }

    #shortcut-bar {
        height: auto;
        margin: 0 1 1 1;
        border: round #a8a8a8;
        background: transparent;
        color: #c8c8c8;
    }

    #auth-modal, #confirm-modal {
        width: 80;
        max-width: 90%;
        height: auto;
        border: round #a8a8a8;
        padding: 1 2;
        background: transparent;
        color: #d8d8d8;
    }

    #confirm-overlay {
        width: 100%;
        height: 100%;
        align: center middle;
        background: transparent;
    }

    #confirm-actions {
        align: center middle;
        height: auto;
        margin-top: 1;
    }

    #confirm-no, #confirm-yes {
        width: 9;
        content-align: center middle;
        border: round #a8a8a8;
    }

    #confirm-no {
        margin-right: 2;
    }

    #confirm-yes {
        margin-left: 2;
    }
    """

    BINDINGS = [
        Binding("ctrl+q", "quit", "Quit"),
        Binding("b", "back", "Back"),
        Binding("colon", "focus_search", "Search"),
        Binding("e", "download_group('exams')", "Exams", priority=True),
        Binding("m", "download_group('midterms')", "Midterms", priority=True),
        Binding("f", "download_group('finals')", "Finals", priority=True),
        Binding("a", "download_group('all')", "All", priority=True),
        Binding("up", "cursor_up", "Up", priority=True),
        Binding("down", "cursor_down", "Down", priority=True),
    ]

    def __init__(self, client: PastPaperClient, store: SessionStore) -> None:
        super().__init__()
        self.client = client
        self.store = store
        self.courses: list[str] = []
        self.current_course: str | None = None
        self.current_page: CoursePage | None = None
        self.search_has_focus = True

    def compose(self) -> ComposeResult:
        with Container(id="root"):
            yield Static("", id="header-line")
            yield Input(placeholder=": search course code", id="search-box")
            yield DataTable(id="paper-table")
            yield Static("", id="shortcut-bar")

    async def on_mount(self) -> None:
        table = self.query_one("#paper-table", DataTable)
        table.cursor_type = "row"
        table.add_columns("#", "Year", "Sem", "Type", "Filename")
        self.query_one("#search-box", Input).focus()
        self.refresh_shortcut_bar()
        await self.initialize()

    async def initialize(self) -> None:
        quota = await asyncio.to_thread(self.client.get_quota)
        if quota is None:
            self.push_screen(AuthScreen(), self.handle_auth_result)
            return
        self.client.save(self.store)
        await self.load_catalog()

    def handle_auth_result(self, success: bool | None) -> None:
        if not success:
            self.exit()
            return
        self.call_after_refresh(self.resume_after_auth)

    async def resume_after_auth(self) -> None:
        self.client.save(self.store)
        await self.load_catalog()

    async def load_catalog(self) -> None:
        try:
            catalog = await asyncio.to_thread(self.client.fetch_catalog)
        except Exception as exc:
            self.notify(str(exc), severity="error")
            self.update_header(catalog_quota=None)
            return
        self.courses = catalog.courses
        self.current_course = None
        self.current_page = None
        self.clear_papers()
        self.update_header(catalog_quota=catalog.quota)

    def clear_papers(self) -> None:
        table = self.query_one("#paper-table", DataTable)
        table.clear(columns=False)

    def update_header(self, catalog_quota: Quota | None) -> None:
        if self.current_course and self.current_page is not None:
            current_page = f"{self.current_course} ({len(self.current_page.papers)} papers)"
        else:
            current_page = "index"
        quota_text = catalog_quota.summary() if catalog_quota is not None else "unknown"
        sign_in = "signed in" if catalog_quota is not None else "not signed in"
        self.query_one("#header-line", Static).update(f"{current_page} | {quota_text} | {sign_in}")

    def build_shortcut_bar(self) -> Text:
        text = Text()
        quit_key = "Ctrl+Q" if self.search_has_focus else "q"
        items = [
            (quit_key, "quit"),
            ("b", "back"),
            (":", "search"),
            ("↑/↓", "move"),
            ("|", None),
            ("e", "exams"),
            ("m", "midterms"),
            ("f", "finals"),
            ("a", "all"),
            ("enter", "current"),
        ]
        prefix_done = False
        for key, label in items:
            if key == "|":
                text.append(" | ", style="#b8b8b8")
                prefix_done = False
                continue
            if not prefix_done and key == "e":
                text.append("download: ", style="#b8b8b8")
            rendered_key = "Enter" if key == "enter" else key
            text.append(rendered_key, style="#efefef bold")
            text.append(f" {label}", style="#b8b8b8")
            text.append("  ")
            prefix_done = True
        return text

    def refresh_shortcut_bar(self) -> None:
        self.query_one("#shortcut-bar", Static).update(self.build_shortcut_bar())

    @on(events.DescendantFocus)
    def on_descendant_focus(self, event: events.DescendantFocus) -> None:
        if event.widget is self.query_one("#search-box", Input):
            self.search_has_focus = True
            self.refresh_shortcut_bar()
            return
        if event.widget is self.query_one("#paper-table", DataTable):
            self.search_has_focus = False
            self.refresh_shortcut_bar()

    @on(Input.Submitted, "#search-box")
    async def search_submitted(self, event: Input.Submitted) -> None:
        query = event.value.strip().upper()
        if not query:
            quota = await asyncio.to_thread(self.client.get_quota)
            self.clear_papers()
            self.current_course = None
            self.current_page = None
            self.update_header(catalog_quota=quota)
            self.query_one("#paper-table", DataTable).focus()
            self.refresh_shortcut_bar()
            return
        course = self.find_course(query)
        if course is None:
            quota = await asyncio.to_thread(self.client.get_quota)
            self.clear_papers()
            self.current_course = None
            self.current_page = None
            self.update_header(catalog_quota=quota)
            self.notify(f"No course match for {query}.", severity="warning")
            self.query_one("#paper-table", DataTable).focus()
            self.refresh_shortcut_bar()
            return
        await self.load_course(course)
        self.query_one("#paper-table", DataTable).focus()
        self.refresh_shortcut_bar()

    async def on_key(self, event) -> None:
        if event.key == "q" and self.focused is not self.query_one("#search-box", Input):
            event.stop()
            self.exit()
            return
        if event.key != "enter":
            return
        if self.focused is self.query_one("#search-box", Input):
            return
        if self.focused is not self.query_one("#paper-table", DataTable):
            return
        event.stop()
        await self.action_download_current()

    def find_course(self, query: str) -> str | None:
        exact = [course for course in self.courses if course == query]
        if exact:
            return exact[0]
        starts = [course for course in self.courses if course.startswith(query)]
        if starts:
            return starts[0]
        contains = [course for course in self.courses if query in course]
        if contains:
            return contains[0]
        return None

    async def load_course(self, course: str) -> None:
        try:
            page = await asyncio.to_thread(self.client.fetch_course, course)
        except Exception as exc:
            self.notify(str(exc), severity="error")
            return
        self.current_course = course
        self.current_page = page
        self.populate_papers(page)
        self.update_header(catalog_quota=page.quota)

    def populate_papers(self, page: CoursePage) -> None:
        table = self.query_one("#paper-table", DataTable)
        table.clear(columns=False)
        for paper in page.papers:
            table.add_row(
                str(paper.site_id),
                paper.display_year,
                paper.semester,
                paper.paper_label,
                paper.suggested_filename(),
                key=str(paper.site_id),
            )
        if page.papers:
            table.move_cursor(row=0)

    def papers_for_group(self, group: str) -> list[Paper]:
        if self.current_page is None:
            return []
        papers = self.current_page.papers
        if group == "all":
            return papers
        if group == "exams":
            return [paper for paper in papers if paper.is_exam]
        if group == "midterms":
            return [paper for paper in papers if paper.is_midterm]
        if group == "finals":
            return [paper for paper in papers if paper.is_final]
        return []

    def current_selected_paper(self) -> Paper | None:
        if self.current_page is None:
            return None
        table = self.query_one("#paper-table", DataTable)
        try:
            row_key = table.coordinate_to_cell_key(table.cursor_coordinate).row_key
        except Exception:
            return None
        if row_key is None or row_key.value is None:
            return None
        selected_id = int(str(row_key.value))
        for paper in self.current_page.papers:
            if paper.site_id == selected_id:
                return paper
        return None

    async def confirm_and_download(self, papers: list[Paper], label: str) -> None:
        if not papers:
            quota = await asyncio.to_thread(self.client.get_quota)
            self.update_header(catalog_quota=quota)
            self.notify("No matching papers for this action.", severity="warning")
            return
        quota = await asyncio.to_thread(self.client.get_quota)
        if quota is None:
            self.push_screen(AuthScreen(), lambda success: self.handle_auth_for_download(success, papers, label))
            return
        course = papers[0].course
        delta = self.client.estimate_quota_delta(course, len(papers))
        projected = quota.project(delta)
        outdir = default_output_dir()
        self.push_screen(
            ConfirmDownloadScreen(
                title=f"Download {label}",
                lines=[
                    f"Course: {course}",
                    f"Files: {len(papers)}",
                    f"Output: {outdir}",
                    f"Quota before: {quota.summary()}",
                    f"Estimated cost: {delta}",
                    f"Estimated after: {projected.summary()}",
                ],
            ),
            lambda confirmed: self.handle_download_confirmation(confirmed, papers, outdir),
        )

    def handle_auth_for_download(self, success: bool | None, papers: list[Paper], label: str) -> None:
        if not success:
            self.notify("Authentication required.", severity="warning")
            return
        self.call_after_refresh(lambda: self.confirm_and_download(papers, label))

    def handle_download_confirmation(self, confirmed: bool | None, papers: list[Paper], outdir: Path) -> None:
        if not confirmed:
            self.call_after_refresh(self.refresh_after_cancel)
            return
        self.call_after_refresh(lambda: self.run_download(papers, outdir))

    async def refresh_after_cancel(self) -> None:
        quota = await asyncio.to_thread(self.client.get_quota)
        self.update_header(catalog_quota=quota)
        self.notify("Download cancelled.")

    async def run_download(self, papers: list[Paper], outdir: Path) -> None:
        try:
            await asyncio.to_thread(self.download_sync, papers, outdir)
            refreshed = await asyncio.to_thread(self.client.get_quota)
        except Exception as exc:
            refreshed = await asyncio.to_thread(self.client.get_quota)
            self.update_header(catalog_quota=refreshed)
            self.notify(str(exc), severity="error")
            return
        self.client.save(self.store)
        self.update_header(catalog_quota=refreshed)
        self.notify(f"Downloaded {len(papers)} file(s) to {outdir}.")

    def download_sync(self, papers: list[Paper], outdir: Path) -> None:
        for paper in papers:
            self.client.download_paper(paper, outdir)
            self.client.record_download_action(paper.course, 1)
            self.client.save(self.store)

    async def action_focus_search(self) -> None:
        self.query_one("#search-box", Input).focus()
        self.refresh_shortcut_bar()

    async def action_cursor_up(self) -> None:
        self.query_one("#paper-table", DataTable).action_cursor_up()

    async def action_cursor_down(self) -> None:
        self.query_one("#paper-table", DataTable).action_cursor_down()

    async def action_download_group(self, group: str) -> None:
        labels = {
            "exams": "exams",
            "midterms": "midterms",
            "finals": "finals",
            "all": "all papers",
        }
        await self.confirm_and_download(self.papers_for_group(group), labels[group])

    async def action_download_current(self) -> None:
        paper = self.current_selected_paper()
        if paper is None:
            self.notify("No paper selected.", severity="warning")
            return
        await self.confirm_and_download([paper], "current paper")

    async def action_back(self) -> None:
        quota = await asyncio.to_thread(self.client.get_quota)
        self.query_one("#search-box", Input).value = ""
        self.current_course = None
        self.current_page = None
        self.clear_papers()
        self.update_header(catalog_quota=quota)
        self.query_one("#search-box", Input).focus()
        self.refresh_shortcut_bar()


def run_textual_tui(client: PastPaperClient, store: SessionStore) -> None:
    app = PeterGaoTui(client=client, store=store)
    app.run()
