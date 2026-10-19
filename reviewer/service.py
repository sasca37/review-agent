from __future__ import annotations

import re
from typing import List, Optional

from .diff_parser import parse_diff
from .llm import LLMReviewer
from .models import DiffFile, Finding, ReviewResult, NEEDS_CHECK
from .rules import check_missing_files, check_typos


def review(
    diff_text: str,
    repo_root: Optional[str] = None,
    llm: Optional[LLMReviewer] = None,
    pair_rules_path: Optional[str] = None,
    git_status: Optional[str] = None,
) -> ReviewResult:
    files = parse_diff(diff_text)
    notes: List[str] = []
    findings: List[Finding] = []

    findings += check_typos(files)
    findings += check_missing_files(files, repo_root, pair_rules_path, git_status)

    if llm is None:
        notes.append("LLM 미연결: 로직 오류 점검은 수행되지 않았습니다 (규칙 기반 점검만 실행).")
    else:
        by_path = {}  # 커밋 여러 개를 합친 diff 에는 같은 파일이 여러 번 나온다
        for f in files:
            by_path[f.path] = by_path.get(f.path, "") + "\n" + f.text
        for f in files:
            if f.status == "deleted":
                continue
            got, note = llm.review_file(f)
            findings += got
            if note:
                notes.append(note)
        findings = [ground(x, by_path) for x in findings]

    findings = _dedupe(findings)
    return ReviewResult(files, findings, notes)


def _norm(s: str) -> str:
    return re.sub(r"\s+", "", s)


def ground(f: Finding, by_path) -> Finding:
    """LLM 지적의 근거가 실제 diff 에 있는지 확인. 없으면 '확인 필요'로 내린다."""
    if f.source != "llm":
        return f
    text = by_path.get(f.file)
    if text is None or not f.evidence or _norm(f.evidence) not in _norm(text):
        f.status = NEEDS_CHECK
        f.reason += " (인용한 근거가 diff 에서 확인되지 않아 '확인 필요'로 분류)"
    return f


def _dedupe(findings: List[Finding]) -> List[Finding]:
    seen, out = set(), []
    for f in findings:
        k = (f.type, f.file, f.line, _norm(f.evidence))
        if k in seen:
            continue
        seen.add(k)
        out.append(f)
    return out
