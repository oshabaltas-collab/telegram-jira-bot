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
_TG_LIMIT = 4000  # safe margin under Telegram's 4096-char message cap


class JiraReporter:
    def __init__(self, url: str, email: str, api_token: str):
        self.base = url.rstrip("/")
        self.auth = HTTPBasicAuth(email, api_token)
        self.headers = {"Accept": "application/json"}

    def _search(self, jql: str) -> list[dict]:
        # Uses the enhanced JQL search endpoint (/search/jql); the legacy
        # /rest/api/3/search was removed by Atlassian (returns 410 Gone).
        # Pagination is token-based: follow nextPageToken until isLast.
        fields = "summary,status,duedate,issuetype,parent,assignee"
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

    def _buckets(self, scope_jql: str) -> dict:
        """Three buckets for a scope (a project or an assignee)."""
        today = date.today().isoformat()
        overdue = self._search(
            f'{scope_jql} AND duedate < "{today}" AND statusCategory != Done ORDER BY duedate ASC'
        )
        due_today = self._search(
            f'{scope_jql} AND duedate = "{today}" AND statusCategory != Done'
        )
        in_progress = self._search(
            f'{scope_jql} AND status = "В работе" ORDER BY duedate ASC'
        )
        return {"overdue": overdue, "due_today": due_today, "in_progress": in_progress}

    def get_project_report(self, project_key: str) -> dict:
        return self._buckets(f'project = "{project_key}"')

    def get_person_report(self, account_id: str) -> dict:
        return self._buckets(f'assignee = "{account_id}"')


# ── Formatting helpers ────────────────────────────────────────────────────────

def _assignee(issue: dict, names: dict | None) -> str:
    a = issue["fields"].get("assignee")
    if not a:
        return "не назначен"
    acct = a.get("accountId", "")
    if names and acct in names:
        return names[acct]
    return a.get("displayName", "не назначен")


def _days_overdue(issue: dict) -> int | None:
    due = issue["fields"].get("duedate")
    if not due:
        return None
    try:
        y, m, d = (int(x) for x in due.split("-"))
        return (date.today() - date(y, m, d)).days
    except Exception:
        return None


def _fmt_issue(issue: dict, names: dict | None = None,
               show_status: bool = False, show_days: bool = False) -> str:
    key = issue["key"]
    fields = issue["fields"]
    summary = fields.get("summary", "—")
    url = f"{_JIRA_BASE}/browse/{key}"

    meta = [_assignee(issue, names)]
    if show_status:
        status = fields.get("status", {}).get("name")
        if status:
            meta.append(status)
    if show_days:
        n = _days_overdue(issue)
        if n is not None and n > 0:
            meta.append(f"просрочено {n} дн.")

    tail = f"  <i>({' · '.join(meta)})</i>" if meta else ""
    return f'• <a href="{url}">{key}</a> — {summary}{tail}'


def _section(title: str, issues: list[dict], names: dict | None,
             show_status: bool, show_days: bool) -> list[str]:
    lines = [title]
    for issue in issues[:_MAX_PER_SECTION]:
        lines.append(_fmt_issue(issue, names, show_status, show_days))
    if len(issues) > _MAX_PER_SECTION:
        lines.append(f"  … и ещё {len(issues) - _MAX_PER_SECTION}")
    return lines


def _dedup(report: dict) -> dict:
    """Each task appears once, by priority: overdue > due_today > in_progress."""
    seen: set[str] = set()
    out: dict = {}
    for bucket in ("overdue", "due_today", "in_progress"):
        kept = []
        for iss in report[bucket]:
            if iss["key"] in seen:
                continue
            seen.add(iss["key"])
            kept.append(iss)
        out[bucket] = kept
    return out


def _counts_line(report: dict) -> str:
    return (f"🔄 {len(report['in_progress'])} · "
            f"📅 {len(report['due_today'])} · "
            f"🔴 {len(report['overdue'])}")


def build_project_block(proj: dict, report: dict, names: dict | None = None) -> str:
    report = _dedup(report)
    lines = [f"<b>📁 {proj['name']}</b>  —  {_counts_line(report)}"]

    if report["in_progress"]:
        lines += _section(f"🔄 <b>В работе ({len(report['in_progress'])}):</b>",
                          report["in_progress"], names, show_status=False, show_days=False)
    if report["due_today"]:
        lines += _section(f"📅 <b>Дедлайн сегодня ({len(report['due_today'])}):</b>",
                          report["due_today"], names, show_status=True, show_days=False)
    if report["overdue"]:
        lines += _section(f"🔴 <b>Просрочено ({len(report['overdue'])}):</b>",
                          report["overdue"], names, show_status=True, show_days=True)

    if not (report["in_progress"] or report["due_today"] or report["overdue"]):
        lines.append("✅ Активных задач нет")

    return "\n".join(lines)


def build_person_block(person_name: str, report: dict, names: dict | None = None) -> str:
    """On-demand report for one assignee across all projects (KEY shows the project)."""
    report = _dedup(report)
    lines = [f"<b>👤 {person_name}</b>  —  {_counts_line(report)}"]

    if report["in_progress"]:
        lines += _section(f"🔄 <b>В работе ({len(report['in_progress'])}):</b>",
                          report["in_progress"], names, show_status=False, show_days=False)
    if report["due_today"]:
        lines += _section(f"📅 <b>Дедлайн сегодня ({len(report['due_today'])}):</b>",
                          report["due_today"], names, show_status=True, show_days=False)
    if report["overdue"]:
        lines += _section(f"🔴 <b>Просрочено ({len(report['overdue'])}):</b>",
                          report["overdue"], names, show_status=True, show_days=True)

    if not (report["in_progress"] or report["due_today"] or report["overdue"]):
        lines.append("✅ Активных задач нет")

    return "\n".join(lines)


def build_full_report(projects: list[dict], jira: JiraReporter,
                      names: dict | None = None) -> tuple[str, list[str]]:
    """Returns (header_text, [project_block, ...]).
    Header includes global totals; each block is HTML-formatted.
    """
    today_label = date.today().strftime("%d.%m.%Y")

    blocks: list[str] = []
    tot_ip = tot_dt = tot_od = 0
    for proj in projects:
        try:
            report = jira.get_project_report(proj["jira_key"])
            d = _dedup(report)
            tot_ip += len(d["in_progress"]); tot_dt += len(d["due_today"]); tot_od += len(d["overdue"])
            blocks.append(build_project_block(proj, report, names))
        except Exception as exc:
            logger.exception("Jira query failed for %s: %s", proj["jira_key"], exc)
            blocks.append(f"<b>📁 {proj['name']}</b>\n⚠️ Ошибка запроса к Jira")

    header = (f"📊 <b>Ежедневный отчёт — {today_label}</b>\n"
              f"Всего: 🔄 в работе {tot_ip} · 📅 сегодня {tot_dt} · 🔴 просрочено {tot_od}")
    return header, blocks


def split_message(text: str, limit: int = _TG_LIMIT) -> list[str]:
    """Split a block into ≤limit-char messages without breaking a line."""
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    current = ""
    for line in text.split("\n"):
        if current and len(current) + 1 + len(line) > limit:
            chunks.append(current)
            current = line
        else:
            current = f"{current}\n{line}" if current else line
    if current:
        chunks.append(current)
    return chunks
