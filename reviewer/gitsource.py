"""로컬 git 저장소에서 diff 를 가져온다."""
from __future__ import annotations

import subprocess
from typing import Optional


class GitError(Exception):
    pass


def get_diff(repo: str, base: Optional[str] = None, head: Optional[str] = None) -> str:
    """base 만 주면 `base` 대비 작업 트리, 둘 다 주면 base..head. 기본값은 HEAD~1..HEAD."""
    if not base:
        base, head = "HEAD~1", head or "HEAD"
    for ref in (base, head):
        if ref and ref.startswith("-"):
            raise GitError("잘못된 리비전 이름입니다.")
    args = ["git", "-C", repo, "diff", "--no-color", base]
    if head:
        args.append(head)
    args.append("--")
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError) as e:
        raise GitError(str(e))
    if r.returncode != 0:
        raise GitError(r.stderr.strip() or "git diff 실패")
    return r.stdout
