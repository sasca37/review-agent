import importlib.util
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from evals.run_eval import ROOT
from web.app import create_app

DIFF = open(os.path.join(ROOT, "samples", "diffs", "sample4.diff"), encoding="utf-8").read()
STATUS = open(os.path.join(ROOT, "samples", "diffs", "sample4.git-status.txt"), encoding="utf-8").read()
KEY = "app-secret-123"


def _load(name):
    spec = importlib.util.spec_from_file_location(name, os.path.join(ROOT, "dify", "nodes", name + ".py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


class FakeDify(BaseHTTPRequestHandler):
    """Dify Workflow API 흉내. 실제 Code 노드 코드를 실행하고 LLM 응답만 고정한다."""
    seen = {}

    def log_message(self, *a):
        pass

    def _send(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _auth(self):
        if self.headers.get("Authorization") != "Bearer " + KEY:
            self._send(401, {"code": "unauthorized", "message": "Access token is invalid"})
            return False
        return True

    def do_GET(self):
        if self.path == "/v1/parameters" and self._auth():
            self._send(200, {"user_input_form": [{"select": {"variable": "task"}},
                                                 {"paragraph": {"variable": "diff_text"}},
                                                 {"paragraph": {"variable": "git_status"}}]})
        elif self.path == "/v1/old-app/v1/parameters" and self._auth():  # 업무명 입력이 없는 구버전 워크플로
            self._send(200, {"user_input_form": [{"paragraph": {"variable": "diff_text"}}]})
        elif self.path == "/v1/other-app/v1/parameters" and self._auth():
            self._send(200, {"user_input_form": [{"text-input": {"variable": "question"}}]})
        elif not self.path.startswith("/v1"):
            self._send(404, {"message": "not found"})

    def do_POST(self):
        if self.path != "/v1/workflows/run" or not self._auth():
            return
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        FakeDify.seen = body
        p, m = _load("parse_diff"), _load("merge_findings")
        out = p.main(body["inputs"]["diff_text"], body["inputs"].get("git_status", ""))
        llm = json.dumps([{"type": "typo", "file": "FeeCalculator.java", "line": 8,
                           "evidence": "실패했읍니다", "reason": "'했습니다' 의 오타", "needs_check": False}])
        res = m.main(llm, out["rule_findings_json"], out["files_json"], out["notice"])
        self._send(200, {"data": {"status": "succeeded", "outputs": res, "elapsed_time": 0.1}})


@pytest.fixture(scope="module")
def dify_url():
    srv = HTTPServer(("127.0.0.1", 0), FakeDify)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield "http://127.0.0.1:%d" % srv.server_port  # /v1 없이 입력해도 동작해야 함
    srv.shutdown()


TASK = "민원 서비스"


@pytest.fixture
def client(tmp_path):
    return create_app(llm=None, db_path=str(tmp_path / "h.db")).test_client()


def test_index_and_samples(client):
    html = client.get("/").get_data(as_text=True)
    assert "API URL" in html and "API Key" in html and "AI 코드 리뷰 시작" in html
    names = [s["name"] for s in client.get("/api/samples").json]
    assert "sample4" in names
    assert "diff --git" in client.get("/api/samples/sample4").json["diff"]
    assert client.get("/api/samples/..%2Fetc").status_code == 404


def test_connection_ok_and_errors(client, dify_url):
    ok = client.post("/api/dify/test", json={"dify_url": dify_url, "dify_key": KEY}).json
    assert ok["ok"] and ok["warning"] is None and "diff_text" in ok["inputs"]
    bad = client.post("/api/dify/test", json={"dify_url": dify_url, "dify_key": "wrong"}).json
    assert not bad["ok"] and "API Key" in bad["error"]
    assert "wrong" not in json.dumps(bad)  # 키가 응답에 되돌아가면 안 됨
    wrong_app = client.post("/api/dify/test", json={"dify_url": dify_url + "/v1/other-app", "dify_key": KEY}).json
    assert wrong_app["ok"] and "diff_text" in wrong_app["warning"]
    old = client.post("/api/dify/test", json={"dify_url": dify_url + "/v1/old-app", "dify_key": KEY}).json
    assert old["ok"] and "task" in old["warning"]
    assert not client.post("/api/dify/test", json={"dify_url": "ftp://x", "dify_key": KEY}).json["ok"]
    assert not client.post("/api/dify/test", json={"dify_url": "http://127.0.0.1:9", "dify_key": KEY}).json["ok"]


def test_review_via_dify_end_to_end(client, dify_url):
    r = client.post("/api/review", json={"task": TASK, "engine": "dify", "diff": DIFF, "git_status": STATUS,
                                         "dify_url": dify_url, "dify_key": KEY})
    assert r.status_code == 200, r.json
    d = r.json
    assert FakeDify.seen["inputs"]["diff_text"] == DIFF and FakeDify.seen["response_mode"] == "blocking"
    assert FakeDify.seen["inputs"]["task"] == TASK  # 업무명이 워크플로 입력으로 전달됨
    kinds = {(f["type"], f["file"]) for f in d["findings"]}
    assert ("missing_file", "TransferController.java") in kinds and ("typo", "FeeCalculator.java") in kinds
    assert all(f["type_label"] for f in d["findings"])
    assert [f["path"] for f in d["files"]] == ["FeeCalculator.java", "FeeMapper.java"]
    assert KEY not in json.dumps(d)


def test_review_errors(client, dify_url):
    assert client.post("/api/review", json={"task": TASK, "diff": ""}).status_code == 400
    r = client.post("/api/review", json={"task": TASK, "engine": "dify", "diff": DIFF, "dify_url": dify_url, "dify_key": "bad"})
    assert r.status_code == 400 and "API Key" in r.json["error"]
    r = client.post("/api/review", json={"task": TASK, "engine": "dify", "diff": "그냥 텍스트", "dify_url": dify_url, "dify_key": KEY})
    assert r.status_code == 400 and "변경 파일" in r.json["error"]


def test_server_default_credentials(client, dify_url, monkeypatch):
    monkeypatch.setenv("DIFY_API_URL", dify_url)
    monkeypatch.setenv("DIFY_API_KEY", KEY)
    assert client.get("/api/config").json["server_key"] is True
    assert client.post("/api/review", json={"task": TASK, "engine": "dify", "diff": DIFF}).status_code == 200


def test_local_engine(client):
    r = client.post("/api/review", json={"task": TASK, "engine": "local", "diff": DIFF, "git_status": STATUS})
    assert r.status_code == 200 and any(f["type"] == "typo" for f in r.json["findings"])
    missing = {f["file"] for f in r.json["findings"] if f["type"] == "missing_file"}
    assert missing == {"TransferController.java", "FeeMapper.xml"}  # 저장소 없이 git status 텍스트만으로 점검


def test_allowed_hosts(client, dify_url, monkeypatch):
    monkeypatch.setenv("DIFY_ALLOWED_HOSTS", "dify.internal")
    assert not client.post("/api/dify/test", json={"dify_url": dify_url, "dify_key": KEY}).json["ok"]


def test_task_required_and_validated(client):
    assert client.get("/api/config").json["tasks"] == ["설문조사 서비스", "민원 서비스", "업무상담 서비스"]
    for bad in (None, "", "없는 업무"):
        r = client.post("/api/review", json={"task": bad, "engine": "local", "diff": DIFF})
        assert r.status_code == 400 and "업무" in r.json["error"]
    assert client.get("/api/history?task=없는업무").status_code == 400


def test_history_is_per_task_and_reopenable(client, dify_url):
    def run(task):
        return client.post("/api/review", json={"task": task, "engine": "dify", "diff": DIFF, "git_status": STATUS,
                                                "dify_url": dify_url, "dify_key": KEY})
    first = run("민원 서비스").json
    run("민원 서비스")
    run("설문조사 서비스")
    assert len(client.get("/api/history?task=민원 서비스").json) == 2
    assert len(client.get("/api/history?task=설문조사 서비스").json) == 1
    assert client.get("/api/history?task=업무상담 서비스").json == []
    item = client.get("/api/history?task=민원 서비스").json[0]
    assert item["found"] >= 1 and item["files"][0] == "FeeCalculator.java"
    rec = client.get("/api/history/%d" % first["history_id"]).json
    assert rec["diff"] == DIFF and rec["git_status"] == STATUS and rec["task"] == "민원 서비스"
    assert rec["result"]["findings"] == first["findings"]
    assert KEY not in json.dumps(rec)  # API Key 는 기록에 저장되지 않음
    assert client.delete("/api/history/%d" % first["history_id"]).status_code == 200
    assert client.get("/api/history/%d" % first["history_id"]).status_code == 404
    assert len(client.get("/api/history?task=민원 서비스").json) == 1


def test_failed_review_is_not_saved(client, dify_url):
    client.post("/api/review", json={"task": TASK, "engine": "dify", "diff": DIFF, "dify_url": dify_url, "dify_key": "bad"})
    assert client.get("/api/history?task=" + TASK).json == []


def test_views_present(client):
    html = client.get("/").get_data(as_text=True)
    assert 'id="view-settings"' in html and 'id="taskSel"' in html and "최근 리뷰" in html
    home = html.split('id="view-settings"')[0]
    assert 'id="url"' not in home and 'id="key"' not in home  # API URL/Key 입력은 홈에 없음


def test_opinions_flow_through_and_history_counts_them(client, dify_url, monkeypatch):
    from reviewer import dify_client
    canned = {"findings": [
        {"type": "logic", "file": "Foo.java", "line": 1, "evidence": "x", "reason": "r", "status": "지적", "source": "llm"},
        {"type": "opinion", "category": "성능", "priority": "상", "file": "Foo.java", "line": None, "evidence": "",
         "reason": "N+1 우려", "status": "의견", "source": "llm"},
        {"type": "opinion", "category": "전반", "priority": "중", "file": "", "line": None, "evidence": "",
         "reason": "총평", "status": "의견", "source": "llm"}], "notes": [], "elapsed": 0.1}
    monkeypatch.setattr(dify_client, "run_review", lambda *a, **k: canned)
    r = client.post("/api/review", json={"task": TASK, "engine": "dify", "diff": DIFF, "dify_url": dify_url, "dify_key": KEY})
    assert r.status_code == 200
    assert [f["type_label"] for f in r.json["findings"]] == ["로직 오류", "의견", "의견"]
    item = client.get("/api/history?task=" + TASK).json[0]
    assert (item["found"], item["check"], item["opinion"]) == (1, 0, 2)   # 의견이 '확인 필요'로 세어지면 안 됨


def test_ui_renders_opinion_section(client):
    html = client.get("/").get_data(as_text=True)
    assert "리뷰 의견" in html and 'pill o' in html and 'fbadge o' in html and "ln.opn" in html
