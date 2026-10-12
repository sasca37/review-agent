from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import List, Optional

LOGIC, TYPO, MISSING_FILE, OPINION = "logic", "typo", "missing_file", "opinion"
TYPE_LABEL = {LOGIC: "로직 오류", TYPO: "오타", MISSING_FILE: "미반영 파일", OPINION: "의견"}

FOUND, NEEDS_CHECK, OPINION_STATUS = "지적", "확인 필요", "의견"


@dataclass
class DiffFile:
    path: str
    status: str  # added / modified / deleted / renamed
    added: int
    removed: int
    # (신규 파일 기준 줄번호, 내용)
    added_lines: List[tuple] = field(default_factory=list)
    context_removed_text: str = ""  # 컨텍스트·삭제 줄 (오타 비교용)
    text: str = ""  # 이 파일의 diff 원문 (근거 검증용)


@dataclass
class Finding:
    type: str
    file: str
    line: Optional[int]
    evidence: str
    reason: str
    status: str = FOUND  # 지적 | 확인 필요
    source: str = "rule"  # rule | llm

    def to_dict(self) -> dict:
        d = asdict(self)
        d["type_label"] = TYPE_LABEL.get(self.type, self.type)
        return d


@dataclass
class ReviewResult:
    files: List[DiffFile]
    findings: List[Finding]
    notes: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "files": [
                {"path": f.path, "status": f.status, "added": f.added, "removed": f.removed}
                for f in self.files
            ],
            "findings": [f.to_dict() for f in self.findings],
            "notes": self.notes,
        }
