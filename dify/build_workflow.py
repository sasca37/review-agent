"""dify/nodes/*.py 와 프롬프트로 Dify Workflow DSL(review-agent.yml)을 만든다.

사용: python dify/build_workflow.py [--keep dify/review-agent.yml]
--keep 을 주면 기존 파일에서 Dify 에서 지정한 설정(앱 이름, 의존성, 모델, 지식 데이터셋·리랭커)을 이어받는다.
"""
import argparse
import copy
import os
import sys

import yaml

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from reviewer.tasks import TASK_SLUGS, TASKS  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))

SYSTEM = """당신은 운영 중인 서비스의 소스 변경분을 형상 결재 전에 1차 검토하는 시니어 코드 리뷰어입니다.
결과는 두 종류입니다.
A. 결함 지적 (type: logic | typo | missing_file): 실제로 틀렸거나 빠졌다고 판단되는 것
B. 리뷰 의견 (type: opinion): 결함이 아니더라도 운영 소스 관점에서 짚어 줄 만한 의견·개선 제안. 자유롭게 쓰세요.

[A. 결함 점검 순서]
변경된 줄('+' 로 표시된 줄)을 하나씩 읽으며 아래 항목을 차례로 확인하세요.
- 경계값: 비교 연산자(> >= < <=)가 주석·이름이 말하는 기준(이상/초과/이하/미만)과 같은가
- null/빈값: 조회 결과나 입력을 검사 없이 바로 쓰는가
- 금액·수량 계산: 수수료·세금 같은 부가 값이 확인·차감·비교에 일관되게 반영되는가, float/double 을 쓰는가
- 분기·반환: 모든 경우에 값을 반환하는가, 새 값이 분기에 빠져 있지 않은가
- 예외 처리: 빈 catch/except 로 오류를 삼키지 않는가
- 문자열 비교: Java 에서 == 로 문자열을 비교하지 않는가
- 반복 범위: 인덱스·종료 조건이 한 칸 어긋나지 않는가
- 오타: 식별자·메시지·상수 철자
- 미반영 파일: 시그니처·참조가 바뀌었는데 함께 바뀌어야 할 파일이 변경분에 없는가

[B. 리뷰 의견 가이드]
이 변경이 운영 환경에서 어떻게 동작할지 생각하며 자유롭게 의견을 내세요. 예를 들면 다음과 같은 관점입니다.
- 설계·구조: 책임 분리, 중복 코드, 결합도, 기존 코드와의 일관성
- 트랜잭션·동시성: 전파 속성(REQUIRES_NEW 등)과 롤백 범위, 락, 중복 요청·재처리(멱등성)
- 성능: 반복문 안의 DB·외부 호출(N+1), 불필요한 조회, 큰 데이터 처리
- 보안·개인정보: 입력 검증, SQL 문자열 조합, 민감정보의 로그·응답 노출, 권한 확인
- 예외·로깅: 예외 처리 방식, 로그 수준과 내용, 장애 시 원인 추적 가능성
- 가독성·유지보수: 이름, 매직 넘버, 지나치게 긴 메서드, 주석
- 테스트·운영: 테스트가 필요한 지점, 배포·롤백 시 영향, 설정·데이터 변경 필요 여부
의견마다 다음을 쓰세요.
- category: 위 관점 중 하나 (설계 / 트랜잭션·동시성 / 성능 / 보안 / 예외·로깅 / 가독성 / 테스트·운영 / 기타)
- priority: 상 / 중 / 하
- 근거가 되는 줄이 있으면 file, line, evidence 에 적으세요. 특정 줄이 아닌 의견이면 생략해도 됩니다.
- reason: 왜 그렇게 생각하는지와 구체적인 개선 방법. 확신이 없으면 '~일 수 있습니다'처럼 표현하세요.
중요한 것부터 최대 10개까지만 쓰고, 우선순위가 낮은 취향 문제는 쓰지 마세요.
마지막에 변경 전체에 대한 총평을 type 'opinion', category '전반', file '' 인 항목 1건으로 한두 문장 쓰세요.

[출력 방식]
1) 먼저 <analysis> 태그 안에 변경 내용을 요약하고 의심 가는 줄을 간단히 적으세요 (최대 15줄).
2) 그 다음 JSON 배열만 출력하세요. JSON 밖에는 설명 문장을 쓰지 마세요.

[규칙]
- 결함 지적(A)은 diff 에 실제로 보이는 줄만 근거로 삼고, evidence 에 해당 줄 내용을 그대로 인용하세요 (줄번호·+/- 기호 제외). 근거 줄을 인용할 수 없는 결함은 쓰지 마세요.
- line 은 입력에 표시된 신규 파일 기준 줄번호입니다. 삭제된 줄이면 null 입니다.
- 결함이 확실하지 않아도 근거 줄이 있고 의심스러우면 needs_check 를 true 로 해서 포함하세요.
- 이미 규칙 기반 점검이 찾은 후보와 같은 내용은 다시 쓰지 마세요.
- '업무 지식'은 해당 업무의 도메인 규칙이고 '공통 점검 기준'은 모든 업무에 쓰는 체크리스트입니다. 참고 지식은 점검 관점을 알려 줄 뿐입니다. 지식에 있다는 이유만으로 지적하지 말고, 지식의 예시 코드를 근거로 인용하지 마세요.
- 실제 사내 업무 규칙은 알 수 없으므로 업무 프로세스 적합성은 단정하지 말고 의견이나 needs_check 로 표시하세요.
- 입력이 중간에서 잘렸다는 표시가 있으면 잘린 부분은 판단하지 마세요.

JSON 형식 (필드는 해당할 때만):
[{"type":"logic|typo|missing_file|opinion","category":"<opinion 일 때 분류>","priority":"<opinion 일 때 상|중|하>","file":"<파일 경로, 전체 의견이면 빈 문자열>","line":<숫자 또는 null>,"evidence":"<diff 의 해당 줄 원문, 없으면 빈 문자열>","reason":"<한국어 설명>","needs_check":<true|false>}]"""


USER = """업무: {{#start.task#}}

변경분 (줄 앞 숫자는 신규 파일 기준 줄번호, '+' 추가, '-' 삭제):

{{#parse.llm_input#}}

이미 규칙 기반 점검이 찾은 후보 (중복 제외용):
{{#parse.rule_findings_json#}}

참고 지식 (업무 지식 + 공통 점검 기준. 샘플 규칙이며 무엇을 볼지 알려 주는 참고용입니다. 지적의 근거는 반드시 위 변경분에서 인용):
{{#know.knowledge_text#}}"""


def code(name):
    with open(os.path.join(HERE, "nodes", name), encoding="utf-8") as f:
        return f.read()


def node(i, ident, data, desc=""):
    return {"id": ident, "type": "custom", "position": {"x": i * 340, "y": 200},
            "positionAbsolute": {"x": i * 340, "y": 200}, "width": 260, "height": 100,
            "sourcePosition": "right", "targetPosition": "left",
            "data": {"desc": desc, "selected": False, **data}}


def var(name, node_id, key):
    return {"variable": name, "value_selector": [node_id, key], "value_type": "string"}


def outs(*names):
    return {n: {"type": "string", "children": None} for n in names}


def code(name):
    with open(os.path.join(HERE, "nodes", name), encoding="utf-8") as f:
        return f.read()


def node(i, ident, data, desc="", pos=None):
    x, y = pos or (i * 340, 200)
    return {"id": ident, "type": "custom", "position": {"x": x, "y": y}, "positionAbsolute": {"x": x, "y": y},
            "width": 260, "height": 100, "sourcePosition": "right", "targetPosition": "left",
            "data": {"desc": desc, "selected": False, **data}}


def var(name, node_id, key, vtype="string"):
    return {"variable": name, "value_selector": [node_id, key], "value_type": vtype}


def outs(*names):
    return {n: {"type": "string", "children": None} for n in names}


KB_TITLE = "공통 점검 기준 검색"
KB_RETRIEVAL = {"retrieval_mode": "multiple",
                "multiple_retrieval_config": {"top_k": 4, "score_threshold": None, "reranking_enable": False}}


def build(keep=None):
    """keep: 기존 yml(dict). 있으면 앱 이름·의존성·모델·지식 설정을 그대로 이어받는다."""
    task_kb = [("kb_" + slug, task) for task, slug in TASK_SLUGS]
    cases = [{"case_id": slug, "id": slug, "logical_operator": "and",
              "conditions": [{"id": "cond-" + slug, "variable_selector": ["start", "task"],
                              "comparison_operator": "is", "value": task, "varType": "string"}]}
             for task, slug in TASK_SLUGS[:2]]  # 나머지(업무상담)는 else 로 처리

    def kb_node(title, desc):
        return {"type": "knowledge-retrieval", "title": title, "query_variable_selector": ["parse", "kb_query"],
                "dataset_ids": [], **copy.deepcopy(KB_RETRIEVAL)}, desc

    defs = [
        ("start", {"type": "start", "title": "변경분 입력", "variables": [
            {"variable": "task", "label": "업무명", "type": "select", "required": True,
             "options": list(TASKS), "max_length": 48},
            {"variable": "diff_text", "label": "변경 diff (git diff 결과)", "type": "paragraph",
             "required": True, "max_length": 200000, "options": []},
            {"variable": "git_status", "label": "git status --porcelain 결과 (선택, 미반영 파일 점검용)",
             "type": "paragraph", "required": False, "max_length": 20000, "options": []}]},
         "개발 반영 후 형상 등록 전, 업무를 고르고 결재 대상 diff 를 입력합니다.", (0, 220)),
        ("parse", {"type": "code", "title": "diff list 정리·규칙 점검", "code_language": "python3",
                   "code": code("parse_diff.py"),
                   "variables": [var("diff_text", "start", "diff_text"), var("git_status", "start", "git_status")],
                   "outputs": outs("files_table", "llm_input", "rule_findings_json", "files_json", "notice", "kb_query")},
         "변경 파일 목록을 만들고 오타·미반영 파일 후보를 규칙으로 찾습니다.", (340, 220)),
        ("kb", *kb_node(KB_TITLE, "모든 업무에 쓰는 점검 체크리스트·오류 사례를 찾습니다. 가져온 뒤 공통 지식 데이터셋을 선택하세요.")[:1],
         "모든 업무에 쓰는 점검 체크리스트·오류 사례를 찾습니다. 가져온 뒤 공통 지식 데이터셋을 선택하세요.", (680, 220)),
        ("branch", {"type": "if-else", "title": "업무별 지식 선택", "cases": cases},
         "선택한 업무에 맞는 지식 데이터셋으로 분기합니다. (설문조사 / 민원 / 그 외=업무상담)", (1020, 220)),
    ]
    ys = {"survey": 40, "complaint": 220, "consult": 400}
    for ident, task in task_kb:
        slug = ident[3:]
        defs.append((ident, kb_node("업무 지식 검색 - " + task, "")[0],
                     "%s 전용 지식(업무 규칙·이전 리뷰 기록) 데이터셋을 선택하세요." % task, (1360, ys[slug])))
    defs += [
        ("agg", {"type": "variable-aggregator", "title": "업무 지식 모으기", "output_type": "array[object]",
                 "variables": [[i, "result"] for i, _ in task_kb],
                 "advanced_settings": {"group_enabled": False, "groups": []}},
         "분기 중 실행된 업무 지식 검색 결과를 하나로 모읍니다.", (1700, 220)),
        ("know", {"type": "code", "title": "참고 지식 합치기", "code_language": "python3",
                  "code": code("merge_knowledge.py"),
                  "variables": [var("common", "kb", "result", "array[object]"),
                                var("task_hits", "agg", "output", "array[object]"),
                                var("task", "start", "task")],
                  "outputs": outs("knowledge_text")},
         "업무 지식과 공통 점검 기준을 하나의 참고 텍스트로 만듭니다.", (2040, 220)),
        ("llm", {"type": "llm", "title": "오류 후보 1차 크로스체크",
                 "model": {"provider": "langgenius/openai_api_compatible/openai_api_compatible",
                           "name": "REPLACE_WITH_MODEL", "mode": "chat",
                           "completion_params": {"temperature": 0}},
                 "prompt_template": [
                     {"id": "sys", "role": "system", "text": SYSTEM},
                     {"id": "usr", "role": "user", "text": USER}],
                 "context": {"enabled": False, "variable_selector": []},
                 "vision": {"enabled": False}, "memory": None, "variables": []},
         "로직 오류·오타·미반영 파일 후보를 근거와 함께 제안합니다. 모델은 임포트 후 선택하세요.", (2380, 220)),
        ("merge", {"type": "code", "title": "근거 검증·결과 정리", "code_language": "python3",
                   "code": code("merge_findings.py"),
                   "variables": [var("llm_text", "llm", "text"),
                                 var("rule_findings_json", "parse", "rule_findings_json"),
                                 var("files_json", "parse", "files_json"), var("notice", "parse", "notice")],
                   "outputs": outs("report", "findings_json")},
         "인용한 근거가 diff 에 없으면 '확인 필요'로 내리고 결과를 합칩니다.", (2720, 220)),
        ("end", {"type": "end", "title": "점검 결과", "outputs": [
            {"variable": "files_table", "value_selector": ["parse", "files_table"], "value_type": "string"},
            {"variable": "report", "value_selector": ["merge", "report"], "value_type": "string"},
            {"variable": "findings_json", "value_selector": ["merge", "findings_json"], "value_type": "string"}]},
         "수정 여부는 개발자가 정합니다. ③ 동료 코드리뷰는 유지됩니다.", (3060, 220)),
    ]
    nodes = [node(i, ident, data, desc, pos) for i, (ident, data, desc, pos) in enumerate(defs)]
    types = {ident: data["type"] for ident, data, _, _ in defs}

    def edge(a, b, handle="source"):
        return {"id": "%s-%s-%s" % (a, handle, b), "source": a, "target": b, "sourceHandle": handle,
                "targetHandle": "target", "type": "custom",
                "data": {"sourceType": types[a], "targetType": types[b], "isInIteration": False}}

    edges = [edge("start", "parse"), edge("parse", "kb"), edge("kb", "branch"),
             edge("branch", "kb_survey", "survey"), edge("branch", "kb_complaint", "complaint"),
             edge("branch", "kb_consult", "false"),  # else = 업무상담
             edge("kb_survey", "agg"), edge("kb_complaint", "agg"), edge("kb_consult", "agg"),
             edge("agg", "know"), edge("know", "llm"), edge("llm", "merge"), edge("merge", "end")]
    app = {
        "version": "0.7.0", "kind": "app",
        "app": {"name": "소스코드 변경분 사전 리뷰", "mode": "workflow", "icon": "🔍", "icon_type": "emoji",
                "icon_background": "#E0F2FE", "use_icon_as_answer_icon": False,
                "description": "형상 결재 전 diff 를 정리하고 로직 오류·오타·미반영 파일 후보를 근거와 함께 1차 크로스체크"},
        "dependencies": [],
        "workflow": {
            "conversation_variables": [], "environment_variables": [],
            "features": {"file_upload": {"enabled": False}, "opening_statement": "",
                         "retriever_resource": {"enabled": False}, "sensitive_word_avoidance": {"enabled": False},
                         "speech_to_text": {"enabled": False}, "suggested_questions": [],
                         "suggested_questions_after_answer": {"enabled": False}, "text_to_speech": {"enabled": False}},
            "graph": {"nodes": nodes, "edges": edges, "viewport": {"x": 0, "y": 0, "zoom": 0.6}}}}
    return inherit(app, keep) if keep else app


def inherit(app, keep):
    """기존 yml 에서 Dify 로 지정한 값을 새 구조에 이어붙인다 (모델, 지식 데이터셋, 리랭커, 앱 이름 등)."""
    for k in ("app", "dependencies", "version", "kind"):
        if k in keep:
            app[k] = copy.deepcopy(keep[k])
    for k, v in keep.get("workflow", {}).items():
        if k != "graph":
            app["workflow"][k] = copy.deepcopy(v)
    old = {n["id"]: n["data"] for n in keep["workflow"]["graph"]["nodes"]}
    new = {n["id"]: n["data"] for n in app["workflow"]["graph"]["nodes"]}
    if "llm" in old:
        new["llm"]["model"] = copy.deepcopy(old["llm"]["model"])
    kb_old = old.get("kb")
    if kb_old:
        for key in ("dataset_ids", "multiple_retrieval_config", "retrieval_mode", "query_attachment_selector"):
            if key in kb_old:
                new["kb"][key] = copy.deepcopy(kb_old[key])
        for ident in ("kb_" + slug for _, slug in TASK_SLUGS):  # 업무별 노드는 검색 설정만 공통과 같게, 데이터셋은 비움
            for key in ("multiple_retrieval_config", "retrieval_mode", "query_attachment_selector"):
                if key in kb_old:
                    new[ident][key] = copy.deepcopy(kb_old[key])
    for ident in ("kb_" + slug for _, slug in TASK_SLUGS):  # 이미 업무별로 지정해 둔 데이터셋이 있으면 유지
        if ident in old and old[ident].get("dataset_ids"):
            new[ident]["dataset_ids"] = list(old[ident]["dataset_ids"])
    return app


def _str(dumper, data):
    style = "|" if "\n" in data else None
    return dumper.represent_scalar("tag:yaml.org,2002:str", data, style=style)


yaml.add_representer(str, _str, Dumper=yaml.SafeDumper)

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--keep", help="Dify 설정을 이어받을 기존 yml")
    ap.add_argument("--out", default=os.path.join(HERE, "review-agent.yml"))
    a = ap.parse_args()
    kept = yaml.safe_load(open(a.keep, encoding="utf-8")) if a.keep else None
    with open(a.out, "w", encoding="utf-8") as f:
        yaml.dump(build(kept), f, Dumper=yaml.SafeDumper, allow_unicode=True, sort_keys=False, width=1000)
    print(a.out)
