"""샘플 사례로 '심어 둔 오류를 놓친 수 / 없는 오류를 지어낸 수'를 센다.

사용: python -m evals.run_eval [samples/sample1]
LLM_BASE_URL/LLM_MODEL 이 설정돼 있으면 LLM 점검도 포함된다.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile

from reviewer.llm import LLMReviewer
from reviewer.service import review

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _git(cwd, *a):
    subprocess.run(["git", "-C", cwd, "-c", "user.name=t", "-c", "user.email=t@t", *a],
                   check=True, capture_output=True)


def build_repo(sample: str, dest: str):
    shutil.copytree(os.path.join(sample, "before"), dest, dirs_exist_ok=True)
    _git(dest, "init", "-q")
    _git(dest, "add", "-A")
    _git(dest, "commit", "-q", "-m", "base")
    shutil.copytree(os.path.join(sample, "after"), dest, dirs_exist_ok=True)  # 작업 트리 변경


def hit(p: dict, f) -> bool:
    if p["type"] != f.type or p["file"] != f.file:
        return False
    return p.get("line") is None or f.line is None or abs(p["line"] - f.line) <= 2


def evaluate(sample: str, llm=None) -> dict:
    exp = json.load(open(os.path.join(sample, "expected.json"), encoding="utf-8"))
    tmp = tempfile.mkdtemp()
    try:
        build_repo(sample, tmp)
        diff = subprocess.run(["git", "-C", tmp, "diff", "HEAD", "--", *exp["diff_paths"]],
                              capture_output=True, text=True, check=True).stdout
        result = review(diff, tmp, llm)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    planted = exp["planted"]
    used, caught, missed = set(), [], []
    for p in planted:  # 지적 1건은 정답 1개에만 대응 (가까운 줄의 다른 지적이 대신 인정되는 것 방지)
        idx = next((i for i, f in enumerate(result.findings) if i not in used and hit(p, f)), None)
        if idx is None:
            missed.append(p)
        else:
            used.add(idx)
            caught.append(p)
    false_pos = [f for i, f in enumerate(result.findings) if i not in used]
    return {"planted": len(planted), "caught": [p["id"] for p in caught],
            "missed": [p["id"] for p in missed], "false_positives": [f.to_dict() for f in false_pos],
            "notes": result.notes}


if __name__ == "__main__":
    sample = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "samples", "sample1")
    print(json.dumps(evaluate(sample, LLMReviewer.from_env()), ensure_ascii=False, indent=2))
