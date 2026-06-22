"""
Jira query logic for the daily report.
"""

from datetime import date
import requests
from requests.auth import HTTPBasicAuth


class JiraReporter:
    def __init__(self, url: str, email: str, api_token: str):
        self.base = url.rstrip("/")
        self.auth = HTTPBasicAuth(email, api_token)
        self.headers = {"Accept": "application/json"}

    def _search(self, jql: str) -> list[dict]:
        fields = "summary,status,duedate,issuetype,parent"
        all_issues: list[dict] = []
        start = 0
        while True:
            resp = requests.get(
                f"{self.base}/rest/api/3/search",
                params={
                    "jql": jql,
                    "fields": fields,
                    "startAt": start,
                    "maxResults": 100,
                },
                auth=self.auth,
                headers=self.headers,
                timeout=30,
            )
            resp.raise_for_status()
            data = resp.json()
            batch = data.get("issues", [])
            all_issues.extend(batch)
            if len(all_issues) >= data.get("total", 0) or not batch:
                break
            start += len(batch)
        return all_issues

    def get_project_report(self, project_key: str) -> dict:
        today = date.today().isoformat()  # YYYY-MM-DD

        overdue = self._search(
            f'project = "{project_key}" AND duedate < "{today}"'
            f' AND statusCategory != Done ORDER BY duedate ASC'
        )
        due_today = self._search(
            f'project = "{project_key}" AND duedate = "{today}"'
            f' AND statusCategory != Done'
        )
        in_progress = self._search(
            f'project = "{project_key}" AND status = "В работе"'
        )

        return {
            "overdue": overdue,
            "due_today": due_today,
            "in_progress_count": len(in_progress),
        }
