"""records — 기록관리(기록연구사·기록물관리전문요원·아키비스트) 채용 수집.

원래 별도 저장소 archivescrawler 가 따로 돌리던 수집기를 그대로 옮겨 왔다
(karma·alio·saramin·classify·parsers 는 그쪽 코드 원본 사본, 설정은 저장소 루트의 records.yaml).
도서관 수집기(main.py)가 같은 실행에서 함께 돌리고, 결과는 사이트 형식으로 바꿔
region="기록관리" 탭에 넣는다. 그래서 도서관 쪽의 안전장치(수집 실패 시 이전 목록 재사용 ·
NEW 표시 · 안전만료 · 건강검진)를 기록관리 공고도 그대로 받는다.
"""
import re
from pathlib import Path

import yaml

from . import karma, alio, saramin, gojobs, cleaneye, jobkorea
from . import classify as rclassify

ROOT = Path(__file__).resolve().parent.parent.parent
CONFIG = ROOT / "records.yaml"
REGION = "기록관리"   # 사이트 탭 이름이자 jobs.json 의 region 값

# (sid, 사이트 '수집 실패' 배너에 쓸 이름, 수집 함수(settings, shared))
STEPS = (
    ("records-karma", "기록관리·한국기록전문가협회", lambda st, sh: karma.crawl_karma(st, max_pages=3)),
    ("records-alio", "기록관리·잡알리오", lambda st, sh: alio.crawl_alio(st)),
    ("records-gojobs", "기록관리·나라일터", lambda st, sh: gojobs.crawl_gojobs(st)),
    ("records-cleaneye", "기록관리·클린아이 잡플러스", lambda st, sh: cleaneye.crawl_cleaneye(st, raw_items=sh.get("cleaneye"))),
    ("records-saramin", "기록관리·사람인", lambda st, sh: saramin.crawl_saramin(st)),
    ("records-jobkorea", "기록관리·잡코리아", lambda st, sh: jobkorea.crawl_jobkorea(st)),
)
# 도서관 탭과 공유하는 통합 포털(사람인은 sources.yaml 의 sel-portal-saramin 이 별도 담당)
LIBRARY_PORTAL_STEPS = (
    ("portal-cleaneye", "통합포털(클린아이 잡플러스)",
     lambda lst, rst, sh: cleaneye.crawl_cleaneye_library(lst, rst, raw_items=sh.get("cleaneye"))),
    ("portal-jobkorea", "통합포털(잡코리아)",
     lambda lst, rst, sh: jobkorea.crawl_jobkorea_library(lst, rst)),
    ("portal-alio", "통합포털(잡알리오)",
     lambda lst, rst, sh: alio.crawl_alio_library(lst, rst)),
    ("portal-gojobs", "통합포털(나라일터)",
     lambda lst, rst, sh: gojobs.crawl_gojobs_library(lst, rst)),
)
# 같은 공고가 여러 곳에 올라오면 남길 쪽: 협회 공식 > 공공기관·나라일터·잡플러스 > 사람인·잡코리아
_PRIORITY = {
    "한국기록전문가협회": 1,
    "잡알리오": 2, "나라일터": 2, "클린아이 잡플러스": 2,
    "사람인": 3, "잡코리아": 3,
}


def load_settings():
    with open(CONFIG, encoding="utf-8") as f:
        return yaml.safe_load(f).get("settings", {})


def _stable_url(url):
    """사람인 검색결과 링크에는 검색할 때마다 바뀌는 search_uuid 등이 붙는다. 그대로 두면
    같은 공고가 매일 'NEW'로 뜨고 안전만료 기준일(firstSeen)도 매일 초기화된다
    → 도서관 탭의 사람인 링크와 같은 짧은 형태(rec_idx 만)로 고정."""
    m = re.search(r"saramin\.co\.kr/.*[?&]rec_idx=(\d+)", url)
    if m:
        return f"https://www.saramin.co.kr/zf_user/jobs/relay/view?view_type=list&rec_idx={m.group(1)}"
    return url


def to_site(a, sid):
    """archivescrawler 형식 공고 1건 → 도서관 사이트 형식(id·firstSeen 등은 main 이 채움)."""
    return {
        "sid": sid,
        "region": REGION,
        "district": a.get("region") or "지역 미상",     # 탭 안의 칩 = 서울/경기
        "source": a.get("organization") or a.get("source", ""),
        "via": a.get("source", ""),                     # 어디서 찾았나(협회·잡알리오·사람인)
        "title": a.get("title", ""),
        "jobType": a.get("jobtype") or "계약",          # 정규·계약·임기제·인턴
        "spec": bool(a.get("is_specialist")),           # 전문요원 자격 필수
        "posted": a.get("start_date"),
        "deadline": a.get("deadline"),
        "url": _stable_url(a.get("url", "")),
        "status": "upcoming" if a.get("status") == "접수예정" else "open",
    }


def fetch_shared():
    """도서관·기록관리 탭이 공통으로 쓰는 원자료(클린아이 잡플러스 서울·경기 진행 중 목록) 1회 수집."""
    shared = {}
    try:
        shared["cleaneye"] = cleaneye.fetch_open_items()
    except Exception as e:
        print(f"  ✗ [공용] 클린아이 잡플러스 목록 조회 실패 ({str(e)[:50]})")
        shared["cleaneye"] = None
    return shared


def collect(settings, shared=None):
    """기록관리 통합 소스를 차례로 수집. 반환: [(sid, name, jobs)] — jobs 가 None 이면 예외로 수집 실패."""
    sh = shared if shared is not None else {}
    out = []
    for sid, name, fn in STEPS:
        if sid == "records-cleaneye" and shared is not None and sh.get("cleaneye") is None:
            out.append((sid, name, None))
            continue
        try:
            raw = fn(settings, sh)
        except Exception as e:
            print(f"  ✗ [기록관리] {name}: 수집 실패 ({str(e)[:50]})")
            out.append((sid, name, None))
            continue
        live = [a for a in raw
                if a.get("status") != "마감" and not rclassify.is_expired(a.get("deadline"))]
        out.append((sid, name, [to_site(a, sid) for a in live]))
    return out


def collect_library_portals(lib_settings, rec_settings, shared=None, only=None):
    """도서관 탭과 공유하는 통합 포털(잡플러스·잡코리아·잡알리오·나라일터) 수집."""
    sh = shared if shared is not None else {}
    out = []
    for sid, name, fn in LIBRARY_PORTAL_STEPS:
        if only and (only not in sid and only not in name and only not in ("통합", "통합포털", "서울", "경기")):
            continue
        if sid == "portal-cleaneye" and shared is not None and sh.get("cleaneye") is None:
            out.append((sid, name, None))
            continue
        try:
            got = fn(lib_settings, rec_settings, sh)
        except Exception as e:
            print(f"  ✗ [도서관·통합] {name}: 수집 실패 ({str(e)[:50]})")
            out.append((sid, name, None))
            continue
        out.append((sid, name, got))
    return out


_CORP_NOISE_RE = re.compile(r"\(주\)|\(유\)|\(재\)|\(사\)|㈜|주식회사|유한회사|재단법인|사단법인")


def _norm_org(s):
    return "".join(c for c in _CORP_NOISE_RE.sub("", s or "") if c.isalnum())[:10]


def _norm_title(s):
    t = re.sub(r"\[[^\]]{1,12}\]", " ", s or "")
    t = re.sub(r"(?:채용\s*공고|채용|모집|공고)$", "", t.strip())
    return "".join(c for c in t if c.isalnum())[:18]


def deduplicate(jobs):
    """기관명+제목 앞부분이 같으면 1건으로 (출처 우선순위 적용, (주)/㈜·주식회사 표기 차이 정규화).
    도서관 쪽 dedup 과 섞지 않는 이유: 같은 공고가 도서관 탭에도 있으면 그쪽에 흡수돼
    기록관리 탭에서 사라진다."""
    seen, out = set(), []
    for j in sorted(jobs, key=lambda x: _PRIORITY.get(x.get("via"), 99)):
        key = _norm_org(j.get("source")) + ":" + _norm_title(j.get("title"))
        if key in seen:
            continue
        seen.add(key)
        out.append(j)
    return out
