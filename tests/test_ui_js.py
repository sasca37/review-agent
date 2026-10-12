"""화면의 diff 분류/파일 목록 로직(JS)을 Node 로 직접 실행해 확인한다. node 가 없으면 건너뜀."""
import json
import os
import re
import shutil
import subprocess

import pytest

from evals.run_eval import ROOT

TEMPLATE = os.path.join(ROOT, "web", "templates", "index.html")
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(not NODE, reason="node 가 없음")


def run_js(diff):
    src = open(TEMPLATE, encoding="utf-8").read()
    a, b = src.index("function classify(text){"), src.index("function shortDir")
    code = src[a:b]  # classify + fileStats (DOM 을 쓰지 않는 부분)
    js = code + "\nconst t=require('fs').readFileSync(0,'utf8');const c=classify(t);" \
                "console.log(JSON.stringify({files:fileStats(c),lines:c.map(x=>[x.c,x.path||'',x.no===undefined?null:x.no])}));"
    out = subprocess.run([NODE, "-e", js], input=diff, capture_output=True, text=True, check=True).stdout
    return json.loads(out)


def stats(diff):
    return [(f["path"], f["status"], f["add"], f["del"]) for f in run_js(diff)["files"]]


GIT = """diff --git a/src/A.java b/src/A.java
index 1..2 100644
--- a/src/A.java
+++ b/src/A.java
@@ -1,3 +1,3 @@
 class A {
-  int a;
+  int b;
 }
diff --git a/src/New.java b/src/New.java
new file mode 100644
index 0..3
--- /dev/null
+++ b/src/New.java
@@ -0,0 +1,2 @@
+class New {
+}
diff --git a/old/Gone.java b/old/Gone.java
deleted file mode 100644
index 3..0
--- a/old/Gone.java
+++ /dev/null
@@ -1,2 +0,0 @@
-class Gone {
-}
diff --git a/x/Before.java b/x/After.java
similarity index 90%
rename from x/Before.java
rename to x/After.java
index 3..4
--- a/x/Before.java
+++ b/x/After.java
@@ -1,1 +1,1 @@
-int p;
+int q;
"""


def test_git_diff_files_and_status():
    assert stats(GIT) == [("src/A.java", "modified", 1, 1), ("src/New.java", "added", 2, 0),
                          ("old/Gone.java", "deleted", 0, 2), ("x/After.java", "renamed", 1, 1)]


def test_added_status_does_not_leak_to_previous_file():
    # 'new file mode' 없이 '--- /dev/null' 만 있는 diff 에서 앞 파일이 '추가됨'으로 바뀌면 안 됨
    d = ("--- a/One.java\n+++ b/One.java\n@@ -1,1 +1,1 @@\n-a\n+b\n"
         "--- /dev/null\n+++ b/Two.java\n@@ -0,0 +1,1 @@\n+z\n")
    assert stats(d) == [("One.java", "modified", 1, 1), ("Two.java", "added", 1, 0)]


def test_same_file_in_several_commits_is_merged_in_list():
    d = GIT.split("diff --git a/src/New.java")[0] * 2  # 같은 파일을 두 번 이어 붙임 (커밋 2개)
    assert stats(d) == [("src/A.java", "modified", 2, 2)]


def test_svn_diff_and_line_numbers():
    d = ("Index: src/FeeCalculator.java\n===================================================================\n"
         "--- src/FeeCalculator.java\t(revision 1204)\n+++ src/FeeCalculator.java\t(working copy)\n"
         "@@ -10,2 +10,3 @@\n a\n+b\n c\n")
    r = run_js(d)
    assert [(f["path"], f["add"], f["del"]) for f in r["files"]] == [("src/FeeCalculator.java", 1, 0)]
    nums = [(c, no) for c, p, no in r["lines"] if c in ("ctx", "add")]
    assert nums == [("ctx", 10), ("add", 11), ("ctx", 12)]  # 신규 파일 기준 줄 번호


def test_every_line_belongs_to_a_file():
    r = run_js(GIT)
    assert all(p for c, p, no in r["lines"][:-1])  # 마지막 빈 줄 제외, 모든 줄이 파일에 소속
