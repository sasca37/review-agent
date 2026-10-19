"""Dify Workflow API 호출. API Key 는 로그·응답에 남기지 않는다."""
from __future__ import annotations

import json
import os
import re
from typing import Dict, List, Optional
from urllib.parse import urlparse

import requests

CONNECT_TIMEOUT = 5
READ_TIMEOUT = 180


class DifyError(Exception):
    """화면에 그대로 보여줘도 되는 사용자용 오류 메시지."""


def normalize_base(url: str) -> str:
    url = (url or "").strip().rstrip("/")
    p = urlparse(url)
    if p.scheme not in ("http", "https") or not p.netloc:
        raise DifyError("API URL 은 http:// 또는 https:// 로 시작해야 합니다. 예) http://dify.example.com/v1")
    allowed = [h.strip() for h in os.getenv("DIFY_ALLOWED_HOSTS", "").split(",") if h.strip()]
    if allowed and p.hostname not in allowed:
        raise DifyError("허용되지 않은 호스트입니다. (서버의 DIFY_ALLOWED_HOSTS 설정 확인)")
    return url if re.search(r"/v1$", url) else url + "/v1"


def _headers(key: str) -> Dict[str, str]:
    if not key:
        raise DifyError("API Key 를 입력하세요.")
    return {"Authorization": "Bearer " + key, "Content-Type": "application/json"}


def _fail(resp: requests.Response) -> DifyError:
    try:
        msg = resp.json().get("message") or ""
    except ValueError:
        msg = ""
    hint = {
        401: "API Key 가 올바르지 않습니다. 워크플로 앱의 'API 접근'에서 발급한 키인지 확인하세요.",
        403: "접근이 거부되었습니다. 키 권한을 확인하세요.",
        404: "주소를 찾을 수 없습니다. API URL 이 맞는지(…/v1) 확인하세요.",
        429: "요청이 너무 많습니다. 잠시 뒤 다시 시도하세요.",
    }.get(resp.status_code)
    if resp.status_code in (301, 302, 307, 308):
        hint = "주소가 다른 곳으로 이동(리다이렉트)합니다. http/https 와 경로를 확인하세요."
    if hint is None:
        hint = "Dify 서버 오류입니다." if resp.status_code >= 500 else "요청이 거부되었습니다."
    return DifyError("%s (HTTP %d%s)" % (hint, resp.status_code, ": " + msg[:200] if msg else ""))


def _request(method: str, url: str, **kw):
    try:
        resp = requests.request(method, url, allow_redirects=False, **kw)
    except requests.exceptions.ConnectTimeout:
        raise DifyError("Dify 서버에 연결하지 못했습니다(시간 초과). 주소와 네트워크를 확인하세요.")
    except requests.exceptions.ReadTimeout:
        raise DifyError("Dify 응답이 %d초 안에 오지 않았습니다. 모델이 느리거나 diff 가 너무 클 수 있습니다." % READ_TIMEOUT)
    except requests.exceptions.SSLError:
        raise DifyError("SSL 인증서 검증에 실패했습니다. 사내 인증서 설정 또는 http 주소를 확인하세요.")
    except requests.exceptions.RequestException:
        raise DifyError("Dify 서버에 연결할 수 없습니다. 주소와 네트워크를 확인하세요.")
    if resp.status_code != 200:
        raise _fail(resp)
    return resp


def test_connection(base_url: str, key: str) -> Dict:
    base = normalize_base(base_url)
    resp = _request("GET", base + "/parameters", headers=_headers(key), timeout=(CONNECT_TIMEOUT, 15))
    try:
        form = resp.json().get("user_input_form") or []
    except ValueError:
        raise DifyError("Dify 가 아닌 응답입니다. API URL 을 확인하세요.")
    names: List[str] = []
    for item in form:
        for spec in item.values():
            if isinstance(spec, dict) and spec.get("variable"):
                names.append(spec["variable"])
    warning = None
    if "diff_text" not in names:
        warning = "이 앱의 입력 변수에 diff_text 가 없습니다 (현재: %s). 리뷰 워크플로 앱의 키인지 확인하세요." % (
            ", ".join(names) or "없음")
    elif "task" not in names:
        warning = "이 워크플로에는 업무명(task) 입력이 없어 업무별 지식 검색이 적용되지 않습니다. 최신 yml 을 가져오세요."
    return {"ok": True, "inputs": names, "warning": warning}


def run_review(base_url: str, key: str, diff_text: str, git_status: str = "", task: str = "",
               user: str = "review-agent") -> Dict:
    base = normalize_base(base_url)
    body = {"inputs": {"diff_text": diff_text, "git_status": git_status or "", "task": task},
            "response_mode": "blocking", "user": user}
    resp = _request("POST", base + "/workflows/run", headers=_headers(key), json=body,
                    timeout=(CONNECT_TIMEOUT, READ_TIMEOUT))
    try:
        data = resp.json()["data"]
    except (ValueError, KeyError):
        raise DifyError("Dify 응답을 해석하지 못했습니다. 워크플로 앱의 API 인지 확인하세요.")
    if data.get("status") != "succeeded":
        raise DifyError("워크플로 실행 실패: %s" % (str(data.get("error") or data.get("status"))[:500]))
    out = data.get("outputs") or {}
    if "findings_json" not in out:
        raise DifyError("워크플로 출력에 findings_json 이 없습니다. 종료 노드의 출력 구성을 확인하세요.")
    try:
        findings = json.loads(out["findings_json"])
    except (TypeError, ValueError):
        raise DifyError("findings_json 을 해석하지 못했습니다.")
    if not isinstance(findings, list) or not all(isinstance(f, dict) for f in findings):
        raise DifyError("findings_json 형식이 올바르지 않습니다 (항목 목록이어야 합니다). 워크플로 출력을 확인하세요.")
    return {"findings": findings, "notes": _notes_from_report(out.get("report", "")),
            "elapsed": data.get("elapsed_time")}


def _notes_from_report(report: str) -> List[str]:
    m = re.search(r"### 안내\n(.*?)(?:\n\n|\Z)", report or "", re.S)
    if not m:
        return []
    return [l[2:].strip() for l in m.group(1).splitlines() if l.startswith("- ")]
