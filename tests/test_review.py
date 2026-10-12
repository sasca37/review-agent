import os
import subprocess

from evals.run_eval import ROOT, evaluate
from reviewer.diff_parser import parse_diff
from reviewer.llm import LLMReviewer
from reviewer.models import LOGIC, NEEDS_CHECK, FOUND
from reviewer.service import review
from web.app import create_app

DIFF = """diff --git a/a.py b/a.py
index 111..222 100644
--- a/a.py
+++ b/a.py
@@ -1,2 +1,4 @@
 def total(items):
-    return sum(items)
+    result = sum(items)
+    result += 1
+    return reslt
"""

CLEAN = """diff --git a/a.py b/a.py
index 111..222 100644
--- a/a.py
+++ b/a.py
@@ -1,2 +1,2 @@
 def total(items):
-    return sum(items)
+    return sum(i for i in items)
"""


def test_diff_list_matches_diff():
    files = parse_diff(DIFF)
    assert [(f.path, f.status, f.added, f.removed) for f in files] == [("a.py", "modified", 3, 1)]


def test_sample_rules_find_typo_and_missing_files():
    r = evaluate(os.path.join(ROOT, "samples", "sample1"))
    assert {"typo-amuont", "missing-fee", "missing-limits"} <= set(r["caught"])
    assert r["missed"] == ["logic-balance"]  # LLM 없이는 로직 오류를 못 잡는 것이 정상
    assert r["false_positives"] == []


def test_no_invented_findings_on_clean_diff():
    assert review(CLEAN).findings == []


def test_common_misspelling_flagged():
    d = CLEAN.replace("sum(i for i in items)", "lenght(items)")
    assert any(f.type == "typo" for f in review(d).findings)


class FakeLLM:
    """LLM 응답을 흉내: 하나는 diff 에 있는 근거, 하나는 지어낸 근거."""

    def review_file(self, f):
        from reviewer.models import Finding
        return [
            Finding(LOGIC, f.path, 2, "return sum(i for i in items)", "예시", FOUND, "llm"),
            Finding(LOGIC, f.path, 9, "x = nothing_here()", "지어낸 근거", FOUND, "llm"),
        ], None


def test_ungrounded_llm_finding_downgraded():
    r = review(CLEAN, llm=FakeLLM())
    by_ev = {f.evidence: f.status for f in r.findings if f.type == LOGIC}
    assert by_ev["return sum(i for i in items)"] == FOUND
    assert by_ev["x = nothing_here()"] == NEEDS_CHECK


def test_all_samples_rule_only_baseline():
    """규칙 기반(LLM 없음) 기준선. LLM 필요 항목은 놓치는 것이 정상이고, 지어낸 지적은 0이어야 한다."""
    expected_caught = {
        1: {"typo-amuont", "missing-fee", "missing-limits"},
        2: set(), 3: set(),
        4: {"typo-ident", "missing-caller", "missing-xml"},
        5: set(),
    }
    for n, caught in expected_caught.items():
        r = evaluate(os.path.join(ROOT, "samples", f"sample{n}"))
        assert set(r["caught"]) == caught, n
        assert r["false_positives"] == [], n
