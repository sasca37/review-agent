from __future__ import annotations

from typing import List

from unidiff import PatchSet

from .models import DiffFile


def parse_diff(diff_text: str) -> List[DiffFile]:
    """unified diff 를 파일 단위로 파싱해 변경 파일 목록(diff list)을 만든다."""
    patch = PatchSet(diff_text)
    files: List[DiffFile] = []
    for pf in patch:
        if pf.is_added_file:
            status = "added"
        elif pf.is_removed_file:
            status = "deleted"
        elif pf.is_rename:
            status = "renamed"
        else:
            status = "modified"
        path = pf.target_file if status != "deleted" else pf.source_file
        path = _strip_prefix(path)

        added_lines, other = [], []
        for hunk in pf:
            for line in hunk:
                if line.is_added:
                    added_lines.append((line.target_line_no, line.value.rstrip("\n")))
                else:
                    other.append(line.value.rstrip("\n"))
        files.append(
            DiffFile(
                path=path,
                status=status,
                added=pf.added,
                removed=pf.removed,
                added_lines=added_lines,
                context_removed_text="\n".join(other),
                text=str(pf),
            )
        )
    return files


def _strip_prefix(p: str) -> str:
    if p.startswith(("a/", "b/")):
        return p[2:]
    return p
