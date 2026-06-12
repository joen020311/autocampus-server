# ─────────────────────────────────────────────────────────────
#  AutoCampus 서버  (영구 저장판: GitHub Gist)
#  - GET  /          서버 상태
#  - GET  /events    저장된 일정(JSON)  ← 앱이 받아감
#  - POST /events    일정 업로드 (X-Token)  ← PC 스크래퍼가 올림
#
#  ★ Render 무료 플랜은 잠시 쉬면 컨테이너가 내려갔다 올라오며 파일이 초기화됨.
#    그래서 일정을 'GitHub Gist' 에 저장해 재시작/재배포에도 안 사라지게 함.
#
#  ── Render 환경변수(Environment) 3개만 넣으면 끝 ────────────────
#    OC_TOKEN       = joen02031              (스크래퍼 SERVER_TOKEN 과 동일)
#    GIST_ID        = (아래 1회 준비에서 만든 gist id)
#    GITHUB_TOKEN   = (gist 권한 PAT)
#
#  ── Gist 1회 준비 ───────────────────────────────────────────
#    1) https://gist.github.com 에서 새 secret gist 생성:
#         파일명 events.json,  내용 {}  →  Create secret gist
#         주소 .../gists/abc123...  의 'abc123...' 가 GIST_ID
#    2) https://github.com/settings/tokens  →  Fine-grained token 생성:
#         Account permissions → Gists: Read and write  →  토큰 복사 = GITHUB_TOKEN
#    3) Render → 서비스 → Environment 에 위 3개 추가 → 재배포
#
#  (환경변수가 없으면 예전처럼 로컬 파일에 저장 = 재시작 시 사라짐)
#  실행: uvicorn app:app --host 0.0.0.0 --port $PORT
# ─────────────────────────────────────────────────────────────
import json
import os
import urllib.request
from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import JSONResponse

app = FastAPI(title="AutoCampus Server")

TOKEN = os.environ.get("OC_TOKEN", "change-me")
GIST_ID = os.environ.get("GIST_ID", "")
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
GIST_FILE = "events.json"
LOCAL_FILE = "events.json"

# 메모리 캐시 (cold start 때 한 번만 Gist 에서 읽어옴)
_cache = None


def _gist_read() -> dict:
    req = urllib.request.Request(
        f"https://api.github.com/gists/{GIST_ID}",
        headers={"Authorization": f"Bearer {GITHUB_TOKEN}",
                 "Accept": "application/vnd.github+json",
                 "User-Agent": "autocampus"},
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        info = json.loads(resp.read().decode("utf-8"))
    content = info["files"][GIST_FILE]["content"]
    return json.loads(content or "{}")


def _gist_write(data: dict) -> None:
    body = json.dumps({"files": {GIST_FILE: {
        "content": json.dumps(data, ensure_ascii=False)}}}).encode("utf-8")
    req = urllib.request.Request(
        f"https://api.github.com/gists/{GIST_ID}", data=body, method="PATCH",
        headers={"Authorization": f"Bearer {GITHUB_TOKEN}",
                 "Accept": "application/vnd.github+json",
                 "Content-Type": "application/json",
                 "User-Agent": "autocampus"},
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        resp.read()


def load_data() -> dict:
    global _cache
    if _cache is not None:
        return _cache
    # 1) Gist 우선
    if GIST_ID and GITHUB_TOKEN:
        try:
            _cache = _gist_read()
            return _cache
        except Exception as e:
            print("   (Gist 읽기 실패, 로컬로 폴백:", e, ")")
    # 2) 로컬 파일 폴백
    if os.path.exists(LOCAL_FILE):
        try:
            with open(LOCAL_FILE, encoding="utf-8") as f:
                _cache = json.load(f)
                return _cache
        except Exception:
            pass
    _cache = {}
    return _cache


def save_data(data: dict) -> None:
    global _cache
    _cache = data
    if GIST_ID and GITHUB_TOKEN:
        try:
            _gist_write(data)
            return
        except Exception as e:
            print("   (Gist 저장 실패, 로컬로 폴백:", e, ")")
    with open(LOCAL_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)


@app.get("/")
def root():
    data = load_data()
    return {"status": "ok",
            "events": sum(len(v) for v in data.values()),
            "store": "gist" if (GIST_ID and GITHUB_TOKEN) else "local"}


@app.get("/events")
def get_events():
    return JSONResponse(load_data())


@app.post("/events")
def post_events(payload: dict, x_token: str = Header(default="")):
    if x_token != TOKEN:
        raise HTTPException(status_code=401, detail="bad token")
    save_data(payload)
    return {"status": "saved", "events": sum(len(v) for v in payload.values())}
