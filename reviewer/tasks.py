"""업무(서비스) 목록. 웹 화면과 Dify 워크플로 생성이 같은 목록을 쓴다. 순서: (업무명, 지식 폴더 이름)"""
TASK_SLUGS = [
    ("설문조사 서비스", "survey"),
    ("민원 서비스", "complaint"),
    ("업무상담 서비스", "consult"),
]
TASKS = [name for name, _ in TASK_SLUGS]
