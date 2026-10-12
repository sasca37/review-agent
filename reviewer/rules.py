"""LLM 없이 동작하는 규칙 기반 점검: 오타 후보, 미반영 파일 후보."""
from __future__ import annotations

import fnmatch
import os
import re
import subprocess
from collections import Counter
from typing import List, Optional

import yaml
from rapidfuzz.distance import OSA

from .vocab import KNOWN_WORDS
from .models import DiffFile, Finding, MISSING_FILE, TYPO, FOUND, NEEDS_CHECK

IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]{4,}")
WORD = re.compile(r"[A-Z]+(?![a-z])|[A-Z]?[a-z]+")

# 자주 틀리는 철자 (소문자) -> 올바른 철자
COMMON_TYPOS = {
    "recieve": "receive", "lenght": "length", "adress": "address", "sucess": "success",
    "occured": "occurred", "seperate": "separate", "paramter": "parameter",
    "retrun": "return", "fucntion": "function", "udpate": "update", "tranfer": "transfer",
    "acount": "account", "accout": "account", "balace": "balance", "ammount": "amount",
    "amout": "amount", "expried": "expired", "pasword": "password", "usre": "user",
    "mesage": "message", "reponse": "response", "reqeust": "request", "tyep": "type",
}


def check_typos(files: List[DiffFile]) -> List[Finding]:
    all_text = "\n".join(
        "\n".join(t for _, t in f.added_lines) + "\n" + f.context_removed_text for f in files
    )
    counts = Counter(IDENT.findall(all_text))
    findings: List[Finding] = []
    seen = set()

    for f in files:
        for lineno, text in f.added_lines:
            for ident in set(IDENT.findall(text)):
                key = (f.path, lineno, ident)
                if key in seen:
                    continue
                # 1) 자주 틀리는 철자 사전
                for w in WORD.findall(ident):
                    good = COMMON_TYPOS.get(w.lower())
                    if good:
                        seen.add(key)
                        findings.append(Finding(
                            TYPO, f.path, lineno, text.strip(),
                            f"'{ident}' 의 '{w}' 는 '{good}' 의 오타로 보입니다.",
                        ))
                        break
                else:
                    # 2) diff 안의 다른 식별자와 1~2글자 차이 (한 번만 등장하는 쪽을 오타 후보로)
                    if counts[ident] != 1:
                        continue
                    cand = _near_match(ident, counts)
                    if cand:
                        seen.add(key)
                        findings.append(Finding(
                            TYPO, f.path, lineno, text.strip(),
                            f"'{ident}' 는 이 변경분에서 {counts[cand]}회 쓰인 '{cand}' 와 "
                            f"철자가 거의 같습니다. 오타인지 확인하세요.",
                            status=NEEDS_CHECK,
                        ))
    return findings


def _words(ident: str) -> List[str]:
    return [w.lower() for w in WORD.findall(ident)] or [ident.lower()]


def _word_typo(a: str, b: str) -> bool:
    """a(변경분에서 한 번만 나온 단어)가 b(여러 번 쓰인 단어)의 오타로 보이는가."""
    if a == b or len(a) < 4 or len(b) < 4 or a in KNOWN_WORDS:
        return False
    if a[0] != b[0]:  # 실제 오타는 첫 글자가 맞는 경우가 대부분 (weight/height 같은 정상 단어 쌍 제외)
        return False
    if a.startswith(b) or b.startswith(a):  # 복수형·접미 추가
        return False
    d = OSA.distance(a, b)
    if d > 2 or d / len(a) > 0.25:
        return False
    diffs = [(x, y) for x, y in zip(a, b) if x != y]
    return not (diffs and all(x.isdigit() and y.isdigit() for x, y in diffs))


def _near_match(ident: str, counts: Counter) -> Optional[str]:
    sw = _words(ident)
    for cand, n in counts.items():
        if cand == ident or n < 2:
            continue
        cw = _words(cand)
        if len(cw) != len(sw):
            continue
        diff = [(x, y) for x, y in zip(sw, cw) if x != y]
        if len(diff) == 1 and _word_typo(*diff[0]):  # 단어 하나만 다르고 그 단어가 오타처럼 보일 때
            return cand
    return None


def check_missing_files(
    files: List[DiffFile], repo_root: Optional[str], pair_rules_path: Optional[str] = None,
    git_status: Optional[str] = None,
) -> List[Finding]:
    """git_status(`git status --porcelain` 텍스트)를 주면 저장소 없이도 1)번 점검을 한다."""
    has_repo = bool(repo_root) and os.path.isdir(repo_root)
    findings: List[Finding] = []
    in_diff = {f.path for f in files}

    # 1) 작업 트리에는 변경됐는데 결재 대상 diff 에 없는 파일
    out = git_status or ""
    if not out and has_repo:
        try:
            out = subprocess.run(
                ["git", "-C", repo_root, "status", "--porcelain"],
                capture_output=True, text=True, check=True, timeout=30,
            ).stdout
        except (OSError, subprocess.SubprocessError):
            out = ""
    for line in out.splitlines():
        m = re.match(r"^\s*([MADRCU?!]{1,2})\s+(.+)$", line)
        if not m:
            continue
        code, path = m.group(1), m.group(2).split(" -> ")[-1].strip().strip('"')
        line = line.strip()
        if path in in_diff or code == "!!":
            continue
        untracked = code == "??"
        findings.append(Finding(
            MISSING_FILE, path, None, f"git status: {line}",
            "작업 트리에서 " + ("새로 만들어졌지만" if untracked else "변경됐지만")
            + " 이번 결재 대상 diff 에 포함되지 않았습니다.",
            status=NEEDS_CHECK if untracked else FOUND,
        ))

    if not has_repo:
        return findings

    # 2) 새로 추가한 내부 모듈 import 가 가리키는 파일이 저장소/diff 어디에도 없음 (python)
    added_paths = {f.path for f in files if f.status in ("added", "renamed")}
    for f in files:
        if not f.path.endswith(".py"):
            continue
        for lineno, text in f.added_lines:
            m = re.match(r"\s*(?:from\s+([\w.]+)\s+import|import\s+([\w.]+))", text)
            mod = (m.group(1) or m.group(2)) if m else None
            if not mod or mod.startswith("."):
                continue
            top = mod.split(".")[0]
            if not (os.path.isdir(os.path.join(repo_root, top))
                    or os.path.isfile(os.path.join(repo_root, top + ".py"))):
                continue  # 내부 패키지가 아니면 판단하지 않음
            rel = mod.replace(".", "/")
            candidates = [rel + ".py", rel + "/__init__.py"]
            if any(os.path.isfile(os.path.join(repo_root, c)) or c in added_paths
                   for c in candidates):
                continue
            findings.append(Finding(
                MISSING_FILE, f.path, lineno, text.strip(),
                f"내부 모듈 '{mod}' 에 해당하는 파일({candidates[0]})이 저장소와 이번 변경분에 없습니다.",
            ))

    # 3) 팀이 정의한 '함께 변경' 파일 쌍 규칙
    findings += _pair_rules(files, repo_root, in_diff, pair_rules_path)
    return findings


def _pair_rules(files, repo_root, in_diff, path) -> List[Finding]:
    if not path or not os.path.isfile(path):
        return []
    with open(path, encoding="utf-8") as fh:
        rules = (yaml.safe_load(fh) or {}).get("rules") or []
    out: List[Finding] = []
    for f in files:
        for r in rules:
            if not fnmatch.fnmatch(f.path, r["if_changed"]):
                continue
            d, name = os.path.split(f.path)
            expect = r["expect"].format(dir=d, stem=os.path.splitext(name)[0], name=name)
            if expect not in in_diff and os.path.isfile(os.path.join(repo_root, expect)):
                out.append(Finding(
                    MISSING_FILE, expect, None, f"변경됨: {f.path}",
                    f"규칙상 {f.path} 와 함께 변경돼야 하는 {expect} 가 변경분에 없습니다.",
                    status=NEEDS_CHECK,
                ))
    return out
