import requests
import os

class GitHubActions:
    def __init__(self):
        self.token = os.getenv("GITHUB_TOKEN")
        self.base_url = "https://api.github.com"
        self.headers = {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/vnd.github+json"
        }

    def create_repo(self, name, private=False):
        payload = {"name": name, "private": private}
        r = requests.post(
            f"{self.base_url}/user/repos",
            headers=self.headers,
            json=payload
        )
        return r.json()

    def list_repos(self):
        r = requests.get(
            f"{self.base_url}/user/repos",
            headers=self.headers
        )
        return r.json()

    def create_issue(self, repo, title, body=""):
        payload = {"title": title, "body": body}
        r = requests.post(
            f"{self.base_url}/repos/{repo}/issues",
            headers=self.headers,
            json=payload
        )
        return r.json()