# ─────────────────────────────────────────────────────────────
#  AutoCampus 서버  (영구저장 + 서버측 자동청소판)
#  - GET  /          상태
#  - GET  /events    일정(JSON)  ← 앱이 받아감 (받을 때도 한 번 더 청소)
#  - POST /events    업로드(X-Token) ← ★ 받는 즉시 중복/쓰레기 제거 후 저장
#
#  ★ 핵심: PC 스크래퍼(gmail_calendar.py)가 옛 버전이라 'CENB..마감 임박' 같은
#    알림 쓰레기를 올려도, 서버가 저장 전에 걸러내므로 앱에는 깨끗한 것만 보인다.
#
#  영구저장(선택): Render 환경변수 GIST_ID, GITHUB_TOKEN 넣으면 재시작에도 안 사라짐.
#    (준비법은 이 파일 하단 주석 참고)  OC_TOKEN 은 스크래퍼 토큰과 동일하게.
#  실행: uvicorn app:app --host 0.0.0.0 --port $PORT
# ─────────────────────────────────────────────────────────────
import json
import os
import re
import hashlib
import urllib.request
from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import JSONResponse

app = FastAPI(title="AutoCampus Server")

TOKEN = os.environ.get("OC_TOKEN", "change-me")
GIST_ID = os.environ.get("GIST_ID", "")
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
GIST_FILE = "events.json"
LOCAL_FILE = "events.json"
_cache = None

# ── 청소 로직 (oc_common 과 동일 규칙을 서버에 내장) ──────────────
ASSIGN_KW = ["과제", "제출", "보고서", "레포트", "리포트", "실습", "연습", "숙제",
             "homework", "assignment"]
EXAM_STRONG = ["기말고사", "중간고사", "정기고사", "기말시험", "중간시험",
               "시험", "고사", "퀴즈", "quiz", "exam", "테스트", "test"]
_JUNK_EXACT = {"ultradocumentbody", "untitled", "document", "n/a", "na", "내용", "본문"}
_JUNK_PHRASES = ("마감 임박", "마감임박", "기한 초과", "기한초과", "기한이 초과",
                 "제출 알림", "마감 알림", "리마인더", "알림이 도착",
                 "이(가) 마감", "이(가) 기한", "을(를) 제출", "새 공지 사항",
                 "제출물 수신함", "의 새 과제", "새 과제", "새 콘텐츠", "새 안내")
_ROMAN_RE = re.compile(r"^[ivxlcdm]+\s*([.\-(]\s*[\d\-.]*\)?\s*)?$", re.I)


def is_junk_title(title):
    t = (title or "").strip()
    if len(t) < 2:
        return True
    if t.lower() in _JUNK_EXACT:
        return True
    if any(ph in t for ph in _JUNK_PHRASES):
        return True
    if "성적" in t or "채점 결과" in t or "평가 결과" in t:
        return True
    if _ROMAN_RE.match(t):
        return True
    if re.match(r"^[A-Za-z]{2,8}\d*_", t):          # CENB102_..., CVL203_...
        return True
    if not re.search(r"[가-힣]", t) and " " not in t and re.match(r"^[A-Z]{2,6}\d*$", t):
        return True
    return False


def _norm(s):
    return re.sub(r"[\s\W_]+", "", (s or "").lower())


def _tokens(s):
    return set(re.findall(r"[가-힣a-z0-9]+", (s or "").lower()))


def _same_course(a, b):
    na, nb = _norm(a), _norm(b)
    if not na or not nb:
        return False
    return na == nb or na in nb or nb in na


def _title_match(a, b):
    na, nb = _norm(a), _norm(b)
    if not na or not nb:
        return False
    if na in nb or nb in na:
        return True
    return len(_tokens(a) & _tokens(b)) >= 2


def _kind_from_text(text):
    t = (text or "").lower()
    if any(w.lower() in t for w in ASSIGN_KW):      # '과제' 우선(기말고사대체과제=과제)
        return "assignment"
    if any(w.lower() in t for w in EXAM_STRONG):
        return "exam"
    return None


def _clean_course(raw):
    s = (raw or "").strip()
    if ":" in s:
        s = s.split(":", 1)[-1].strip()
    s = re.sub(r"^[A-Za-z0-9.\-]+_\s*", "", s)
    s = re.sub(r"\([^)]*\)\s*$", "", s)
    return s.strip()


def _make_id(date, title, course):
    h = hashlib.md5(f"{date}|{title}|{course}".encode("utf-8")).hexdigest()
    return int(h[:12], 16)


_GENERIC_EXAM = {"기말고사", "중간고사", "정기고사", "기말시험", "중간시험",
                 "시험", "고사", "퀴즈", "quiz", "exam", "테스트", "test"}


def clean_payload(events_map):
    """받은 일정에서 가짜 제목 제거 + 같은 과목/제목 중복을 1건으로. manual 은 보존."""
    if not isinstance(events_map, dict):
        return {}
    manual, auto = [], []
    for d, bucket in events_map.items():
        if not isinstance(bucket, list):
            continue
        for ev in bucket:
            if not isinstance(ev, dict):
                continue
            (manual if ev.get("source") == "manual" else auto).append((d, ev))

    auto = [(d, ev) for (d, ev) in auto if not is_junk_title(ev.get("title"))]

    groups = []
    for d, ev in auto:
        for g in groups:
            gd, ge = g[0]
            ce = (ge.get("course") or "").strip()
            cv = (ev.get("course") or "").strip()
            same_c = _same_course(ge.get("course"), ev.get("course"))
            both_empty = (not ce) and (not cv)
            one_empty = (not ce) != (not cv)
            if _title_match(ge.get("title"), ev.get("title")) and \
               (same_c or both_empty or (one_empty and d == gd)):
                g.append((d, ev))
                break
        else:
            groups.append([(d, ev)])

    out = {}
    for d, ev in manual:
        out.setdefault(d, []).append(ev)
    for g in groups:
        g.sort(key=lambda x: (x[1].get("time", "") == "",
                              not (x[1].get("course") or "").strip(),
                              -len(x[1].get("title", ""))))
        d, base = g[0]
        base = dict(base)
        base["done"] = any(e.get("done") for _, e in g)
        base["course"] = _clean_course(base.get("course", ""))
        base["kind"] = _kind_from_text(base.get("title")) or base.get("kind") or "assignment"
        _t = (base.get("title") or "").strip()
        if _t in _GENERIC_EXAM:
            _c = (base.get("course") or "").strip()
            if not _c:
                continue
            base["title"] = f"{_c} {_t}"
        base["id"] = _make_id(d, base.get("title", ""), base.get("course", ""))
        out.setdefault(d, []).append(base)
    for d in out:
        out[d].sort(key=lambda e: (e.get("time", "") == "", e.get("time", "")))
    return out


# ── Gist 저장/로드 ──────────────────────────────────────────
def _gist_read():
    req = urllib.request.Request(
        f"https://api.github.com/gists/{GIST_ID}",
        headers={"Authorization": f"Bearer {GITHUB_TOKEN}",
                 "Accept": "application/vnd.github+json", "User-Agent": "autocampus"})
    with urllib.request.urlopen(req, timeout=20) as r:
        info = json.loads(r.read().decode("utf-8"))
    return json.loads(info["files"][GIST_FILE]["content"] or "{}")


def _gist_write(data):
    body = json.dumps({"files": {GIST_FILE: {
        "content": json.dumps(data, ensure_ascii=False)}}}).encode("utf-8")
    req = urllib.request.Request(
        f"https://api.github.com/gists/{GIST_ID}", data=body, method="PATCH",
        headers={"Authorization": f"Bearer {GITHUB_TOKEN}",
                 "Accept": "application/vnd.github+json",
                 "Content-Type": "application/json", "User-Agent": "autocampus"})
    with urllib.request.urlopen(req, timeout=20) as r:
        r.read()


def load_data():
    global _cache
    if _cache is not None:
        return _cache
    if GIST_ID and GITHUB_TOKEN:
        try:
            _cache = _gist_read()
            return _cache
        except Exception as e:
            print("   (Gist 읽기 실패:", e, ")")
    if os.path.exists(LOCAL_FILE):
        try:
            with open(LOCAL_FILE, encoding="utf-8") as f:
                _cache = json.load(f)
                return _cache
        except Exception:
            pass
    _cache = {}
    return _cache


def save_data(data):
    global _cache
    _cache = data
    if GIST_ID and GITHUB_TOKEN:
        try:
            _gist_write(data)
            return
        except Exception as e:
            print("   (Gist 저장 실패:", e, ")")
    with open(LOCAL_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)


@app.get("/")
def root():
    data = load_data()
    return {"status": "ok", "events": sum(len(v) for v in data.values()),
            "store": "gist" if (GIST_ID and GITHUB_TOKEN) else "local"}


@app.get("/events")
def get_events():
    # 받아갈 때도 한 번 더 청소 (옛 데이터가 그대로 남아 있을 경우 대비)
    return JSONResponse(clean_payload(load_data()))


@app.post("/events")
def post_events(payload: dict, x_token: str = Header(default="")):
    if x_token != TOKEN:
        raise HTTPException(status_code=401, detail="bad token")
    cleaned = clean_payload(payload)          # ★ 저장 전에 쓰레기/중복 제거
    save_data(cleaned)
    raw = sum(len(v) for v in payload.values() if isinstance(v, list))
    kept = sum(len(v) for v in cleaned.values())
    return {"status": "saved", "received": raw, "events": kept}


# ── Gist 1회 준비 ───────────────────────────────────────────
#  1) https://gist.github.com → 새 secret gist (파일명 events.json, 내용 {}) → 주소의 id 가 GIST_ID
#  2) https://github.com/settings/tokens → Fine-grained → Gists: Read and write → GITHUB_TOKEN
#  3) Render Environment 에 OC_TOKEN / GIST_ID / GITHUB_TOKEN 넣고 재배포
