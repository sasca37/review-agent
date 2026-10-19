import codecs
import json
import re

IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]{4,}")
WORD = re.compile(r"[A-Z]+(?![a-z])|[A-Z]?[a-z]+")
HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
COMMON_TYPOS = {
    "recieve": "receive", "lenght": "length", "adress": "address", "sucess": "success",
    "occured": "occurred", "seperate": "separate", "paramter": "parameter",
    "retrun": "return", "fucntion": "function", "udpate": "update", "tranfer": "transfer",
    "acount": "account", "accout": "account", "balace": "balance", "ammount": "amount",
    "amout": "amount", "expried": "expired", "pasword": "password", "usre": "user",
    "mesage": "message", "reponse": "response", "reqeust": "request", "tyep": "type",
}
MAX_LLM_CHARS = 60000  # LLM 에 보낼 변경분 최대 글자 수 (모델 컨텍스트에 맞춰 조정)

# 오타 후보에서 제외할 정상 단어 (reviewer/vocab.py 와 같은 내용). 자주 나오는 오탐 단어를 여기에 추가하세요.
KNOWN_WORDS = set("""
absolute account address align amount animation array auto background balance baseline begin black
block blur bold bolder boolean border bottom calc capitalize center child children class clip close
code codes color column const contain content count cover cursor dark dashed date dates decoration
delete depth desktop display dotted email end error errors event family field fields filter final
finish first fixed flex font footer function gradient gray green grey grid group groups handler
having header height hidden hour image index inherit initial inline insert italic item items justify
key keys label large last left length letter level light lighter limit line load long lowercase
margin medium message method minute mobile month name names next none normal nowrap number object
offset opacity open orange order origin overflow padding param params parent phone pointer position
prev previous price private public purple query radius range read receive relative repeat request
response result results return reverse right role roles rotate save scale scroll second select send
shadow short size skew small solid source spacing start state static status sticky stretch string
style table tablet target text time times title token tokens top total transform transition
translate type types underline unset update uppercase user users value values visible week weight
where white width wrap write year yellow
""".split())


def osa(a, b):
    """전치(철자 순서 바뀜)를 1회로 세는 편집 거리."""
    d = [[0] * (len(b) + 1) for _ in range(len(a) + 1)]
    for i in range(len(a) + 1):
        d[i][0] = i
    for j in range(len(b) + 1):
        d[0][j] = j
    for i in range(1, len(a) + 1):
        for j in range(1, len(b) + 1):
            c = 0 if a[i - 1] == b[j - 1] else 1
            d[i][j] = min(d[i - 1][j] + 1, d[i][j - 1] + 1, d[i - 1][j - 1] + c)
            if i > 1 and j > 1 and a[i - 1] == b[j - 2] and a[i - 2] == b[j - 1]:
                d[i][j] = min(d[i][j], d[i - 2][j - 2] + 1)
    return d[len(a)][len(b)]


def unquote(p):
    """git 이 따옴표로 감싼 경로("\\355\\225\\234.txt" 같은 한글·특수문자 이름)를 원래 이름으로 되돌린다."""
    if len(p) >= 2 and p[0] == p[-1] == '"':
        return codecs.escape_decode(p[1:-1].encode())[0].decode("utf-8", "replace")
    return p


def strip_prefix(p):
    p = unquote(p.strip().split("\t")[0])
    return p[2:] if p[:2] in ("a/", "b/") else p


def parse(diff_text):
    files, cur = [], None
    old_left = new_left = new_no = 0

    def new_file(path=""):
        f = {"path": path, "status": "modified", "added": 0, "removed": 0,
             "added_lines": [], "other": [], "numbered": [], "raw": [], "seen_hunk": False}
        files.append(f)
        return f

    for raw in diff_text.splitlines():
        in_hunk = old_left > 0 or new_left > 0
        if in_hunk and cur is not None and (raw == "" or raw[:1] in (" ", "-", "+", "\\")):
            cur["raw"].append(raw)
            sign, text = (raw[:1], raw[1:]) if raw else (" ", "")
            if sign == "+":
                cur["added"] += 1
                cur["added_lines"].append((new_no, text))
                cur["numbered"].append("%4d + %s" % (new_no, text))
                new_no += 1
                new_left -= 1
            elif sign == "-":
                cur["removed"] += 1
                cur["other"].append(text)
                cur["numbered"].append("     - %s" % text)
                old_left -= 1
            elif sign == " ":
                cur["other"].append(text)
                cur["numbered"].append("%4d   %s" % (new_no, text))
                new_no += 1
                old_left -= 1
                new_left -= 1
            continue
        old_left = new_left = 0
        if raw.startswith("diff --git "):
            m = re.match(r'diff --git (?:a/(.*) b/(.*)|"a/.*" ("b/.*"))$', raw)
            cur = new_file((m.group(2) or strip_prefix(m.group(3))) if m else raw[11:].strip())
            cur["raw"].append(raw)
            continue
        m = HUNK.match(raw)
        if m and cur is not None:
            cur["seen_hunk"] = True
            old_left = int(m.group(2)) if m.group(2) is not None else 1
            new_left = int(m.group(4)) if m.group(4) is not None else 1
            new_no = int(m.group(3))
            cur["raw"].append(raw)
            cur["numbered"].append(raw)
            continue
        if raw.startswith("--- "):
            if cur is None or cur["seen_hunk"]:
                cur = new_file()
            cur["raw"].append(raw)
            if "/dev/null" in raw:
                cur["status"] = "added"
            elif not cur["path"]:
                cur["path"] = strip_prefix(raw[4:])
            continue
        if cur is None:
            continue
        cur["raw"].append(raw)
        if raw.startswith("+++ "):
            if "/dev/null" in raw:
                cur["status"] = "deleted"
            else:
                cur["path"] = strip_prefix(raw[4:])
        elif raw.startswith("new file mode"):
            cur["status"] = "added"
        elif raw.startswith("deleted file mode"):
            cur["status"] = "deleted"
        elif raw.startswith("rename to "):
            cur["status"] = "renamed"
            cur["path"] = unquote(raw[10:].strip())
    return [f for f in files if f["path"]]


def check_typos(files):
    all_text = "\n".join(
        "\n".join(t for _, t in f["added_lines"]) + "\n" + "\n".join(f["other"]) for f in files)
    counts = {}
    for i in IDENT.findall(all_text):
        counts[i] = counts.get(i, 0) + 1
    out, seen = [], set()
    for f in files:
        for lineno, text in f["added_lines"]:
            for ident in sorted(set(IDENT.findall(text))):
                if (f["path"], lineno, ident) in seen:
                    continue
                hit = None
                for w in WORD.findall(ident):
                    if w.lower() in COMMON_TYPOS:
                        hit = ("'%s' 의 '%s' 는 '%s' 의 오타로 보입니다." % (ident, w, COMMON_TYPOS[w.lower()]), "지적")
                        break
                if hit is None and counts.get(ident) == 1:
                    cand = near_match(ident, counts)
                    if cand:
                        hit = ("'%s' 는 이 변경분에서 %d회 쓰인 '%s' 와 철자가 거의 같습니다. 오타인지 확인하세요."
                               % (ident, counts[cand], cand), "확인 필요")
                if hit:
                    seen.add((f["path"], lineno, ident))
                    out.append({"type": "typo", "file": f["path"], "line": lineno, "evidence": text.strip(),
                                "reason": hit[0], "status": hit[1], "source": "rule"})
    return out


def words_of(ident):
    return [w.lower() for w in WORD.findall(ident)] or [ident.lower()]


def word_typo(a, b):
    """a(변경분에서 한 번만 나온 단어)가 b(여러 번 쓰인 단어)의 오타로 보이는가."""
    if a == b or len(a) < 4 or len(b) < 4 or a in KNOWN_WORDS:
        return False
    if a[0] != b[0]:  # 실제 오타는 첫 글자가 맞는 경우가 대부분 (weight/height 같은 정상 단어 쌍 제외)
        return False
    if a.startswith(b) or b.startswith(a):  # 복수형·접미 추가
        return False
    d = osa(a, b)
    if d > 2 or float(d) / len(a) > 0.25:
        return False
    diffs = [(x, y) for x, y in zip(a, b) if x != y]
    return not (diffs and all(x.isdigit() and y.isdigit() for x, y in diffs))


def near_match(ident, counts):
    sw = words_of(ident)
    for cand, n in counts.items():
        if cand == ident or n < 2:
            continue
        cw = words_of(cand)
        if len(cw) != len(sw):
            continue
        diff = [(x, y) for x, y in zip(sw, cw) if x != y]
        if len(diff) == 1 and word_typo(*diff[0]):  # 단어 하나만 다르고 그 단어가 오타처럼 보일 때
            return cand
    return None


CUT_MARK = "[이하 생략: diff 가 길어 여기서 잘렸습니다. 이 아래 줄은 점검 대상이 아니며, 잘린 줄은 문제로 판단하지 마세요]"


def build_llm_input(files, limit=None):
    """LLM 에 보낼 입력을 만든다. 길면 파일 단위로 채우다가 줄 경계에서 자르고, 잘린 곳과 빠진 파일을 명시한다.
    (글자 수로 그냥 자르면 줄이 '@' 같은 조각으로 끝나 문법 오류로 오해받는다)"""
    limit = limit or MAX_LLM_CHARS
    parts, used = [], 0
    partial, omitted = None, []
    for k, f in enumerate(files):
        head = "### %s (%s)" % (f["path"], f["status"])
        full = head + "\n" + "\n".join(f["numbered"])
        if used + len(full) + 2 <= limit and partial is None and not omitted:
            parts.append(full)
            used += len(full) + 2
            continue
        room = limit - used - len(head) - len(CUT_MARK) - 4
        if partial is None and not omitted and room >= 1000:  # 이 파일은 줄 경계까지만 보낸다
            kept, size = [], 0
            for line in f["numbered"]:
                if size + len(line) + 1 > room:
                    break
                kept.append(line)
                size += len(line) + 1
            parts.append(head + "\n" + "\n".join(kept) + "\n" + CUT_MARK)
            used = limit
            partial = f["path"]
            omitted = [g["path"] for g in files[k + 1:]]
        else:
            omitted = [g["path"] for g in files[k:]]
        break
    text = "\n\n".join(parts)
    notice = ""
    if partial or omitted:
        if omitted:
            text += "\n\n[점검 제외 파일: %s — diff 가 길어 전달하지 않았습니다. 이 파일들이 없다고 판단하지 마세요]" % ", ".join(omitted)
        notice = "diff 가 길어 일부만 LLM 이 검토했습니다."
        if partial:
            notice += " 일부만 검토: %s." % partial
        if omitted:
            notice += " 검토 제외: %s." % ", ".join(omitted[:10]) + (" 외 %d개" % (len(omitted) - 10) if len(omitted) > 10 else "")
    return text, notice


def check_git_status(files, git_status):
    in_diff = set(f["path"] for f in files)
    out = []
    for line in (git_status or "").splitlines():
        m = re.match(r"^\s*([MADRCU?!]{1,2})\s+(.+)$", line)
        if not m:
            continue
        code, path = m.group(1), unquote(m.group(2).split(" -> ")[-1].strip())
        if path in in_diff or code == "!!":
            continue
        untracked = code == "??"
        out.append({"type": "missing_file", "file": path, "line": None, "evidence": "git status: " + line.strip(),
                    "reason": "작업 트리에서 " + ("새로 만들어졌지만" if untracked else "변경됐지만")
                              + " 이번 결재 대상 diff 에 포함되지 않았습니다.",
                    "status": "확인 필요" if untracked else "지적", "source": "rule"})
    return out


def main(diff_text: str, git_status: str) -> dict:
    files = parse(diff_text or "")
    if not files:
        raise ValueError("diff 를 해석할 수 없습니다. 'git diff' 결과(unified diff)를 붙여넣었는지 확인하세요.")
    rule = check_typos(files) + check_git_status(files, git_status)

    table = ["| 파일 | 상태 | + | - |", "|---|---|---|---|"]
    table += ["| `%s` | %s | %d | %d |" % (f["path"], f["status"], f["added"], f["removed"]) for f in files]

    llm_input, notice = build_llm_input(files)

    snippets = []
    for f in files:
        snippets.append(f["path"] + "\n" + "\n".join(t for _, t in f["added_lines"][:40]))
    kb_query = ("로직 오류 오타 미반영 파일 점검\n" + "\n".join(snippets))[:1500]

    files_json = [{"path": f["path"], "status": f["status"], "added": f["added"], "removed": f["removed"],
                   "text": "\n".join(f["raw"])} for f in files]
    return {
        "files_table": "\n".join(table),
        "llm_input": llm_input,
        "rule_findings_json": json.dumps(rule, ensure_ascii=False),
        "files_json": json.dumps(files_json, ensure_ascii=False),
        "notice": notice,
        "kb_query": kb_query,
    }
