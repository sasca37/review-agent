from __future__ import annotations

import glob
import json
import os
import time

from flask import Flask, jsonify, render_template, request

from reviewer import dify_client, github_client
from reviewer.dify_client import DifyError
from reviewer.github_client import GithubError
from reviewer.diff_parser import parse_diff
from reviewer.gitsource import GitError, get_diff
from reviewer.history import HistoryStore
from reviewer.llm import LLMReviewer
from reviewer.models import TYPE_LABEL
from reviewer.service import review as local_review
from reviewer.tasks import TASKS

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PAIR_RULES = os.path.join(ROOT, "config", "pair_rules.yaml")
SAMPLES = os.path.join(ROOT, "samples")
MAX_DIFF_BYTES = 2_000_000


def _files(diff: str):
    try:
        files = parse_diff(diff)
    except Exception:
        raise ValueError("diff 형식을 해석할 수 없습니다. 'git diff' 결과가 중간에 잘리지 않았는지 확인하세요.")
    if not files:
        raise ValueError("변경 파일을 찾지 못했습니다. 'git diff' 결과(unified diff)를 입력하세요.")
    return files


def merge_files(files):
    """여러 커밋 diff 를 이어 붙이면 같은 파일이 여러 번 나온다. 목록에서는 한 줄로 합산한다."""
    out = {}
    for f in files:
        if f.path in out:
            out[f.path]["added"] += f.added
            out[f.path]["removed"] += f.removed
        else:
            out[f.path] = {"path": f.path, "status": f.status, "added": f.added, "removed": f.removed}
    return list(out.values())


def create_app(llm=None, db_path=None) -> Flask:
    app = Flask(__name__)
    store = HistoryStore(db_path or os.getenv("HISTORY_DB") or os.path.join(ROOT, "data", "history.db"))
    app.config["LLM"] = llm if llm is not None else LLMReviewer.from_env()
    app.config["MAX_CONTENT_LENGTH"] = MAX_DIFF_BYTES * 2

    @app.get("/")
    def index():
        return render_template("index.html")

    @app.get("/api/config")
    def config():
        return jsonify(tasks=TASKS, dify_url=os.getenv("DIFY_API_URL", ""), server_key=bool(os.getenv("DIFY_API_KEY")), github_token=bool(os.getenv("GITHUB_TOKEN")),
                       local_llm=app.config["LLM"] is not None)

    def creds(body):
        url = (body.get("dify_url") or os.getenv("DIFY_API_URL") or "").strip()
        key = (body.get("dify_key") or os.getenv("DIFY_API_KEY") or "").strip()
        return url, key

    @app.post("/api/dify/test")
    def dify_test():
        url, key = creds(request.get_json(silent=True) or {})
        try:
            return jsonify(dify_client.test_connection(url, key))
        except DifyError as e:
            return jsonify(ok=False, error=str(e)), 200

    @app.post("/api/review")
    def api_review():
        body = request.get_json(silent=True) or {}
        diff = body.get("diff") or ""
        status = body.get("git_status") or ""
        engine = body.get("engine") or "dify"
        task = body.get("task")
        if task not in TASKS:
            return jsonify(error="업무를 선택하세요."), 400
        if not diff.strip():
            return jsonify(error="변경 diff 를 입력하세요."), 400
        if len(diff.encode("utf-8")) > MAX_DIFF_BYTES:
            return jsonify(error="diff 가 너무 큽니다 (최대 2MB)."), 400
        t0 = time.time()
        try:
            files = _files(diff)
            if engine == "dify":
                url, key = creds(body)
                res = dify_client.run_review(url, key, diff, status, task)
                findings = res["findings"]
                notes = res["notes"]
            else:
                r = local_review(diff, (body.get("repo") or "").strip() or None, app.config["LLM"], PAIR_RULES, status)
                findings = [f.to_dict() for f in r.findings]
                notes = r.notes
        except (ValueError, DifyError) as e:
            return jsonify(error=str(e)), 400
        for f in findings:
            f["type_label"] = TYPE_LABEL.get(f.get("type"), f.get("type"))
        result = dict(
            engine=engine, elapsed=round(time.time() - t0, 1), notes=notes, findings=findings,
            files=merge_files(files))
        result["history_id"] = store.add(task, engine, diff, status, result)
        return jsonify(result)

    @app.get("/api/history")
    def history_list():
        task = request.args.get("task") or None
        if task is not None and task not in TASKS:
            return jsonify(error="알 수 없는 업무입니다."), 400
        return jsonify(store.list(task))

    @app.get("/api/history/<int:id_>")
    def history_get(id_):
        rec = store.get(id_)
        return (jsonify(rec), 200) if rec else (jsonify(error="기록을 찾을 수 없습니다."), 404)

    @app.delete("/api/history/<int:id_>")
    def history_delete(id_):
        return (jsonify(ok=True), 200) if store.delete(id_) else (jsonify(error="기록을 찾을 수 없습니다."), 404)

    @app.post("/api/diff")
    def api_diff():
        body = request.get_json(silent=True) or {}
        repo = (body.get("repo") or "").strip()
        if not repo or not os.path.isdir(repo):
            return jsonify(error="저장소 경로가 존재하지 않습니다."), 400
        try:
            diff = get_diff(repo, (body.get("base") or "").strip() or None, (body.get("head") or "").strip() or None)
            import subprocess
            status = subprocess.run(["git", "-C", repo, "status", "--porcelain"], capture_output=True,
                                    text=True, timeout=30).stdout
        except (GitError, OSError, Exception) as e:
            return jsonify(error=str(e) or "git 실행에 실패했습니다."), 400
        if not diff.strip():
            return jsonify(error="변경된 내용이 없습니다. base/head 를 확인하세요."), 400
        return jsonify(diff=diff, git_status=status)

    def gh_token(body):
        return (body.get("github_token") or os.getenv("GITHUB_TOKEN") or "").strip()

    @app.post("/api/github/commits")
    def github_commits():
        body = request.get_json(silent=True) or {}
        try:
            return jsonify(github_client.list_commits(body.get("repo"), body.get("branch") or "", gh_token(body),
                                                      body.get("page") or 1))
        except (GithubError, ValueError) as e:
            return jsonify(error=str(e) or "요청이 올바르지 않습니다."), 400

    @app.post("/api/github/diff")
    def github_diff():
        body = request.get_json(silent=True) or {}
        try:
            shas = body.get("shas") or ([body["sha"]] if body.get("sha") else [])
            if not isinstance(shas, list):
                raise GithubError("커밋 값이 올바르지 않습니다.")
            diff = github_client.get_commits_diff(body.get("repo"), shas, gh_token(body))
        except GithubError as e:
            return jsonify(error=str(e)), 400
        if not diff.strip():
            return jsonify(error="선택한 커밋에는 변경된 파일 내용이 없습니다."), 400
        return jsonify(diff=diff, git_status="", count=len(set(shas)))

    @app.get("/api/samples")
    def samples():
        out = []
        for p in sorted(glob.glob(os.path.join(SAMPLES, "diffs", "sample*.diff"))):
            name = os.path.basename(p)[:-5]
            try:
                n = len(json.load(open(os.path.join(SAMPLES, name, "expected.json"), encoding="utf-8"))["planted"])
            except (OSError, KeyError, ValueError):
                n = None
            out.append({"name": name, "planted": n})
        return jsonify(out)

    @app.get("/api/samples/<name>")
    def sample(name):
        import re
        if not re.fullmatch(r"sample\d+", name):
            return jsonify(error="잘못된 샘플 이름"), 404
        base = os.path.join(SAMPLES, "diffs", name)
        try:
            return jsonify(diff=open(base + ".diff", encoding="utf-8").read(),
                           git_status=open(base + ".git-status.txt", encoding="utf-8").read())
        except OSError:
            return jsonify(error="샘플이 없습니다. python -m evals.make_diffs 를 실행하세요."), 404

    return app


if __name__ == "__main__":
    create_app().run(host="127.0.0.1", port=int(os.getenv("PORT", "5050")))
