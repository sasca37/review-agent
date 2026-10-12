"""samples/sampleN 에서 Dify/웹에 붙여넣을 diff 파일을 만든다.
출력: samples/diffs/sampleN.diff (+ 미반영 파일 점검용 sampleN.git-status.txt)"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile

from evals.run_eval import ROOT, build_repo


def main():
    out = os.path.join(ROOT, "samples", "diffs")
    os.makedirs(out, exist_ok=True)
    for name in sorted(os.listdir(os.path.join(ROOT, "samples"))):
        sample = os.path.join(ROOT, "samples", name)
        if not name.startswith("sample") or not os.path.isfile(os.path.join(sample, "expected.json")):
            continue
        exp = json.load(open(os.path.join(sample, "expected.json"), encoding="utf-8"))
        tmp = tempfile.mkdtemp()
        try:
            build_repo(sample, tmp)
            run = lambda *a: subprocess.run(["git", "-C", tmp, *a], capture_output=True, text=True, check=True).stdout
            open(os.path.join(out, name + ".diff"), "w", encoding="utf-8").write(
                run("diff", "HEAD", "--", *exp["diff_paths"]))
            open(os.path.join(out, name + ".git-status.txt"), "w", encoding="utf-8").write(
                run("status", "--porcelain"))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        print(name)


if __name__ == "__main__":
    main()
