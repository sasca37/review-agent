import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile

import yaml

from evals.run_eval import ROOT, build_repo

NODES = os.path.join(ROOT, "dify", "nodes")


def load(name):
    spec = importlib.util.spec_from_file_location(name, os.path.join(NODES, name + ".py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


parse_diff, merge = load("parse_diff"), load("merge_findings")


def sample_inputs():
    sample = os.path.join(ROOT, "samples", "sample1")
    tmp = tempfile.mkdtemp()
    try:
        build_repo(sample, tmp)
        diff = subprocess.run(["git", "-C", tmp, "diff", "HEAD", "--", "bank/transfer.py"],
                              capture_output=True, text=True, check=True).stdout
        status = subprocess.run(["git", "-C", tmp, "status", "--porcelain"],
                                capture_output=True, text=True, check=True).stdout
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return diff, status


def test_parse_matches_diff_and_rules():
    diff, status = sample_inputs()
    out = parse_diff.main(diff, status)
    files = json.loads(out["files_json"])
    assert [f["path"] for f in files] == ["bank/transfer.py"]
    assert (files[0]["added"], files[0]["removed"]) == (6, 4)  # git diff --numstat 과 일치
    found = {(f["type"], f["file"]) for f in json.loads(out["rule_findings_json"])}
    assert ("typo", "bank/transfer.py") in found
    assert ("missing_file", "bank/fee.py") in found and ("missing_file", "bank/limits.py") in found
    typo = [f for f in json.loads(out["rule_findings_json"]) if f["type"] == "typo"][0]
    assert typo["line"] == 11 and "amuont" in typo["evidence"]


def test_merge_grounds_llm_and_handles_bad_json():
    diff, status = sample_inputs()
    out = parse_diff.main(diff, status)
    good = {"type": "logic", "file": "bank/transfer.py", "line": 8,
            "evidence": "if src.balance < amount:", "reason": "수수료 미반영", "needs_check": False}
    fake = dict(good, evidence="x = nothing()", line=3)
    llm = "설명...\n" + json.dumps([good, fake], ensure_ascii=False)
    res = merge.main(llm, out["rule_findings_json"], out["files_json"], out["notice"])
    f = {x["evidence"]: x["status"] for x in json.loads(res["findings_json"]) if x["source"] == "llm"}
    assert f["if src.balance < amount:"] == "지적" and f["x = nothing()"] == "확인 필요"
    assert "사전 점검 결과" in res["report"]
    bad = merge.main("죄송합니다", out["rule_findings_json"], out["files_json"], "")
    assert "해석하지 못해" in bad["report"]


def test_merge_grounds_evidence_in_earlier_commit_of_same_file():
    # 커밋 여러 개를 합친 diff: 같은 파일이 커밋마다 따로 나온다. 앞 커밋의 줄을 인용해도 근거로 인정해야 한다
    files = [{"path": "a.py", "text": "+first = 1\n"}, {"path": "a.py", "text": "+second = 2\n"}]
    llm = json.dumps([{"type": "logic", "file": "a.py", "line": 1, "evidence": "first = 1", "reason": "r"}])
    res = merge.main(llm, "[]", json.dumps(files), "")
    assert [x["status"] for x in json.loads(res["findings_json"])] == ["지적"]
    assert "변경 파일 1개" in res["report"]


def test_no_false_positive_on_clean_diff():
    d = ("diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -1,2 +1,2 @@\n def total(items):\n"
         "-    return sum(items)\n+    return sum(i for i in items)\n")
    out = parse_diff.main(d, "")
    assert json.loads(out["rule_findings_json"]) == []


def test_headerless_and_new_file_diff():
    d = ("--- /dev/null\n+++ b/n.py\n@@ -0,0 +1,2 @@\n+a = 1\n+b = 2\n")
    files = json.loads(parse_diff.main(d, "")["files_json"])
    assert files[0]["path"] == "n.py" and files[0]["status"] == "added" and files[0]["added"] == 2


def _knowledge_files():
    base = os.path.join(ROOT, "dify")
    out = []
    for folder in ("knowledge", "knowledge-tasks"):
        for dp, _, fns in os.walk(os.path.join(base, folder)):
            out += [os.path.join(dp, f) for f in fns if f.endswith(".txt")]
    return sorted(out)


def test_knowledge_files_format():
    files = _knowledge_files()
    assert len(files) >= 20  # 공통 16 + 업무별(업무당 1개 이상)
    for path in files:
        raw = open(path, encoding="utf-8").read()
        lines = raw.splitlines()
        assert lines[0] == "문서 ID: " + os.path.basename(path)[:-4]
        assert all(l.strip() and not l.startswith("#") for l in lines), path  # 빈 줄·마크다운 제목 금지
        assert any(l.startswith("예외: ") for l in lines) and len(raw) < 2000


def test_task_knowledge_matches_task_list():
    from reviewer.tasks import TASK_SLUGS
    for task, slug in TASK_SLUGS:
        d = os.path.join(ROOT, "dify", "knowledge-tasks", slug)
        names = [n for n in os.listdir(d) if n.endswith(".txt")]  # .DS_Store 같은 숨김 파일 제외
        assert len(names) >= 1
        for n in names:  # 각 문서가 자기 업무명을 담고 있어야 잘못된 폴더에 올리는 실수를 막을 수 있음
            assert "업무: " + task in open(os.path.join(d, n), encoding="utf-8").read()


def test_knowledge_does_not_leak_sample_answers():
    text = "".join(open(p, encoding="utf-8").read() for p in _knowledge_files())
    for leak in ("amuont", "calc_fee", "bank.limits", "src.balance", "grade <= 5", "totlaFee", "double rate"):
        assert leak not in text  # 샘플 평가의 심어 둔 오류가 지식에 들어가면 평가가 오염됨


def test_merge_knowledge_node():
    mk = load("merge_knowledge")
    out = mk.main([{"content": "공통 항목 A"}, {"content": ""}], [{"content": "업무 항목 B"}], "민원 서비스")["knowledge_text"]
    assert out.index("업무 지식: 민원 서비스") < out.index("공통 점검 기준")  # 업무 지식이 먼저
    assert "업무 항목 B" in out and "공통 항목 A" in out
    assert "참고 지식 없음" in mk.main(None, None, "")["knowledge_text"]


def test_branch_routes_each_task_to_its_own_knowledge():
    """IF/ELSE 조건을 같은 규칙(순서대로 'is' 비교, 없으면 else)으로 평가해 업무별 노드로 가는지 확인."""
    from dify.build_workflow import build
    from reviewer.tasks import TASKS
    g = build()["workflow"]["graph"]
    nodes = {n["id"]: n["data"] for n in g["nodes"]}
    cases = nodes["branch"]["cases"]
    handles = {e["sourceHandle"]: e["target"] for e in g["edges"] if e["source"] == "branch"}
    reached = []
    for task in TASKS:
        hit = next((c["case_id"] for c in cases
                    if all(cd["value"] == task for cd in c["conditions"])), "false")
        target = handles[hit]
        assert task in nodes[target]["title"], (task, target)
        reached.append(target)
    assert len(set(reached)) == len(TASKS)
    assert set(nodes["start"]["variables"][0]["options"]) == set(TASKS)
    assert nodes["start"]["variables"][0]["type"] == "select" and nodes["start"]["variables"][0]["required"]


def test_workflow_yml_is_consistent():
    from dify.build_workflow import build
    app = build()
    g = app["workflow"]["graph"]
    ids = {n["id"] for n in g["nodes"]}
    assert all(e["source"] in ids and e["target"] in ids for e in g["edges"])
    path = os.path.join(ROOT, "dify", "review-agent.yml")
    on_disk = yaml.safe_load(open(path, encoding="utf-8"))
    # review-agent.yml 은 Dify 에서 모델·지식을 지정해 다시 내보낸 사용본일 수 있으므로 통째로 비교하지 않고
    # 그래프 구조와 코드 노드의 코드가 dify/nodes 와 같은지만 본다.
    disk_nodes = {n["id"]: n["data"] for n in on_disk["workflow"]["graph"]["nodes"]}
    assert set(disk_nodes) == ids
    assert {(e["source"], e["target"]) for e in on_disk["workflow"]["graph"]["edges"]} == \
        {(e["source"], e["target"]) for e in g["edges"]}
    for node_id, fname in (("parse", "parse_diff.py"), ("merge", "merge_findings.py"), ("know", "merge_knowledge.py")):
        src = open(os.path.join(NODES, fname), encoding="utf-8").read()
        assert disk_nodes[node_id]["code"].strip() == src.strip(), f"{node_id} 노드 코드가 {fname} 와 다릅니다"
    for n in g["nodes"]:  # 변수 참조가 실제 노드 출력과 맞는지
        for v in n["data"].get("variables", []):
            if "value_selector" in v:
                src = next(x for x in g["nodes"] if x["id"] == v["value_selector"][0])
                assert v["value_selector"][1] in (src["data"].get("outputs") or {}) or src["data"]["type"] in ("start", "llm", "knowledge-retrieval", "variable-aggregator")


# ---- 오탐 회귀: font-weight 의 weight 가 height 와 철자가 비슷하다고 지적된 사례 ----
def css_diff(added_lines, context_heights=12):
    ctx = "".join(" .box%d { height: %dpx; }\n" % (i, i * 10) for i in range(context_heights))
    add = "".join("+" + l + "\n" for l in added_lines)
    n_old, n_new = context_heights, context_heights + len(added_lines)
    return ("diff --git a/frontend/src/styles/main.css b/frontend/src/styles/main.css\n"
            "--- a/frontend/src/styles/main.css\n+++ b/frontend/src/styles/main.css\n"
            "@@ -280,%d +280,%d @@\n%s%s" % (n_old, n_new, ctx, add))


def typo_lines(diff):
    return [(f["line"], f["evidence"]) for f in json.loads(parse_diff.main(diff, "")["rule_findings_json"])
            if f["type"] == "typo"]


def python_typo_lines(diff):
    from reviewer.service import review
    return [(f.line, f.evidence) for f in review(diff).findings if f.type == "typo"]


def test_font_weight_is_not_a_typo_of_height():
    d = css_diff(["  .email-signup-button {", "    font-weight: 750;", "  }"])
    assert typo_lines(d) == []          # Dify 노드
    assert python_typo_lines(d) == []   # 내장 엔진


def test_compound_names_of_valid_words_are_not_typos():
    d = ("diff --git a/A.java b/A.java\n--- a/A.java\n+++ b/A.java\n@@ -1,3 +1,4 @@\n"
         " int getHeight() { return height; }\n int getHeight2() { return getHeight(); }\n"
         " int getHeight3() { return getHeight(); }\n+int getWeight() { return weight; }\n")
    assert typo_lines(d) == [] and python_typo_lines(d) == []


def test_real_typos_are_still_found_after_fix():
    d = ("diff --git a/A.java b/A.java\n--- a/A.java\n+++ b/A.java\n@@ -1,3 +1,4 @@\n"
         " int totalFee = a;\n int totalFee2 = totalFee + 1;\n int x = totalFee;\n+log(totlaFee);\n")
    assert [e for _, e in typo_lines(d)] == ["log(totlaFee);"]
    assert [e for _, e in python_typo_lines(d)] == ["log(totlaFee);"]


def test_known_words_in_sync_between_dify_node_and_python():
    from reviewer.vocab import KNOWN_WORDS
    assert set(parse_diff.KNOWN_WORDS) == set(KNOWN_WORDS), "reviewer/vocab.py 와 dify/nodes/parse_diff.py 를 맞추세요"


def test_dify_node_and_python_rules_agree_on_all_samples():
    corpus = [open(os.path.join(ROOT, "samples", "diffs", n), encoding="utf-8").read()
              for n in sorted(os.listdir(os.path.join(ROOT, "samples", "diffs"))) if n.endswith(".diff")]
    corpus.append(css_diff(["    font-weight: 750;"]))
    for d in corpus:
        assert typo_lines(d) == python_typo_lines(d)


# ---- LLM 응답 해석: 지적이 조용히 사라지지 않아야 함 ----
FILES = lambda: json.dumps([{"path": "A.java", "status": "modified", "added": 1, "removed": 0,
                             "text": "+int grade = c.getGrade();"}])
ITEM = {"type": "logic", "file": "A.java", "line": 2, "evidence": "int grade = c.getGrade();",
        "reason": "c 가 null 일 수 있음", "needs_check": False}


def merged(llm_text):
    res = merge.main(llm_text, "[]", FILES(), "")
    return json.loads(res["findings_json"]), res["report"]


def test_reply_following_the_prompt_format_is_parsed():
    """프롬프트가 요구하는 형식(<analysis> 메모 + JSON 배열)대로 답하면, 메모 속 대괄호가 있어도 지적을 읽는다."""
    reply = ("<analysis>줄 2: [null 검사 없음] c 가 null 이면 NPE. 경계값 [해당 없음]</analysis>\n"
             + json.dumps([ITEM], ensure_ascii=False))
    f, _ = merged(reply)
    assert [(x["type"], x["status"]) for x in f] == [("logic", "지적")]


def test_reasoning_block_and_code_fence_are_ignored():
    reply = "<think>생각 [1] 과 [x]</think>\n```json\n" + json.dumps([ITEM], ensure_ascii=False) + "\n```"
    assert len(merged(reply)[0]) == 1


def test_unusual_type_names_are_kept_not_dropped():
    for t in ("bug", "Logic_Error", "로직 오류"):
        f, _ = merged(json.dumps([dict(ITEM, type=t)]))
        assert len(f) == 1 and f[0]["type"] == "logic" and f[0]["status"] == "지적", t
    # 모르는 유형(security, performance …)은 버리지 않고 '의견'의 분류로 살린다
    f, _ = merged(json.dumps([dict(ITEM, type="security")]))
    assert len(f) == 1 and f[0]["type"] == "opinion" and f[0]["category"] == "security" and f[0]["status"] == "의견"


def test_empty_reply_is_distinguishable_from_clean_result():
    f, report = merged("<analysis>문제 없음</analysis>\n[]")
    assert f == [] and "LLM 이 결과를 0건 반환했습니다" in report
    f, report = merged("죄송하지만 도와드릴 수 없습니다.")
    assert f == [] and "해석하지 못해" in report and "죄송하지만" in report  # 원문 앞부분이 안내에 포함


def test_longest_array_wins_over_stray_brackets():
    reply = "참고 [] 입니다.\n" + json.dumps([ITEM, dict(ITEM, line=3)], ensure_ascii=False) + "\n끝 [메모]"
    assert len(merged(reply)[0]) == 2


def test_prompt_matches_output_format_the_parser_expects():
    from dify.build_workflow import SYSTEM
    assert "<analysis>" in SYSTEM and "JSON 배열" in SYSTEM and "needs_check" in SYSTEM
    for t in ("logic", "typo", "missing_file", "opinion", "category", "priority"):
        assert t in SYSTEM
    for cat in ("설계", "트랜잭션·동시성", "성능", "보안", "예외·로깅", "가독성", "테스트·운영", "전반"):
        assert cat in SYSTEM


def test_eval_scoring_is_one_to_one():
    from evals.run_dify_eval import score
    planted = [{"id": "a", "type": "logic", "file": "A.java", "line": 5},
               {"id": "b", "type": "logic", "file": "A.java", "line": 6}]
    # 지적 1건이 이웃한 정답 2개를 동시에 맞힌 것으로 치지 않는다
    caught, missed, fp = score(planted, [{"type": "logic", "file": "A.java", "line": 5}])
    assert caught == ["a"] and missed == ["b"] and fp == []
    caught, missed, fp = score(planted, [{"type": "typo", "file": "A.java", "line": 5}])
    assert caught == [] and len(fp) == 1


# ---- LLM 입력이 길 때: 줄 중간에서 잘려 '@' 같은 조각으로 끝나면 문법 오류로 오해받는다 ----
def _big_diff(n_big, tail_files=2):
    big = "".join("+    int v%d = compute(%d);\n" % (i, i) for i in range(n_big))
    out = ("diff --git a/Big.java b/Big.java\n--- a/Big.java\n+++ b/Big.java\n@@ -1,1 +1,%d @@\n class Big {\n%s"
           % (n_big + 1, big))
    for k in range(tail_files):
        out += ("diff --git a/Svc%d.java b/Svc%d.java\n--- a/Svc%d.java\n+++ b/Svc%d.java\n@@ -1,2 +1,4 @@\n class Svc {\n"
                "+    @Transactional(propagation = Propagation.REQUIRES_NEW)\n+    public void save() {}\n }\n" % (k, k, k, k))
    return out


def _rows_to_exceed_limit():
    """_big_diff 의 큰 파일이 LLM 입력 한도를 막 넘는 줄 수 (한도를 바꿔도 테스트가 따라가도록 계산)."""
    n = 10
    while len("\n".join(parse_diff.parse(_big_diff(n, 0))[0]["numbered"])) <= parse_diff.MAX_LLM_CHARS:
        n += 25
    return n


def _allowed_lines(diff):
    files = parse_diff.parse(diff)
    ok = {"", parse_diff.CUT_MARK}
    for f in files:
        ok.add("### %s (%s)" % (f["path"], f["status"]))
        ok.update(f["numbered"])
    return ok


def test_short_diff_is_sent_whole_without_notice():
    d = _big_diff(5)
    out = parse_diff.main(d, "")
    assert out["notice"] == "" and parse_diff.CUT_MARK not in out["llm_input"]
    assert "@Transactional(propagation = Propagation.REQUIRES_NEW)" in out["llm_input"]


def test_long_diff_is_cut_on_line_boundary_for_every_possible_offset():
    base = _rows_to_exceed_limit()
    for n in range(base - 70, base + 30):  # 잘리는 위치를 바꿔 가며: 어느 경우에도 줄이 조각으로 끝나면 안 됨
        d = _big_diff(n)
        out = parse_diff.main(d, "")
        ok = _allowed_lines(d)
        for line in out["llm_input"].splitlines():
            assert line in ok or line.startswith("[점검 제외 파일:"), (n, line[-40:])


def test_cut_is_announced_to_the_llm_and_the_user():
    d = _big_diff(_rows_to_exceed_limit() + 50)
    out = parse_diff.main(d, "")
    assert parse_diff.CUT_MARK in out["llm_input"]                 # 모델에게: 여기서 잘렸다
    assert "점검 제외 파일: Svc0.java, Svc1.java" in out["llm_input"]  # 모델에게: 빠진 파일은 없는 게 아니다
    assert "일부만 검토: Big.java" in out["notice"] and "검토 제외: Svc0.java, Svc1.java" in out["notice"]  # 사용자에게


def test_files_that_fit_are_sent_whole_and_the_rest_listed():
    # 작은 파일 여러 개 + 마지막에 큰 파일: 앞의 파일들은 통째로 가야 함
    small = "".join(("diff --git a/S%d.java b/S%d.java\n--- a/S%d.java\n+++ b/S%d.java\n@@ -1,1 +1,2 @@\n class S {\n"
                     "+    @Transactional(propagation = Propagation.REQUIRES_NEW)\n") % (i, i, i, i) for i in range(3))
    d = small + _big_diff(_rows_to_exceed_limit() + 500, tail_files=0)
    out = parse_diff.main(d, "")
    for i in range(3):
        assert "### S%d.java" % i in out["llm_input"]
    assert out["llm_input"].count("@Transactional(propagation = Propagation.REQUIRES_NEW)") == 3
    assert "일부만 검토: Big.java" in out["notice"]


# ---- 리뷰 의견(opinion): 결함이 아닌 자유 의견 ----
OPINION = {"type": "opinion", "category": "트랜잭션·동시성", "priority": "상", "file": "A.java", "line": 2,
           "evidence": "int grade = c.getGrade();",
           "reason": "REQUIRES_NEW 는 호출 트랜잭션과 롤백이 분리됩니다. 의도한 동작인지 확인하세요."}


def test_opinion_is_a_separate_status_and_needs_no_evidence():
    general = {"type": "opinion", "category": "전반", "priority": "중", "file": "", "reason": "전체적으로 무난합니다."}
    f, report = merged(json.dumps([OPINION, general]))
    assert [(x["type"], x["status"]) for x in f] == [("opinion", "의견")] * 2
    assert f[0]["category"] == "전반"                       # 총평이 맨 앞
    assert f[1]["line"] == 2 and f[1]["evidence"]           # 인용이 diff 에서 확인되면 그대로 유지
    assert "### 리뷰 의견" in report and "리뷰 의견 2건" in report and "`전체`" in report
    assert "지적 0건" in report                              # 의견은 결함 건수에 섞이지 않음


def test_invented_evidence_in_opinion_is_dropped_but_opinion_is_kept():
    f, _ = merged(json.dumps([dict(OPINION, evidence="없는 코드();", line=9)]))
    assert len(f) == 1 and f[0]["status"] == "의견"          # 의견 자체는 살리고
    assert f[0]["evidence"] == "" and f[0]["line"] is None   # 지어낸 인용과 줄번호는 보여주지 않음
    f, _ = merged(json.dumps([dict(OPINION, file="Ghost.java")]))  # diff 에 없는 파일이면 '전체' 의견
    assert f[0]["file"] == "" and f[0]["evidence"] == ""


def test_opinions_do_not_hide_or_replace_defects():
    f, report = merged(json.dumps([OPINION, ITEM]))
    assert [x["status"] for x in f] == ["지적", "의견"]       # 결함이 먼저
    assert "지적 1건" in report and "리뷰 의견 1건" in report
    f, report = merged(json.dumps([OPINION]))                 # 의견만 있어도 '결함 없음'이 분명히 보임
    assert "지적할 결함이 없습니다" in report


def test_opinions_sorted_by_priority_and_priority_aliases():
    items = [dict(OPINION, priority="하", line=None, evidence="", reason="a"),
             dict(OPINION, priority="high", line=None, evidence="", reason="b"),
             dict(OPINION, priority="중", line=None, evidence="", reason="c")]
    f, _ = merged(json.dumps(items))
    assert [x["priority"] for x in f] == ["상", "중", "하"]


def test_eval_ignores_opinions():
    from evals.run_dify_eval import score
    planted = [{"id": "a", "type": "logic", "file": "A.java", "line": 5}]
    caught, missed, fp = score(planted, [{"type": "logic", "file": "A.java", "line": 5},
                                         {"type": "opinion", "file": "A.java", "line": 5, "category": "성능"}])
    assert caught == ["a"] and fp == []                       # 의견이 오탐으로 세어지면 안 됨


def test_different_opinions_on_same_file_are_not_collapsed_but_exact_duplicates_are():
    a = dict(OPINION, line=None, evidence="", reason="의견 A")
    b = dict(OPINION, line=None, evidence="", reason="의견 B")
    assert len(merged(json.dumps([a, b]))[0]) == 2
    assert len(merged(json.dumps([a, dict(a)]))[0]) == 1


def test_evidence_is_matched_against_new_code_only():
    d = ("diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -1,2 +1,3 @@\n-old_call()\n"
         "+if a > b:\n+    return a\n keep()\n")
    out = parse_diff.main(d, "")
    multi = {"type": "logic", "file": "a.py", "evidence": "if a > b:\n    return a", "reason": "여러 줄 인용"}
    removed = {"type": "logic", "file": "a.py", "evidence": "old_call()", "reason": "삭제된 줄 인용"}
    res = merge.main(json.dumps([multi, removed]), "[]", out["files_json"], "")
    status = {x["reason"].split(" (")[0]: x["status"] for x in json.loads(res["findings_json"])}
    assert status == {"여러 줄 인용": "지적", "삭제된 줄 인용": "확인 필요"}
