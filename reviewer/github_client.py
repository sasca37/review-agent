"""GitHub 커밋 목록 / 커밋별 diff 가져오기 (REST API). 토큰은 선택이며 로그·응답에 남기지 않는다."""
from __future__ import annotations

import os
import re
from datetime import datetime
from typing import Dict, List, Optional, Tuple

import requests

API = os.getenv("GITHUB_API_URL", "https://api.github.com").rstrip("/")
PER_PAGE = 20
MAX_DIFF_BYTES = 2_000_000
_NAME = re.compile(r"^(?!\.+$)[A-Za-z0-9_.-]+$")  # ".", ".." 같은 경로 이름 금지
_SHA = re.compile(r"^[0-9a-fA-F]{7,40}$")


class GithubError(Exception):
    """화면에 그대로 보여줘도 되는 사용자용 오류 메시지."""


def parse_repo(text: str) -> Tuple[str, str]:
    """'owner/repo' 또는 github.com URL(.git, /tree/... 포함)에서 (owner, repo) 추출."""
    t = (text or "").strip()
    t = re.sub(r"^https?://[^/]+/", "", t)
    t = re.sub(r"^git@[^:]+:", "", t)
    parts = [p for p in t.split("/") if p]
    if len(parts) < 2:
        raise GithubError("저장소는 'owner/repo' 형식이나 GitHub 주소로 입력하세요. 예) pallets/flask")
    owner, repo = parts[0], re.sub(r"\.git$", "", parts[1])
    if not (_NAME.match(owner) and _NAME.match(repo)):
        raise GithubError("저장소 이름에 사용할 수 없는 문자가 있습니다.")
    return owner, repo


def _headers(token: str, accept: str) -> Dict[str, str]:
    h = {"Accept": accept, "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "review-agent"}
    if token:
        h["Authorization"] = "Bearer " + token
    return h


def _get(url: str, token: str, accept: str, params: Optional[dict] = None) -> requests.Response:
    try:
        resp = requests.get(url, headers=_headers(token, accept), params=params, timeout=(5, 30),
                            allow_redirects=False)
    except requests.exceptions.Timeout:
        raise GithubError("GitHub 응답이 늦습니다(시간 초과). 네트워크를 확인하세요.")
    except requests.exceptions.RequestException:
        raise GithubError("GitHub 에 연결할 수 없습니다. 외부망 연결을 확인하세요. (폐쇄망에서는 사용할 수 없습니다)")
    if resp.status_code == 200:
        return resp
    if resp.status_code in (403, 429) and resp.headers.get("X-RateLimit-Remaining") == "0":
        try:
            when = datetime.fromtimestamp(int(resp.headers.get("X-RateLimit-Reset", "0"))).strftime("%H:%M")
        except (ValueError, OSError):
            when = "잠시"
        raise GithubError("GitHub 호출 한도를 모두 썼습니다. %s 이후 다시 시도하거나, 설정에서 GitHub 토큰을 입력하세요." % when)
    msg = {
        401: "GitHub 토큰이 올바르지 않습니다. 설정에서 확인하세요.",
        403: "접근이 거부되었습니다. 토큰 권한을 확인하세요.",
        404: "저장소 또는 커밋을 찾을 수 없습니다. 이름을 확인하세요. 비공개 저장소는 설정에서 토큰이 필요합니다.",
        406: "변경분이 너무 커서 GitHub 가 diff 를 제공하지 않습니다. 더 작은 커밋을 고르세요.",
        422: "요청을 처리할 수 없습니다. 브랜치 이름 또는 커밋 값을 확인하세요.",
    }.get(resp.status_code, "GitHub 요청이 실패했습니다.")
    raise GithubError("%s (HTTP %d)" % (msg, resp.status_code))


def list_commits(repo: str, branch: str = "", token: str = "", page: int = 1) -> Dict:
    owner, name = parse_repo(repo)
    params = {"per_page": PER_PAGE, "page": max(1, int(page))}
    if branch.strip():
        params["sha"] = branch.strip()
    resp = _get("%s/repos/%s/%s/commits" % (API, owner, name), token, "application/vnd.github+json", params)
    try:
        items = resp.json()
    except ValueError:
        raise GithubError("GitHub 응답을 해석하지 못했습니다.")
    commits: List[Dict] = []
    for c in items:
        info = c.get("commit", {})
        commits.append({
            "sha": c.get("sha", ""), "short": c.get("sha", "")[:7],
            "message": (info.get("message") or "").splitlines()[0][:120] if info.get("message") else "",
            "author": (info.get("author") or {}).get("name", ""),
            "date": ((info.get("author") or {}).get("date") or "")[:10]})
    return {"repo": "%s/%s" % (owner, name), "commits": commits, "has_more": len(items) >= PER_PAGE}


def get_commit_diff(repo: str, sha: str, token: str = "") -> str:
    owner, name = parse_repo(repo)
    if not _SHA.match(sha or ""):
        raise GithubError("커밋 값이 올바르지 않습니다.")
    resp = _get("%s/repos/%s/%s/commits/%s" % (API, owner, name, sha), token, "application/vnd.github.diff")
    if len(resp.content) > MAX_DIFF_BYTES:
        raise GithubError("diff 가 너무 큽니다 (최대 2MB). 더 작은 커밋을 고르세요.")
    return resp.text


MAX_COMMITS = 20


def get_commits_diff(repo: str, shas: List[str], token: str = "") -> str:
    """선택한 커밋들의 diff 를 주어진 순서(오래된 것 → 최신)대로 이어 붙인다."""
    shas = [x for x in (shas or []) if x]
    if not shas:
        raise GithubError("커밋을 선택하세요.")
    if len(shas) > MAX_COMMITS:
        raise GithubError("한 번에 최대 %d개 커밋까지 선택할 수 있습니다." % MAX_COMMITS)
    parts, total = [], 0
    for sha in dict.fromkeys(shas):  # 중복 제거, 순서 유지
        try:
            d = get_commit_diff(repo, sha, token)
        except GithubError as e:
            raise GithubError("커밋 %s: %s" % (str(sha)[:7], e))
        total += len(d.encode("utf-8"))
        if total > MAX_DIFF_BYTES:
            raise GithubError("선택한 커밋의 diff 합계가 너무 큽니다 (최대 2MB). 커밋을 줄여 주세요.")
        parts.append(d if d.endswith("\n") else d + "\n")
    return "".join(parts)
