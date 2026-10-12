def fmt(items, label):
    out = []
    for i, it in enumerate(items or []):
        c = it.get("content") if isinstance(it, dict) else it
        c = str(c or "").strip()
        if c:
            out.append("[%s %d]\n%s" % (label, i + 1, c))
    return out


def main(common: list, task_hits: list, task: str) -> dict:
    parts = fmt(task_hits, "업무 지식: " + (task or "미지정")) + fmt(common, "공통 점검 기준")
    text = "\n\n".join(parts) or "(검색된 참고 지식 없음)"
    return {"knowledge_text": text[:8000]}
