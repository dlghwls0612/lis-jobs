"""cleaneye.py — 클린아이 잡플러스(지방공기업·지자체 출자출연기관) 통합 채용 수집기.

목록 API(/user/selectYpRecruitment.do)가 정형 JSON을 반환하므로 httpx로 빠르게 읽힌다.
서울(704001)·경기(704009) 진행 중(709001) 공고를 한 번만 받아 기록관리 탭과 도서관 탭이
공유해 필터링한다(추가 요청 0). 상세 링크(/user/ypCareersData.do)는 GET으로 바로 열린다.
"""
import re
import time
import httpx
from . import classify as rclassify
from . import parsers as rparsers
from .. import classify as lib_classify
from .. import parsers as lib_parsers

BASE_URL = "https://job.cleaneye.go.kr"
LIST_URL = f"{BASE_URL}/user/selectYpRecruitment.do"
DETAIL_URL = f"{BASE_URL}/user/ypCareersData.do"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
    "X-Requested-With": "XMLHttpRequest",
    "Referer": f"{BASE_URL}/user/ypRecruitment.do",
}

# P000005 근무지 코드(서울·경기) / T000008 소재지 시도 코드(서울·경기)
WORKPLACE_SEOUL = "704001"
WORKPLACE_GYEONGGI = "704009"
SIDO_MAP = {"007001": "서울", "007008": "경기"}

# 도서관·공공기관 시설·지원직 오탐 배제어 (예: 도서관 미화원·경비원)
_FACILITY_EXCLUDES = ("미화", "청소", "경비", "방호", "조리", "취사", "운전", "셔틀", "주차", "전기", "기계", "시설관리", "환경미화")
_SASEO_FALSE_RE = re.compile(r"(?:공|회|구|복|인|감|행|입|심|조|수|문)사서|사서(?:비스|울|함|류|지)")
_SEOUL_GU = (
    "강남", "강동", "강북", "강서", "관악", "광진", "구로", "금천", "노원", "도봉",
    "동대문", "동작", "마포", "서대문", "서초", "성동", "성북", "송파", "양천",
    "영등포", "용산", "은평", "종로", "중구", "중랑",
)
_GGI_SI_GUN = (
    ("수원", "시"), ("성남", "시"), ("고양", "시"), ("용인", "시"), ("부천", "시"),
    ("안산", "시"), ("안양", "시"), ("남양주", "시"), ("화성", "시"), ("평택", "시"),
    ("의정부", "시"), ("시흥", "시"), ("파주", "시"), ("광명", "시"), ("김포", "시"),
    ("군포", "시"), ("광주", "시"), ("이천", "시"), ("양주", "시"), ("오산", "시"),
    ("구리", "시"), ("안성", "시"), ("포천", "시"), ("의왕", "시"), ("하남", "시"),
    ("여주", "시"), ("동두천", "시"), ("과천", "시"),
    ("양평", "군"), ("가평", "군"), ("연천", "군"),
)


def _extract_district(org, title, region):
    """서울·경기 실제 시·군·구 목록 기준으로 자치구/시/군 추출(없으면 '전역·통합')."""
    text = f"{org or ''} {title or ''}"
    if region == "서울":
        for gu in _SEOUL_GU:
            if gu in text:
                return gu if gu.endswith("구") else f"{gu}구"
    elif region == "경기":
        for name, suffix in _GGI_SI_GUN:
            if name in text:
                return f"{name}{suffix}"
    return "전역·통합"


def _detect_region(row, rec_settings):
    """소재지 코드(sidoCd) 우선 판정, 타 시도 본사(예: 충북학사 동서울관)는 제목·기관명으로 보정."""
    sido = row.get("sidoCd") or ""
    if sido in SIDO_MAP:
        return SIDO_MAP[sido]
    return rclassify.detect_region(row.get("entTitle", ""), "", row.get("entName", ""), rec_settings)


def _detail_url(row):
    return (f"{DETAIL_URL}?empyear={row.get('empyear', '')}"
            f"&ypEntId={row.get('ypEntId', '')}&entSeq={row.get('entSeq', '')}")


def fetch_open_items(max_pages=25, delay=0.15):
    """클린아이 잡플러스 서울·경기 진행 중 공고 전체 목록(JSON)을 한 번에 가져온다."""
    items = []
    seen = set()
    with httpx.Client(headers=HEADERS, timeout=15.0, follow_redirects=True) as client:
        for page in range(1, max_pages + 1):
            form = {
                "pageIndex": str(page),
                "status": "709001",
                "workPlaceList[]": [WORKPLACE_SEOUL, WORKPLACE_GYEONGGI],
            }
            res = client.post(LIST_URL, data=form)
            if res.status_code != 200:
                if page == 1:
                    raise RuntimeError(f"cleaneye HTTP {res.status_code}")
                break
            data = res.json()
            rows = data.get("list") or []
            total = int(data.get("cnt") or 0)
            for row in rows:
                key = f"{row.get('empyear', '')}-{row.get('ypEntId', '')}-{row.get('entSeq', '')}"
                if key in seen:
                    continue
                seen.add(key)
                row["_key"] = key
                row["_url"] = _detail_url(row)
                items.append(row)
            if not rows or len(items) >= total or len(rows) < 10:
                break
            if delay:
                time.sleep(delay)
    return items


def crawl_cleaneye(rec_settings, raw_items=None):
    """클린아이 잡플러스 서울·경기 기록관리 공고 수집."""
    rows = raw_items if raw_items is not None else fetch_open_items()
    jobs = []
    for row in rows:
        title = " ".join((row.get("entTitle") or "").split())
        org = " ".join((row.get("entName") or "").split())
        if not title:
            continue
        if rclassify.is_result_post(title, rec_settings):
            continue
        region = _detect_region(row, rec_settings)
        if not region:
            continue
        if not rclassify.is_records_relevant(title, org, rec_settings, organization=org):
            continue

        start = rparsers.normalize_date(row.get("pubDate"))
        deadline = rparsers.normalize_date(row.get("pubEndDate"))
        if deadline and rclassify.is_expired(deadline):
            continue

        status_text, dday_label, badge_color = rclassify.calc_status(start, deadline)
        jobs.append({
            "id": f"cleaneye-{row['_key']}",
            "source": "클린아이 잡플러스",
            "category": "지방공기업·출연기관",
            "title": title,
            "organization": org or "지방공공기관",
            "region": region,
            "location": region,
            "jobtype": rclassify.tag_jobtype(title, "", rec_settings),
            "is_specialist": rclassify.has_specialist_credential(title, "", rec_settings),
            "start_date": start,
            "deadline": deadline,
            "status": status_text,
            "dday": dday_label,
            "badge_color": badge_color,
            "url": row["_url"],
        })
    return jobs


def crawl_cleaneye_library(lib_settings, rec_settings, raw_items=None):
    """클린아이 잡플러스 서울·경기 도서관·사서 공고 수집(도서관 탭 공유용)."""
    rows = raw_items if raw_items is not None else fetch_open_items()
    jobs = []
    for row in rows:
        title = " ".join((row.get("entTitle") or "").split())
        org = " ".join((row.get("entName") or "").split())
        if not title:
            continue
        if lib_classify.is_result_post(title, lib_settings):
            continue
        if lib_parsers.has_exclude_word(title) or any(w in title for w in _FACILITY_EXCLUDES):
            continue
        if not lib_classify.is_library_relevant(_SASEO_FALSE_RE.sub(" ", title), lib_settings):
            continue
        region = _detect_region(row, rec_settings)
        if not region:
            continue

        posted = rparsers.normalize_date(row.get("pubDate"))
        deadline = rparsers.normalize_date(row.get("pubEndDate"))
        if deadline and lib_classify.is_expired(deadline):
            continue

        jobs.append({
            "region": region,
            "district": _extract_district(org, title, region),
            "source": "통합포털(클린아이 잡플러스)",
            "title": f"[{org}] {title}" if org and org not in title else title,
            "posted": posted,
            "deadline": deadline,
            "url": row["_url"],
        })
    return jobs
