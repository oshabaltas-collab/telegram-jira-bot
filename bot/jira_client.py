import requests
from requests.auth import HTTPBasicAuth


class JiraClient:
    def __init__(self, url: str, email: str, api_token: str):
        self.base = url.rstrip("/")
        self.auth = HTTPBasicAuth(email, api_token)
        self.headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    def create_issue(
        self,
        project_key: str,
        summary: str,
        assignee_account_id: str | None = None,
        due_date: str | None = None,
        issue_type: str = "Задание",
    ) -> dict:
        payload: dict = {
            "fields": {
                "project": {"key": project_key},
                "summary": summary,
                "issuetype": {"name": issue_type},
            }
        }
        if assignee_account_id:
            payload["fields"]["assignee"] = {"accountId": assignee_account_id}
        if due_date:
            payload["fields"]["duedate"] = due_date

        resp = requests.post(
            f"{self.base}/rest/api/3/issue",
            json=payload,
            auth=self.auth,
            headers=self.headers,
            timeout=15,
        )
        resp.raise_for_status()
        return resp.json()
