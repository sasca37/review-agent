"""Dify 워크플로로 샘플(심어 둔 오류)을 점검하고 점수를 낸다. 모델·프롬프트·지식을 바꿨을 때 전후 비교용.

사용: DIFY_API_URL=http://호스트/v1 DIFY_API_KEY=app-... python -m evals.run_dify_eval [--repeat 3] [--task "민원 서비스"] [2 3 ...]
인자로 샘플 번호를 주면 그 샘플만 돌린다 (기본: 전체).
출력: 샘플별 잡은 오류 / 놓친 오류 / 지어낸 지적, 그리고 LLM 이 '지적 0건'을 돌려준 횟수.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from types import SimpleNamespace

from evals.run_eval import ROOT, hit
from reviewer import dify_client


def score(planted, findings):
    """지적 1건은 정답 1개에만 대응. (잡은 id, 놓친 id, 지어낸 지적) 반환.
    '의견'(type=opinion)은 결함 정답과 무관한 자유 의견이므로 채점에서 제외한다."""
    findings = [f for f in findings if f.get("type") != "opinion"]
    objs = [SimpleNamespace(type=f.get("type"), file=f.get("file"), line=f.get("line"), raw=f) for f in findings]
    used, caught, missed = set(), [], []
    for p in planted:
        idx = next((i for i, f in enumerate(objs) if i not in used and hit(p, f)), None)
        if idx is None:
            missed.append(p["id"])
        else:
            used.add(idx)
            caught.append(p["id"])
    fp = [o.raw for i, o in enumerate(objs) if i not in used]
    return caught, missed, fp


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("samples", nargs="*", help="샘플 번호 (예: 2 3)")
    ap.add_argument("--repeat", type=int, default=1)
    ap.add_argument("--task", default="민원 서비스")
    a = ap.parse_args(argv)
    url, key = os.getenv("DIFY_API_URL", ""), os.getenv("DIFY_API_KEY", "")
    if not url or not key:
        sys.exit("DIFY_API_URL 과 DIFY_API_KEY 환경변수를 설정하세요.")
    names = ["sample%s" % n for n in a.samples] or sorted(
        d for d in os.listdir(os.path.join(ROOT, "samples")) if d.startswith("sample") and d[6:].isdigit())
    tot = {"planted": 0, "caught": 0, "fp": 0, "zero": 0, "runs": 0}
    for name in names:
        exp = json.load(open(os.path.join(ROOT, "samples", name, "expected.json"), encoding="utf-8"))
        base = os.path.join(ROOT, "samples", "diffs", name)
        diff = open(base + ".diff", encoding="utf-8").read()
        status = open(base + ".git-status.txt", encoding="utf-8").read()
        for r in range(a.repeat):
            res = dify_client.run_review(url, key, diff, status, a.task)
            caught, missed, fp = score(exp["planted"], res["findings"])
            zero = any("0건 반환" in n for n in res["notes"])
            n_op = sum(1 for f in res["findings"] if f.get("type") == "opinion")
            tot["planted"] += len(exp["planted"]); tot["caught"] += len(caught)
            tot["fp"] += len(fp); tot["zero"] += zero; tot["runs"] += 1
            print("%-8s #%d 잡음 %s | 놓침 %s | 지어낸 지적 %d%s" % (
                name, r + 1, caught or "-", missed or "-", len(fp), " | LLM 0건 응답" if zero else "") + " | 의견 %d건" % n_op)
            for f in fp:
                print("          └ 정답에 없는 지적: [%s] %s:%s %s" % (f.get("status"), f.get("file"), f.get("line"), f.get("reason", "")[:60]))
    rate = 100.0 * tot["caught"] / tot["planted"] if tot["planted"] else 0
    print("\n요약: 심어 둔 오류 %d개 중 %d개 재현 (%.0f%%) · 지어낸 지적 %d건 · LLM 0건 응답 %d/%d회"
          % (tot["planted"], tot["caught"], rate, tot["fp"], tot["zero"], tot["runs"]))


if __name__ == "__main__":
    main()
