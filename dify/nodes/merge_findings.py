import json
import re

TYPES = {"logic": "로직 오류", "typo": "오타", "missing_file": "미반영 파일", "opinion": "의견"}
PRIORITY = {"상": "상", "중": "중", "하": "하", "high": "상", "medium": "중", "mid": "중", "low": "하", "높음": "상", "보통": "중", "낮음": "하"}


def norm(s):
    return re.sub(r"\s+", "", s or "")


def new_side(text):
    """diff 원문에서 변경 후 코드(추가·문맥 줄)만 접두어 없이 뽑는다. 근거는 이 내용과 대조한다
    (+/- 접두어가 붙은 채 비교하면 여러 줄 인용이 안 맞고, 삭제된 줄 인용이 맞는 것으로 잘못 확인된다)."""
    out, in_hunk = [], True  # 헤더 없이 hunk 본문만 온 경우도 받는다
    for line in (text or "").splitlines():
        if line.startswith("@@"):
            in_hunk = True
        elif line.startswith("diff --git "):
            in_hunk = False
        elif in_hunk and line[:1] in ("+", " "):
            out.append(line[1:])
    return "\n".join(out)


ALIASES = {
    "logic": "logic", "logical": "logic", "logic_error": "logic", "logic error": "logic", "logicerror": "logic",
    "bug": "logic", "error": "logic", "로직": "logic", "로직 오류": "logic", "로직오류": "logic", "버그": "logic",
    "typo": "typo", "spelling": "typo", "misspelling": "typo", "오타": "typo",
    "missing_file": "missing_file", "missing file": "missing_file", "missingfile": "missing_file",
    "missing": "missing_file", "미반영 파일": "missing_file", "미반영파일": "missing_file", "미반영": "missing_file",
    "opinion": "opinion", "suggestion": "opinion", "comment": "opinion", "improvement": "opinion", "review": "opinion",
    "recommendation": "opinion", "의견": "opinion", "제안": "opinion", "개선": "opinion", "리뷰 의견": "opinion",
    "리뷰의견": "opinion", "코멘트": "opinion", "권고": "opinion",
}


def strip_reasoning(text):
    """모델의 생각 과정(<think>)과 분석 메모(<analysis>)를 걷어내 JSON 만 남긴다."""
    t = text or ""
    for tag in ("think", "thinking", "analysis"):
        t = re.sub(r"<%s>.*?</%s>" % (tag, tag), "", t, flags=re.S | re.I)
    return t


def parse_llm(text):
    """응답 안의 JSON 배열을 찾는다. 가장 긴 배열을 답으로 본다 (설명 속 대괄호에 속지 않도록).
    배열을 못 찾으면 None, 찾았지만 비어 있으면 []."""
    t = strip_reasoning(text)
    dec = json.JSONDecoder()
    best, best_len, empty_seen = None, -1, False
    for m in re.finditer(r"\[", t):
        try:
            obj, end = dec.raw_decode(t, m.start())
        except ValueError:
            continue
        if not isinstance(obj, list) or not all(isinstance(d, dict) for d in obj):
            continue
        if not obj:
            empty_seen = True
        elif end - m.start() > best_len:
            best, best_len = obj, end - m.start()
    if best is not None:
        return best
    return [] if empty_seen else None


def norm_type(t):
    return ALIASES.get(re.sub(r"\s+", " ", str(t or "")).strip().lower())


def snippet(text, n=200):
    return re.sub(r"\s+", " ", strip_reasoning(text)).strip()[:n]


def cell(s):
    return str(s).replace("|", "\\|").replace("\n", " ")


def main(llm_text: str, rule_findings_json: str, files_json: str, notice: str) -> dict:
    files = json.loads(files_json)
    # 커밋 여러 개를 합친 diff 에는 같은 파일이 커밋마다 따로 나온다. 근거는 그 파일의 모든 조각에서 찾는다
    by_path = {}
    for f in files:
        by_path[f["path"]] = by_path.get(f["path"], "") + "\n" + new_side(f["text"])
    notes = [notice] if notice else []
    findings = json.loads(rule_findings_json)

    items = parse_llm(llm_text)
    if items is None:
        notes.append("LLM 응답을 해석하지 못해 로직 점검 결과가 포함되지 않았습니다. 다시 실행하세요. (응답 앞부분: %s)"
                     % (snippet(llm_text) or "비어 있음"))
        items = []
    elif not items:
        # '이상 없음'이 점검 결과인지 모델이 아무것도 못 찾은 것인지 구분할 수 있게 남긴다
        notes.append("LLM 이 결과를 0건 반환했습니다. (응답 앞부분: %s)" % (snippet(llm_text) or "비어 있음"))
    for it in items:
        t = norm_type(it.get("type"))
        path, evidence = str(it.get("file") or ""), str(it.get("evidence") or "").strip()
        reason = str(it.get("reason") or "").strip()
        line = it.get("line") if isinstance(it.get("line"), int) else None
        text = by_path.get(path)
        verified = text is not None and bool(evidence) and norm(evidence) in norm(text)
        if t is None or t == "opinion":
            # 리뷰 의견: 근거 줄이 없어도 된다. 낯선 유형 이름(security, performance …)은 의견의 분류로 살린다.
            category = str(it.get("category") or "").strip()
            if t is None:
                category = category or str(it.get("type") or "").strip()
            if not verified:  # 인용한 줄이 diff 에서 확인되지 않으면 인용·줄번호를 보여주지 않는다 (지어낸 인용 방지)
                evidence, line = "", None
                if text is None:
                    path = ""
            priority = PRIORITY.get(str(it.get("priority") or "").strip().lower(), "")
            findings.append({"type": "opinion", "category": category or "기타", "priority": priority,
                             "file": path, "line": line, "evidence": evidence, "reason": reason,
                             "status": "의견", "source": "llm"})
            continue
        status = "확인 필요" if it.get("needs_check") else "지적"
        # 결함은 인용한 근거가 해당 파일 diff 에 실제로 있어야 한다. 없으면 '확인 필요'
        if not verified:
            status = "확인 필요"
            reason += " (인용한 근거가 diff 에서 확인되지 않아 '확인 필요'로 분류)"
        findings.append({"type": t, "file": path, "line": line, "evidence": evidence,
                         "reason": reason, "status": status, "source": "llm"})

    seen, uniq = set(), []
    for f in findings:
        # 의견은 줄·근거가 비어 있는 경우가 많아, 내용(reason)까지 같을 때만 중복으로 본다
        k = (f["type"], f["file"], f["line"], norm(f["evidence"]), norm(f["reason"]) if f["type"] == "opinion" else "")
        if k not in seen:
            seen.add(k)
            uniq.append(f)
    def order(f):
        if f["status"] == "의견":  # 총평(전반) → 우선순위 상·중·하 → 위치
            return (1, f.get("category") != "전반", {"상": 0, "중": 1, "하": 2}.get(f.get("priority"), 3), f["file"], f["line"] or 0)
        return (0, f["status"] != "지적", 0, f["file"], f["line"] or 0)
    uniq.sort(key=order)

    defects = [f for f in uniq if f["status"] != "의견"]
    opinions = [f for f in uniq if f["status"] == "의견"]
    n_found = len([f for f in defects if f["status"] == "지적"])
    lines = ["## 사전 점검 결과", "",
             "- 변경 파일 %d개 · 지적 %d건 · 확인 필요 %d건 · 리뷰 의견 %d건"
             % (len(by_path), n_found, len(defects) - n_found, len(opinions)), ""]
    lines += ["### 지적 사항", ""]
    if defects:
        lines += ["| 구분 | 유형 | 위치 | 근거 | 설명 |", "|---|---|---|---|---|"]
        for f in defects:
            loc = f["file"] + (":%d" % f["line"] if f["line"] else "")
            lines.append("| %s | %s | `%s` | `%s` | %s |" % (
                f["status"], TYPES[f["type"]], cell(loc), cell(f["evidence"]), cell(f["reason"])))
    else:
        lines.append("점검한 범위에서 지적할 결함이 없습니다. (점검 범위 밖의 오류는 여전히 있을 수 있습니다)")
    if opinions:
        lines += ["", "### 리뷰 의견", "", "| 우선순위 | 분류 | 위치 | 의견 |", "|---|---|---|---|"]
        for f in opinions:
            loc = (f["file"] + (":%d" % f["line"] if f["line"] else "")) or "전체"
            body = ("`%s` — " % cell(f["evidence"]) if f["evidence"] else "") + cell(f["reason"])
            lines.append("| %s | %s | `%s` | %s |" % (f.get("priority") or "-", cell(f.get("category", "")), cell(loc), body))
    if notes:
        lines += ["", "### 안내"] + ["- " + n for n in notes]
    lines += ["", "> AI 는 오류 후보를 제안할 뿐입니다. 수정 여부는 개발자가 정하고, 고친 뒤 다시 점검할 수 있습니다. "
              "③ 동료 코드리뷰는 그대로 진행하세요."]
    return {"report": "\n".join(lines), "findings_json": json.dumps(uniq, ensure_ascii=False)}
