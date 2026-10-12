import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

import pytest

from reviewer import github_client
from reviewer.github_client import GithubError, parse_repo
from web.app import create_app

SHA = "a" * 40
DIFF = ("diff --git a/Foo.java b/Foo.java\nindex 1..2 100644\n--- a/Foo.java\n+++ b/Foo.java\n"
        "@@ -1,2 +1,2 @@\n class Foo {\n-    int a;\n+    int b;\n")


class FakeGithub(BaseHTTPRequestHandler):
    seen = {}

    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="application/json", headers=None):
        raw = body.encode() if isinstance(body, str) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(raw)))
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        u = urlparse(self.path)
        FakeGithub.seen = {"auth": self.headers.get("Authorization"), "accept": self.headers.get("Accept"),
                           "query": parse_qs(u.query), "path": u.path}
        parts = u.path.strip("/").split("/")  # repos/owner/repo/commits[/sha]
        owner, repo = parts[1], parts[2]
        if repo == "limited":
            return self._send(403, {"message": "rate limit"}, headers={"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "1900000000"})
        if repo == "private" and not self.headers.get("Authorization"):
            return self._send(404, {"message": "Not Found"})
        if repo == "huge":
            return self._send(406, {"message": "diff too large"})
        if repo not in ("demo", "private"):
            return self._send(404, {"message": "Not Found"})
        if len(parts) == 4:
            page = int(parse_qs(u.query).get("page", ["1"])[0])
            n = 20 if page == 1 else 3
            return self._send(200, [{"sha": ("%040x" % (page * 100 + i)), "commit": {
                "message": "메시지 %d\n\n본문" % i, "author": {"name": "홍길동", "date": "2026-10-0%dT10:00:00Z" % (i % 9 + 1)}}}
                for i in range(n)])
        self._send(200, DIFF.replace("int b;", "int v%s;" % parts[4][-3:]), ctype="text/plain")


@pytest.fixture(scope="module")
def gh(monkeypatch_module=None):
    srv = HTTPServer(("127.0.0.1", 0), FakeGithub)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    old = github_client.API
    github_client.API = "http://127.0.0.1:%d" % srv.server_port
    yield
    github_client.API = old
    srv.shutdown()


@pytest.fixture
def client(tmp_path):
    return create_app(llm=None, db_path=str(tmp_path / "h.db")).test_client()


def test_parse_repo():
    assert parse_repo("pallets/flask") == ("pallets", "flask")
    assert parse_repo("https://github.com/pallets/flask.git") == ("pallets", "flask")
    assert parse_repo("https://github.com/pallets/flask/tree/main/src") == ("pallets", "flask")
    assert parse_repo("git@github.com:a/b.git") == ("a", "b")
    for bad in ("", "flask", "a/b c", "../etc", "a/..", "./x"):
        with pytest.raises(GithubError):
            parse_repo(bad)


def test_list_and_diff(gh, client):
    r = client.post("/api/github/commits", json={"repo": "https://github.com/acme/demo", "branch": "dev"})
    d = r.json
    assert r.status_code == 200 and d["repo"] == "acme/demo" and len(d["commits"]) == 20 and d["has_more"]
    assert d["commits"][0]["message"] == "메시지 0" and d["commits"][0]["author"] == "홍길동"  # 첫 줄만
    assert FakeGithub.seen["query"]["sha"] == ["dev"] and FakeGithub.seen["auth"] is None
    page2 = client.post("/api/github/commits", json={"repo": "acme/demo", "page": 2}).json
    assert len(page2["commits"]) == 3 and page2["has_more"] is False
    out = client.post("/api/github/diff", json={"repo": "acme/demo", "sha": d["commits"][0]["sha"]}).json
    assert out["diff"] == DIFF.replace("int b;", "int v%s;" % d["commits"][0]["sha"][-3:]) and out["git_status"] == ""
    assert FakeGithub.seen["accept"] == "application/vnd.github.diff"


def test_token_forwarded_and_never_echoed(gh, client, monkeypatch):
    r = client.post("/api/github/commits", json={"repo": "acme/private", "github_token": "ghp_SECRET"})
    assert r.status_code == 200 and FakeGithub.seen["auth"] == "Bearer ghp_SECRET"
    assert "ghp_SECRET" not in r.get_data(as_text=True)
    assert client.post("/api/github/commits", json={"repo": "acme/private"}).status_code == 400  # 토큰 없으면 404
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_ENV")
    assert client.post("/api/github/commits", json={"repo": "acme/private"}).status_code == 200
    assert FakeGithub.seen["auth"] == "Bearer ghp_ENV"


def test_errors_are_friendly(gh, client):
    def err(repo, **kw):
        r = client.post("/api/github/commits", json=dict(repo=repo, **kw))
        assert r.status_code == 400
        return r.json["error"]
    assert "찾을 수 없습니다" in err("acme/missing")
    assert "호출 한도" in err("acme/limited") and "토큰" in err("acme/limited")
    assert "owner/repo" in err("flask")
    r = client.post("/api/github/diff", json={"repo": "acme/huge", "sha": SHA})
    assert "너무 커서" in r.json["error"]
    assert client.post("/api/github/diff", json={"repo": "acme/demo", "sha": "zzzz"}).status_code == 400
    assert client.post("/api/github/diff", json={"repo": "acme/demo", "sha": "../../x"}).status_code == 400


def test_commit_diff_flows_into_review(gh, client):
    diff = client.post("/api/github/diff", json={"repo": "acme/demo", "sha": SHA}).json["diff"]
    r = client.post("/api/review", json={"task": "민원 서비스", "engine": "local", "diff": diff})
    assert r.status_code == 200 and [f["path"] for f in r.json["files"]] == ["Foo.java"]


def test_multi_commit_diff_is_combined_in_order(gh, client):
    shas = ["%040x" % n for n in (0x101, 0x102, 0x103)]
    r = client.post("/api/github/diff", json={"repo": "acme/demo", "shas": shas})
    d = r.json
    assert r.status_code == 200 and d["count"] == 3 and d["git_status"] == ""
    assert [m for m in ("v101", "v102", "v103") if m in d["diff"]] == ["v101", "v102", "v103"]
    assert d["diff"].index("v101") < d["diff"].index("v102") < d["diff"].index("v103")  # 주어진 순서 유지
    assert d["diff"].count("diff --git") == 3
    dup = client.post("/api/github/diff", json={"repo": "acme/demo", "shas": [shas[0], shas[0]]}).json
    assert dup["count"] == 1 and dup["diff"].count("diff --git") == 1  # 중복 제거
    # 단일 sha 형식(이전 방식)도 계속 동작
    assert client.post("/api/github/diff", json={"repo": "acme/demo", "sha": shas[0]}).status_code == 200


def test_multi_commit_limits_and_errors(gh, client):
    many = ["%040x" % (0x200 + i) for i in range(21)]
    r = client.post("/api/github/diff", json={"repo": "acme/demo", "shas": many})
    assert r.status_code == 400 and "20개" in r.json["error"]
    r = client.post("/api/github/diff", json={"repo": "acme/demo", "shas": []})
    assert r.status_code == 400 and "선택" in r.json["error"]
    r = client.post("/api/github/diff", json={"repo": "acme/huge", "shas": [SHA, "b" * 40]})
    assert r.status_code == 400 and "aaaaaaa" in r.json["error"]  # 어느 커밋에서 실패했는지 표시
    assert client.post("/api/github/diff", json={"repo": "acme/demo", "shas": "notalist"}).status_code == 400


def test_review_merges_same_file_rows(gh, client):
    shas = ["%040x" % n for n in (0x101, 0x102)]
    diff = client.post("/api/github/diff", json={"repo": "acme/demo", "shas": shas}).json["diff"]
    r = client.post("/api/review", json={"task": "민원 서비스", "engine": "local", "diff": diff})
    files = r.json["files"]
    assert r.status_code == 200 and len(files) == 1 and files[0]["path"] == "Foo.java"
    assert (files[0]["added"], files[0]["removed"]) == (2, 2)  # 두 커밋의 +1/-1 이 합산됨


def test_ui_has_github_tab(client):
    html = client.get("/").get_data(as_text=True)
    assert 'data-tab="github"' in html and 'id="ghList"' in html and 'id="ghToken"' in html
    assert 'id="ghSelBar"' in html and 'id="ghLoad"' in html
    assert 'id="ghToken"' not in html.split('id="view-settings"')[0]  # 토큰 입력은 설정 탭에만
