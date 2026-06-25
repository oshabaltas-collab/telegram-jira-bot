"""
Jira query and report formatting logic — shared by daily cron and on-demand handler.
"""

import logging
from datetime import date
import requests
from requests.auth import HTTPBasicAuth

logger = logging.getLogger(__name__)

_JIRA_BASE = "https://fincortex.atlassian.net"
_MAX_PER_SECTION = 20


class JiraReporter:
    def __init__(self, url: str, email: str, api_token: str):
        self.base = url.rstrip("/")
        self.auth = HTTPBasicAuth(email, api_token)
        self.headers = {"Accept": "application/json"}

    def _search(self, jql: str) -> list[dict]:
        # Uses the enhanced JQL search endpoint (/search/jql); the legacy
        # /rest/api/3/search was removed by Atlassian (returns 410 Gone).
        # Pagination is token-based: follow nextPageToken until isLast.
        fields = "summary,status,duedate,issuetype,parent"
        all_issues: list[dict] = []
        next_token: str | None = None
        while True:
            params = {"jql": jql, "fields": fields, "maxResults": 100}
            if next_token:
                params["nextPageToken"] = next_token
            resp = requests.get(
                f"{self.base}/rest/api/3/search/jql",
                params=params,
                auth=self.auth,
                headers=self.headers,
                timeout=30,
            )
            resp.raise_for_status()
            data = resp.json()
            all_issues.extend(data.get("issues", []))
            if data.get("isLast", True) or not data.get("nextPageToken"):
                break
            next_token = data["nextPageToken"]
        return all_issues

    def get_project_report(self, project_key: str) -> dict:
        today = date.today().isoformat()

        overdue = self._search(
            f'project = "{project_key}" AND duedate < "{today}"'
            f' AND statusCategory != Done ORDER BY duedate ASC'
        )
        due_today = self._search(
            f'project = "{project_key}" AND duedate = "{today}"'
            f' AND statusCategory != Done'
        )
        in_progress = self._search(
            f'project = "{project_key}" AND status = "В работе" ORDER BY duedate ASC'
        )

        return {
            "overdue": overdue,
            "due_today": due_today,
            "in_progress": in_progress,
        }


# ── Formatting helpers ────────────────────────────────────────────────────────

def _fmt_issue(issue: dict) -> str:
    key = issue["key"]
    summary = issue["fields"].get("summary", "—")
    url = f"{_JIRA_BASE}/browse/{key}"
    return f'• <a href="{url}">{key}</a> — {summary}'


def _section(title: str, issues: list[dict]) -> list[str]:
    lines = [title]
    for issue in issues[:_MAX_PER_SECTION]:
        lines.append(_fmt_issue(issue))
    if len(issues) > _MAX_PER_SECTION:
        lines.append(f"  … и ещё {len(issues) - _MAX_PER_SECTION}")
    return lines


def build_project_block(proj: dict, report: dict) -> str:
    lines = [f"<b>📁 {proj['name']}</b>"]

    if report["in_progress"]:
        lines += _section(
            f"🔄 <b>В работе ({len(report['in_progress'])}):</b>",
            report["in_progress"],
        )
    if report["due_today"]:
        lines += _section(
            f"📅 <b>Дедлайн сегодня ({len(report['due_today'])}):</b>",
            report["due_today"],
        )
    if report["overdue"]:
        lines += _section(
            f"🔴 <b>Просрочено ({len(report['overdue'])}):</b>",
            report["overdue"],
        )

    if not (report["in_progress"] or report["due_today"] or report["overdue"]):
        lines.append("✅ Активных задач нет")

    return "\n".join(lines)


def build_full_report(projects: list[dict], jira: JiraReporter) -> tuple[str, list[str]]:
    """Returns (header_text, [project_block, ...]).
    Each block is HTML-formatted and safe to send as a standalone Telegram message.
    """
    today_label = date.today().strftime("%d.%m.%Y")
    header = f"📊 <b>Ежедневный отчёт — {today_label}</b>"

    blocks: list[str] = []
    for proj in projects:
        try:
            report = jira.get_project_report(proj["jira_key"])
            blocks.append(build_project_block(proj, report))
        except Exception as exc:
            logger.exception("Jira query failed for %s: %s", proj["jira_key"], exc)
            blocks.append(f"<b>📁 {proj['name']}</b>\n⚠️ Ошибка запроса к Jira")

    return header, blocks
