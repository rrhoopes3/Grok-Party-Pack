"""
GitHub integration tools — PR creation, issue management, CI status.

Uses the GitHub REST API (via urllib) so no extra dependencies are needed.
Requires a GITHUB_TOKEN env var for authenticated operations.

Tools:
    github_list_issues      — List issues/PRs for a repo
    github_get_issue        — Get full issue/PR details + comments
    github_create_issue     — Create a new issue
    github_create_pr        — Create a pull request
    github_pr_review        — Add a review comment to a PR
    github_ci_status        — Get CI/check run status for a ref
    github_list_repos       — List repos for a user/org
    github_search_code      — Search code across GitHub
"""
from __future__ import annotations

import json
import logging
import urllib.request
import urllib.error
import urllib.parse

from .registry import ToolRegistry

log = logging.getLogger("forge.tools.github")

_token: str | None = None


def _get_token() -> str:
    global _token
    if _token is None:
        from forge.config import GITHUB_TOKEN
        _token = GITHUB_TOKEN
    return _token


def _github_request(
    method: str,
    path: str,
    body: dict | None = None,
    query: dict | None = None,
    token: str = "",
) -> str:
    """Make a request to the GitHub API and return JSON string."""
    base = "https://api.github.com"
    url = f"{base}{path}"
    if query:
        url += "?" + urllib.parse.urlencode(query)

    data = json.dumps(body).encode("utf-8") if body else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    req.add_header("User-Agent", "Forge/1.0")

    tok = token or _get_token()
    if tok:
        req.add_header("Authorization", f"Bearer {tok}")
    if body:
        req.add_header("Content-Type", "application/json")

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        error_body = e.read().decode("utf-8", errors="replace")
        return json.dumps({"error": f"HTTP {e.code}: {error_body[:500]}"})
    except Exception as e:
        return json.dumps({"error": f"{type(e).__name__}: {e}"})


# ── Tool Implementations ─────────────────────────────────────────────────

def github_list_issues(
    owner: str,
    repo: str,
    state: str = "open",
    labels: str = "",
    per_page: int = 20,
) -> str:
    """List issues and pull requests for a GitHub repository."""
    query = {"state": state, "per_page": min(per_page, 100)}
    if labels:
        query["labels"] = labels
    raw = _github_request("GET", f"/repos/{owner}/{repo}/issues", query=query)
    try:
        items = json.loads(raw)
        if isinstance(items, list):
            summary = []
            for item in items:
                entry = {
                    "number": item["number"],
                    "title": item["title"],
                    "state": item["state"],
                    "user": item["user"]["login"],
                    "labels": [l["name"] for l in item.get("labels", [])],
                    "is_pr": "pull_request" in item,
                    "created_at": item["created_at"],
                    "updated_at": item["updated_at"],
                }
                summary.append(entry)
            return json.dumps({"status": "ok", "count": len(summary), "issues": summary})
        return raw
    except (json.JSONDecodeError, KeyError):
        return raw


def github_get_issue(
    owner: str,
    repo: str,
    number: int,
    include_comments: bool = True,
) -> str:
    """Get full details for an issue or pull request, optionally with comments."""
    raw = _github_request("GET", f"/repos/{owner}/{repo}/issues/{number}")
    try:
        issue = json.loads(raw)
        if "error" in issue:
            return raw

        result = {
            "number": issue["number"],
            "title": issue["title"],
            "state": issue["state"],
            "user": issue["user"]["login"],
            "body": issue.get("body", ""),
            "labels": [l["name"] for l in issue.get("labels", [])],
            "assignees": [a["login"] for a in issue.get("assignees", [])],
            "is_pr": "pull_request" in issue,
            "created_at": issue["created_at"],
            "updated_at": issue["updated_at"],
        }

        if include_comments and issue.get("comments", 0) > 0:
            comments_raw = _github_request(
                "GET", f"/repos/{owner}/{repo}/issues/{number}/comments",
                query={"per_page": 50},
            )
            comments = json.loads(comments_raw)
            if isinstance(comments, list):
                result["comments"] = [
                    {
                        "user": c["user"]["login"],
                        "body": c["body"][:500],
                        "created_at": c["created_at"],
                    }
                    for c in comments
                ]

        return json.dumps({"status": "ok", **result})
    except (json.JSONDecodeError, KeyError):
        return raw


def github_create_issue(
    owner: str,
    repo: str,
    title: str,
    body: str = "",
    labels: str = "",
    assignees: str = "",
) -> str:
    """Create a new GitHub issue."""
    payload: dict = {"title": title}
    if body:
        payload["body"] = body
    if labels:
        payload["labels"] = [l.strip() for l in labels.split(",")]
    if assignees:
        payload["assignees"] = [a.strip() for a in assignees.split(",")]

    raw = _github_request("POST", f"/repos/{owner}/{repo}/issues", body=payload)
    try:
        issue = json.loads(raw)
        if "error" in issue:
            return raw
        return json.dumps({
            "status": "ok",
            "number": issue["number"],
            "url": issue["html_url"],
            "title": issue["title"],
        })
    except (json.JSONDecodeError, KeyError):
        return raw


def github_create_pr(
    owner: str,
    repo: str,
    title: str,
    head: str,
    base: str = "main",
    body: str = "",
    draft: bool = False,
) -> str:
    """Create a pull request."""
    payload = {
        "title": title,
        "head": head,
        "base": base,
        "body": body,
        "draft": draft,
    }
    raw = _github_request("POST", f"/repos/{owner}/{repo}/pulls", body=payload)
    try:
        pr = json.loads(raw)
        if "error" in pr:
            return raw
        return json.dumps({
            "status": "ok",
            "number": pr["number"],
            "url": pr["html_url"],
            "title": pr["title"],
            "state": pr["state"],
        })
    except (json.JSONDecodeError, KeyError):
        return raw


def github_pr_review(
    owner: str,
    repo: str,
    number: int,
    body: str,
    event: str = "COMMENT",
) -> str:
    """Submit a review on a pull request. Event: APPROVE, REQUEST_CHANGES, COMMENT."""
    payload = {"body": body, "event": event.upper()}
    raw = _github_request(
        "POST", f"/repos/{owner}/{repo}/pulls/{number}/reviews", body=payload,
    )
    try:
        review = json.loads(raw)
        if "error" in review:
            return raw
        return json.dumps({
            "status": "ok",
            "review_id": review["id"],
            "state": review["state"],
        })
    except (json.JSONDecodeError, KeyError):
        return raw


def github_ci_status(
    owner: str,
    repo: str,
    ref: str = "HEAD",
) -> str:
    """Get CI / check run status for a git ref (branch, tag, or SHA)."""
    raw = _github_request(
        "GET", f"/repos/{owner}/{repo}/commits/{ref}/check-runs",
        query={"per_page": 50},
    )
    try:
        data = json.loads(raw)
        if "error" in data:
            return raw
        runs = data.get("check_runs", [])
        summary = []
        for run in runs:
            summary.append({
                "name": run["name"],
                "status": run["status"],
                "conclusion": run.get("conclusion"),
                "started_at": run.get("started_at"),
                "completed_at": run.get("completed_at"),
            })
        all_passed = all(r.get("conclusion") == "success" for r in summary if r["status"] == "completed")
        any_running = any(r["status"] in ("queued", "in_progress") for r in summary)
        return json.dumps({
            "status": "ok",
            "ref": ref,
            "total": len(summary),
            "all_passed": all_passed,
            "any_running": any_running,
            "check_runs": summary,
        })
    except (json.JSONDecodeError, KeyError):
        return raw


def github_list_repos(
    owner: str,
    type: str = "owner",
    sort: str = "updated",
    per_page: int = 20,
) -> str:
    """List repositories for a user or organization."""
    # Try as user first, fall back to org
    raw = _github_request(
        "GET", f"/users/{owner}/repos",
        query={"type": type, "sort": sort, "per_page": min(per_page, 100)},
    )
    try:
        repos = json.loads(raw)
        if isinstance(repos, list):
            summary = []
            for r in repos:
                summary.append({
                    "name": r["name"],
                    "full_name": r["full_name"],
                    "description": r.get("description", ""),
                    "language": r.get("language"),
                    "stars": r["stargazers_count"],
                    "forks": r["forks_count"],
                    "updated_at": r["updated_at"],
                    "private": r["private"],
                })
            return json.dumps({"status": "ok", "count": len(summary), "repos": summary})
        return raw
    except (json.JSONDecodeError, KeyError):
        return raw


def github_search_code(
    query: str,
    per_page: int = 10,
) -> str:
    """Search code across GitHub repositories."""
    raw = _github_request(
        "GET", "/search/code",
        query={"q": query, "per_page": min(per_page, 100)},
    )
    try:
        data = json.loads(raw)
        if "error" in data:
            return raw
        items = data.get("items", [])
        summary = []
        for item in items:
            summary.append({
                "name": item["name"],
                "path": item["path"],
                "repo": item["repository"]["full_name"],
                "url": item["html_url"],
                "score": item.get("score"),
            })
        return json.dumps({
            "status": "ok",
            "total_count": data.get("total_count", 0),
            "results": summary,
        })
    except (json.JSONDecodeError, KeyError):
        return raw


# ── Registration ─────────────────────────────────────────────────────────

def register(registry: ToolRegistry):
    """Register all GitHub tools with the Forge tool registry."""

    registry.register(
        name="github_list_issues",
        description=(
            "List issues and pull requests for a GitHub repository. "
            "Filter by state (open/closed/all) and labels."
        ),
        parameters={
            "type": "object",
            "properties": {
                "owner": {"type": "string", "description": "Repository owner (user or org)"},
                "repo": {"type": "string", "description": "Repository name"},
                "state": {"type": "string", "enum": ["open", "closed", "all"], "default": "open"},
                "labels": {"type": "string", "description": "Comma-separated label names to filter by"},
                "per_page": {"type": "integer", "default": 20, "description": "Results per page (max 100)"},
            },
            "required": ["owner", "repo"],
        },
        handler=github_list_issues,
    )

    registry.register(
        name="github_get_issue",
        description=(
            "Get full details for a GitHub issue or pull request, "
            "including body text, labels, assignees, and comments."
        ),
        parameters={
            "type": "object",
            "properties": {
                "owner": {"type": "string", "description": "Repository owner"},
                "repo": {"type": "string", "description": "Repository name"},
                "number": {"type": "integer", "description": "Issue or PR number"},
                "include_comments": {"type": "boolean", "default": True},
            },
            "required": ["owner", "repo", "number"],
        },
        handler=github_get_issue,
    )

    registry.register(
        name="github_create_issue",
        description="Create a new issue on a GitHub repository.",
        parameters={
            "type": "object",
            "properties": {
                "owner": {"type": "string", "description": "Repository owner"},
                "repo": {"type": "string", "description": "Repository name"},
                "title": {"type": "string", "description": "Issue title"},
                "body": {"type": "string", "description": "Issue body (Markdown)"},
                "labels": {"type": "string", "description": "Comma-separated label names"},
                "assignees": {"type": "string", "description": "Comma-separated GitHub usernames"},
            },
            "required": ["owner", "repo", "title"],
        },
        handler=github_create_issue,
    )

    registry.register(
        name="github_create_pr",
        description=(
            "Create a pull request on a GitHub repository. "
            "Requires head branch to be pushed to the remote."
        ),
        parameters={
            "type": "object",
            "properties": {
                "owner": {"type": "string", "description": "Repository owner"},
                "repo": {"type": "string", "description": "Repository name"},
                "title": {"type": "string", "description": "PR title"},
                "head": {"type": "string", "description": "Branch containing changes"},
                "base": {"type": "string", "default": "main", "description": "Branch to merge into"},
                "body": {"type": "string", "description": "PR description (Markdown)"},
                "draft": {"type": "boolean", "default": False},
            },
            "required": ["owner", "repo", "title", "head"],
        },
        handler=github_create_pr,
    )

    registry.register(
        name="github_pr_review",
        description="Submit a review on a GitHub pull request (approve, request changes, or comment).",
        parameters={
            "type": "object",
            "properties": {
                "owner": {"type": "string", "description": "Repository owner"},
                "repo": {"type": "string", "description": "Repository name"},
                "number": {"type": "integer", "description": "PR number"},
                "body": {"type": "string", "description": "Review comment body"},
                "event": {
                    "type": "string",
                    "enum": ["APPROVE", "REQUEST_CHANGES", "COMMENT"],
                    "default": "COMMENT",
                },
            },
            "required": ["owner", "repo", "number", "body"],
        },
        handler=github_pr_review,
    )

    registry.register(
        name="github_ci_status",
        description=(
            "Get CI / check run status for a git ref (branch, tag, or SHA). "
            "Shows all check runs with their status and conclusion."
        ),
        parameters={
            "type": "object",
            "properties": {
                "owner": {"type": "string", "description": "Repository owner"},
                "repo": {"type": "string", "description": "Repository name"},
                "ref": {"type": "string", "default": "HEAD", "description": "Git ref (branch, tag, or SHA)"},
            },
            "required": ["owner", "repo"],
        },
        handler=github_ci_status,
    )

    registry.register(
        name="github_list_repos",
        description="List repositories for a GitHub user or organization.",
        parameters={
            "type": "object",
            "properties": {
                "owner": {"type": "string", "description": "GitHub username or org name"},
                "type": {"type": "string", "enum": ["all", "owner", "member"], "default": "owner"},
                "sort": {"type": "string", "enum": ["created", "updated", "pushed", "full_name"], "default": "updated"},
                "per_page": {"type": "integer", "default": 20},
            },
            "required": ["owner"],
        },
        handler=github_list_repos,
    )

    registry.register(
        name="github_search_code",
        description=(
            "Search code across GitHub repositories. Uses GitHub's code search API. "
            "Query syntax: 'keyword repo:owner/repo language:python' etc."
        ),
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "GitHub code search query"},
                "per_page": {"type": "integer", "default": 10},
            },
            "required": ["query"],
        },
        handler=github_search_code,
    )
