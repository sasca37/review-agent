"""LLM 로직 점검. OpenAI 호환 엔드포인트(LLM_BASE_URL/LLM_API_KEY/LLM_MODEL)를 사용한다.
설정이 없으면 비활성화되어 규칙 기반 점검만 수행된다."""
from __future__ import annotations

import json
import os
import re
from typing import List, Optional

from .models import DiffFile, Finding, LOGIC, TYPO, MISSING_FILE, FOUND, NEEDS_CHECK

MAX_CHARS = 12000

SYSTEM = """당신은 형상 결재 전 소스 변경분을 1차 점검하는 코드 리뷰어입니다.
점검 대상은 세 가지뿐입니다: 로직 오류(logic), 오타(typo), 미반영 파일(missing_file).
규칙:
- diff 에 실제로 보이는 줄만 근거로 삼고, evidence 에는 해당 줄을 그대로 인용하세요.
- 근거가 불충분하거나 diff 밖의 정보가 필요하면 needs_check 를 true 로 하세요.
- 문제가 없으면 빈 배열을 반환하세요. 없는 문제를 만들지 마세요. 스타일·취향 지적은 금지입니다.
출력은 JSON 배열만: [{"type":"logic|typo|missing_file","line":<신규 파일 기준 줄번호 또는 null>,
"evidence":"<diff 의 해당 줄 원문>","reason":"<한국어 설명>","needs_check":<true|false>}]"""


class LLMReviewer:
    def __init__(self, base_url: str, api_key: str, model: str):
        from openai import OpenAI  # 지연 import

        self.client = OpenAI(base_url=base_url, api_key=api_key or "none")
        self.model = model

    @classmethod
    def from_env(cls) -> Optional["LLMReviewer"]:
        url, model = os.getenv("LLM_BASE_URL"), os.getenv("LLM_MODEL")
        if not (url and model):
            return None
        return cls(url, os.getenv("LLM_API_KEY", ""), model)

    def review_file(self, f: DiffFile) -> tuple:
        """(findings, note) 반환. 호출 실패 시 note 로 사유를 남긴다."""
        text = f.text
        note = None
        if len(text) > MAX_CHARS:
            text = text[:MAX_CHARS]
            note = f"{f.path}: diff 가 길어 앞 {MAX_CHARS}자만 LLM 이 검토했습니다."
        try:
            resp = self.client.chat.completions.create(
                model=self.model,
                temperature=0,
                messages=[
                    {"role": "system", "content": SYSTEM},
                    {"role": "user", "content": f"파일: {f.path}\n\n{text}"},
                ],
            )
            items = _parse_json(resp.choices[0].message.content or "")
        except Exception as e:  # 네트워크·파싱 오류는 점검 실패로 알리고 계속 진행
            return [], f"{f.path}: LLM 점검 실패 ({type(e).__name__}: {e})"
        out: List[Finding] = []
        for it in items:
            t = it.get("type")
            if t not in (LOGIC, TYPO, MISSING_FILE):
                continue
            out.append(Finding(
                t, f.path, it.get("line") if isinstance(it.get("line"), int) else None,
                str(it.get("evidence", "")).strip(), str(it.get("reason", "")).strip(),
                status=NEEDS_CHECK if it.get("needs_check") else FOUND, source="llm",
            ))
        return out, note


def _parse_json(s: str) -> list:
    m = re.search(r"\[.*\]", s, re.S)
    if not m:
        return []
    data = json.loads(m.group(0))
    return [d for d in data if isinstance(d, dict)]
