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

    def get_transitions(self, issue_key: str) -> list[dict]:
        resp = requests.get(
            f"{self.base}/rest/api/3/issue/{issue_key}/transitions",
            auth=self.auth,
            headers=self.headers,
            timeout=15,
        )
        resp.raise_for_status()
        return resp.json().get("transitions", [])

    def transition_to_backlog(self, issue_key: str) -> None:
        transitions = self.get_transitions(issue_key)
        backlog = next((t for t in transitions if t["name"].lower() == "backlog"), None)
        if backlog:
            requests.post(
                f"{self.base}/rest/api/3/issue/{issue_key}/transitions",
                json={"transition": {"id": backlog["id"]}},
                auth=self.auth,
                headers=self.headers,
                timeout=15,
            ).raise_for_status()
