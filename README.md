# review-agent — 형상 결재 전 소스 변경분 사전 리뷰

①개발 반영 후 ②형상 등록 전에 변경분(diff list)을 정리하고, 로직 오류·오타·미반영 파일 후보를
근거와 함께 1차 크로스체크하는 웹 도구입니다. 수정 여부는 개발자가 정하고 ③동료 리뷰는 그대로 진행합니다.
(과제 진단서: 소스코드 리뷰 에이전트)

## 구성
- `reviewer/` 핵심 로직: diff 파싱, 규칙 기반 점검(오타·미반영 파일), LLM 로직 점검, 근거 검증
- `web/` Flask 화면·API (`POST /api/review`)
- `evals/run_eval.py` 심어 둔 오류 놓친 수 / 지어낸 지적 수 측정 (샘플: `samples/sample1`)
- `config/pair_rules.yaml` 팀 규칙용 "함께 변경 파일 쌍" (비어 있음)
- 사용 라이브러리는 모두 `docs/python-library.txt` 허용 목록 내 (flask, unidiff, openai, rapidfuzz, pyyaml, pytest)

## 화면 사용
1. `python -m web.app` 후 http://127.0.0.1:5050
2. 왼쪽 메뉴 **설정**에서 API URL(`http://호스트/v1`)과 워크플로 앱의 API Key 입력 → **연결 테스트**
   (앱 입력 변수에 `diff_text` 가 없으면 경고). 서버 기본값은 환경변수 `DIFY_API_URL`, `DIFY_API_KEY`. 홈 화면에는 URL/Key 가 보이지 않고 '설정됨' 여부만 표시
   홈의 **업무**(설문조사·민원·업무상담 서비스)를 고르면 그 업무의 이전 요청 기록이 오른쪽 '최근 리뷰'에 표시되고, 눌러서 diff 와 결과를 다시 열 수 있음
   기록은 서버의 `data/history.db`(SQLite, 업무당 최근 300건)에 diff·결과가 저장되며 API Key 는 저장하지 않음. 업무 목록은 `web/app.py` 의 `TASKS`
3. 왼쪽에 `git diff` 를 붙여넣기 / 파일 업로드 / 로컬 저장소에서 가져오기 / **GitHub 커밋 목록에서 골라 불러오기**(`owner/repo` 입력 → 커밋 여러 개 선택(Shift+클릭 범위, 최대 20개) → 오래된 순으로 합쳐 한 번에 점검; 비공개 저장소·호출 한도 초과 시 설정에 토큰, 환경변수 `GITHUB_TOKEN` 도 가능; 외부망 전용). `+`·`-`·`@@` 줄은 자동 하이라이트,
   점검 후 지적된 줄은 테두리로 표시되고 결과의 위치 링크로 이동
   diff 를 넣으면 입력창 위에 **변경 파일 목록**(폴더별 묶음, +/- 줄 수, 점검 후 파일별 지적 건수)이 나오고, 파일을 누르면 그 파일의 변경만 따로 볼 수 있음(다시 누르거나 '전체 diff 보기'로 복귀)
4. **AI 코드 리뷰 시작**. 예제 불러오기에서 `samples/diffs` 의 샘플을 바로 쓸 수 있음 (`python -m evals.make_diffs` 로 생성)
- API Key 는 기본적으로 세션에만 보관(체크 시 브라우저 저장)하고 응답·로그에 남기지 않음
- 서버가 접속할 수 있는 Dify 호스트를 제한하려면 `DIFY_ALLOWED_HOSTS=dify.internal,...`

## 실행
    python -m venv .venv && .venv/bin/pip install -r requirements.txt
    .venv/bin/python -m web.app          # http://127.0.0.1:5050
    .venv/bin/python -m pytest -q
    .venv/bin/python -m evals.run_eval

## LLM 연결
`.env.example` 참고. `LLM_BASE_URL`, `LLM_MODEL`(, `LLM_API_KEY`)을 환경변수로 설정하면 OpenAI 호환
엔드포인트로 로직 점검을 수행합니다. 미설정 시 규칙 기반 점검만 돌고 화면에 그 사실이 표시됩니다.
LLM 지적은 인용한 근거가 diff 에 실제로 없으면 자동으로 '확인 필요'로 내려갑니다.

## 현재 한계
- 로직 오류 점검은 LLM 연결 시에만 가능 (샘플에서 규칙만으로는 3/4 검출, 로직 1건 미검출)
- 업무별 이전 소스·리뷰 기록 RAG 는 미구현 (사내 자료 반출 불가, 교육에서는 효과 확인 불가)
- 폐쇄망 모델·허용 라이브러리 최종 확인은 인프라 담당 확인 필요

## 소스 코드 호출 방법 
- git diff HEAD~1 | pbcopy
## Dify 워크플로
`dify/review-agent.yml` (Workflow, DSL 0.7.0). 재생성: `python dify/build_workflow.py`
흐름: 입력(diff, 선택: git status) → diff list 정리·규칙 점검(Code) → 오류 후보 크로스체크(LLM) → 근거 검증·결과 정리(Code) → 출력
1. Dify 에서 DSL 가져오기 → LLM 노드의 모델을 폐쇄망 모델로 선택 (`REPLACE_WITH_MODEL` 자리표시)
2. 지식 데이터셋 4개(공통 + 업무별 3) 연결: `dify/knowledge-guide.md`
3. 실행 입력: 업무명(`task`, 선택형), `git diff` 결과, (선택) `git status --porcelain` 결과.
   흐름: 입력 → diff 정리 → 공통 지식 검색 → **업무 분기 → 업무별 지식 검색** → 합치기 → LLM → 근거 검증 → 출력
   재생성: `python dify/build_workflow.py --keep dify/review-agent.yml` (Dify 에서 지정한 모델·지식·리랭커 설정 유지)
4. Code 노드는 표준 라이브러리만 사용 (Dify 샌드박스에 rapidfuzz·unidiff 없음). 로직은 `dify/nodes/*.py`
