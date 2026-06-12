# ─────────────────────────────────────────────────────────────
#  AutoCampus 서버 (1단계)
#  - GET  /          서버 상태 확인
#  - GET  /events    저장된 일정(JSON) 내려주기  ← 앱이 여기서 받아감
#  - POST /events    일정 업로드 (X-Token 필요)  ← PC 스크래퍼/나중에 서버가 채움
#
#  실행(로컬 테스트):  uvicorn app:app --reload
#  Render 배포 시작명령:  uvicorn app:app --host 0.0.0.0 --port $PORT
# ─────────────────────────────────────────────────────────────
import json
import os
from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import JSONResponse

app = FastAPI(title="AutoCampus Server")

DATA_FILE = "events.json"
# 업로드 보호용 토큰 (Render 환경변수 OC_TOKEN 으로 설정 권장. 없으면 기본값)
TOKEN = os.environ.get("OC_TOKEN", "change-me")


def load_data() -> dict:
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def save_data(data: dict) -> None:
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)


# 처음 켜질 때 파일 없으면 동작 확인용 샘플 한 건 넣기
if not os.path.exists(DATA_FILE):
    save_data({
        "2026-06-20": [{
            "id": 1, "title": "서버 연결 테스트", "time": "23:59",
            "source": "lms", "kind": "assignment", "category": "school",
            "course": "오토캠퍼스", "done": False
        }]
    })


@app.get("/")
def root():
    data = load_data()
    total = sum(len(v) for v in data.values())
    return {"status": "ok", "events": total}


@app.get("/events")
def get_events():
    # 앱(또는 브라우저)이 이 주소로 일정을 받아감
    return JSONResponse(load_data())


@app.post("/events")
def post_events(payload: dict, x_token: str = Header(default="")):
    # PC 스크래퍼가 events.json 내용을 여기로 올리면 서버에 저장됨
    if x_token != TOKEN:
        raise HTTPException(status_code=401, detail="bad token")
    save_data(payload)
    total = sum(len(v) for v in payload.values())
    return {"status": "saved", "events": total}
