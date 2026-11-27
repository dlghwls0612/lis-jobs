"""jobkorea.py — 잡코리아 서울·경기 기록관리·도서관 통합 채용 수집기.

검색 페이지(/Search/?stext=...&local=I000,B000)의 [data-sentry-component="CardJob"] 카드를
httpx로 읽는다. 잡코리아는 일치 공고가 적을 때 무관한 추천 공고를 섞어 보여 주므로
키워드·태그 필터를 엄격히 거친 뒤, 통과한 소수 공고만 상세 페이지의 JSON-LD(validThrough)에서
정확한 마감일·게시일을 읽는다.
"""
import json
import re
import time
import urllib.parse
import httpx
from bs4 import BeautifulSoup
from . import classify as rclassify
from . import parsers as rparsers
from .. import classify as lib_classify
from .. import parsers as lib_parsers

BASE_URL = "https://www.jobkorea.co.kr"
SEARCH_URL = f"{BASE_URL}/Search/"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Referer": "https://www.jobkorea.co.kr/",
}
# 서울(I000), 경기(B000)
LOCAL_CODE = "I000,B000"
DETAIL_CAP = 25

# 도서관 탭 오탐 배제어('사서함'·'구사서울'·영어도서관/학원·영업 포함)
_LIB_FACILITY_EXCLUDES = (
    "미화", "청소", "경비", "방호", "조리", "취사", "운전", "셔틀", "주차",
    "베이커리", "바리스타", "카페", "제과", "제빵", "물류", "택배", "배달",
    "영업", "판매", "매장", "텔레마케터", "상담원", "인테리어",
    "영어도서관", "영어학원", "어학원", "영어 학원", "리딩", "영어대화",
    "파트너 모집", "유치", "프리랜서",
)
_SASEO_FALSE_RE = re.compile(r"(?:공|회|구|복|인|감|행|입|심|조|수|문)사서|사서(?:비스|울|함|류|지)")
_LIB_STRONG_WORDS = ("도서관", "개관연장", "개관시간 연장", "순회사서", "자료실", "열람실",
                     "작은도서관", "북큐레이터", "문헌정보", "도서정보", "사서")


def _parse_card(card):
    """CardJob 요소에서 공고 ID·제목·회사명·근무지·태그·조건 텍스트를 추출."""
    links = [a for a in card.find_all("a", href=True) if "/Recruit/GI_Read/" in a["href"]]
    if not links:
        return None
    href = links[0]["href"]
    m = re.search(r"/Recruit/GI_Read/(\d+)", href)
    if not m:
        return None
    gno = m.group(1)
    url = f"{BASE_URL}/Recruit/GI_Read/{gno}"

    # 첫 번째 공고 링크 = 제목, 두 번째(또는 다른 텍스트의 링크) = 회사명
    link_texts = []
    for a in links:
        t = " ".join(a.get_text(" ", strip=True).split())
        if t and t not in link_texts:
            link_texts.append(t)
    if not link_texts:
        return None
    title = link_texts[0]
    corp = link_texts[1] if len(link_texts) > 1 else ""

    strings = [s.strip() for s in card.stripped_strings if s.strip() and s.strip() != "스크랩"]
    if not corp:
        for s in strings:
            if s != title and not s.startswith(("서울", "경기", "즉시", "홈페이지", "경력", "신입", "연봉", "월급")):
                corp = s
                break

    loc_text = ""
    tags = []
    for s in strings:
        if not loc_text and re.match(r"^(?:서울|경기)(?:\s|$)", s):
            loc_text = s
        elif "," in s and not re.search(r"\d", s) and s not in (title, corp):
            tags.extend(x.strip() for x in s.split(",") if x.strip())

    cond_text = " ".join(s for s in strings if s not in (title, corp))
    return {
        "gno": gno,
        "url": url,
        "title": title,
        "corp": corp or "기관명 미확인",
        "loc_text": loc_text,
        "tags": tags,
        "cond_text": cond_text,
    }


def _search_cards(client, keywords, max_pages=1, delay=0.35):
    """검색어 목록에 대해 서울·경기 CardJob을 중복 없이 수집."""
    out = []
    seen = set()
    for kw in keywords:
        for page in range(1, max_pages + 1):
            params = {"stext": kw, "local": LOCAL_CODE}
            if page > 1:
                params["Page_No"] = str(page)
            try:
                res = client.get(SEARCH_URL, params=params)
                if res.status_code != 200:
                    break
            except Exception as e:
                print(f"[JobKorea] 검색어 '{kw}' {page}쪽 호출 실패: {e}")
                break
            soup = BeautifulSoup(res.text, "html.parser")
            cards = soup.select('[data-sentry-component="CardJob"]')
            if not cards:
                break
            for card in cards:
                parsed = _parse_card(card)
                if not parsed or parsed["gno"] in seen:
                    continue
                seen.add(parsed["gno"])
                out.append(parsed)
            if delay:
                time.sleep(delay)
    return out


def _fetch_detail_dates(client, url, delay=0.3):
    """상세 페이지의 JSON-LD(validThrough·datePosted)에서 마감일·게시일 추출."""
    posted = None
    deadline = None
    try:
        res = client.get(url)
        if res.status_code != 200:
            return None, None
        soup = BeautifulSoup(res.text, "html.parser")
        for sc in soup.find_all("script", type="application/ld+json"):
            raw = sc.get_text(strip=True)
            if not raw or "validThrough" not in raw:
                continue
            try:
                jd = json.loads(raw)
            except Exception:
                continue
            if isinstance(jd, dict):
                vt = str(jd.get("validThrough") or "")
                dp = str(jd.get("datePosted") or "")
                if len(vt) >= 10:
                    deadline = rparsers.normalize_date(vt[:10])
                if len(dp) >= 10:
                    posted = rparsers.normalize_date(dp[:10])
                break
        if not deadline:
            text = soup.get_text(" ", strip=True)
            m = re.search(r"마감일\s*(\d{4}[.\-/]\d{1,2}[.\-/]\d{1,2})", text)
            if m:
                deadline = rparsers.normalize_date(m.group(1))
    except Exception:
        pass
    if delay:
        time.sleep(delay)
    return posted, deadline


def _region_district(loc_text, title, corp, rec_settings):
    """근무지 문자열에서 (region, district) 추출."""
    region = None
    district = "전역·통합"
    if loc_text:
        parts = loc_text.split()
        if parts[0].startswith("서울"):
            region = "서울"
        elif parts[0].startswith("경기"):
            region = "경기"
        if len(parts) > 1 and parts[1].endswith(("구", "시", "군")):
            district = parts[1]
    if not region:
        region = rclassify.detect_region(title, loc_text, corp, rec_settings)
    return region, district


def crawl_jobkorea(rec_settings, keywords=None):
    """잡코리아 서울·경기 기록관리 공고 수집."""
    if keywords is None:
        keywords = ["기록물관리", "기록물", "기록연구", "아키비스트", "기록관리전문요원",
                    "디지털아카이브", "메타데이터"]
    jobs = []
    details_used = 0
    with httpx.Client(headers=HEADERS, timeout=12.0, follow_redirects=True) as client:
        cards = _search_cards(client, keywords, max_pages=1)
        for c in cards:
            title, corp, loc_text = c["title"], c["corp"], c["loc_text"]
            if rclassify.is_result_post(title, rec_settings):
                continue
            full_text = f"{title} {corp} {c['cond_text']}"
            if not rclassify.is_records_relevant(title, full_text, rec_settings, organization=corp, tags=c["tags"]):
                continue
            region, _ = _region_district(loc_text, title, corp, rec_settings)
            if not region:
                continue

            posted, deadline = (None, None)
            if details_used < DETAIL_CAP:
                posted, deadline = _fetch_detail_dates(client, c["url"])
                details_used += 1
            if deadline and rclassify.is_expired(deadline):
                continue

            status_text, dday_label, badge_color = rclassify.calc_status(posted, deadline)
            jobs.append({
                "id": f"jobkorea-{c['gno']}",
                "source": "잡코리아",
                "category": "문화재단·공공·민간",
                "title": title,
                "organization": corp,
                "region": region,
                "location": loc_text or region,
                "jobtype": rclassify.tag_jobtype(title, c["cond_text"], rec_settings),
                "is_specialist": rclassify.has_specialist_credential(title, f"{c['cond_text']} {corp}", rec_settings),
                "start_date": posted,
                "deadline": deadline,
                "status": status_text,
                "dday": dday_label,
                "badge_color": badge_color,
                "url": c["url"],
            })
    return jobs


def _is_jobkorea_library_relevant(title, corp, tags, lib_settings):
    """잡코리아 도서관·사서 관련성 판별(무관 추천 공고·'사서함/구사서울'·영어도서관 오탐 차단)."""
    if lib_classify.is_result_post(title, lib_settings):
        return False
    combined = f"{title} {corp}"
    if lib_parsers.has_exclude_word(title) or any(w in combined for w in _LIB_FACILITY_EXCLUDES):
        return False
    cleaned = _SASEO_FALSE_RE.sub(" ", title)
    if any(w in cleaned for w in _LIB_STRONG_WORDS):
        return True
    if "사서" in tags:
        return True
    return False


def crawl_jobkorea_library(lib_settings, rec_settings, keywords=None):
    """잡코리아 서울·경기 도서관·사서 공고 수집(도서관 탭 공유용)."""
    if keywords is None:
        keywords = ["사서", "도서관", "문헌정보"]
    jobs = []
    details_used = 0
    with httpx.Client(headers=HEADERS, timeout=12.0, follow_redirects=True) as client:
        cards = _search_cards(client, keywords, max_pages=1)
        for c in cards:
            title, corp, loc_text = c["title"], c["corp"], c["loc_text"]
            if not _is_jobkorea_library_relevant(title, corp, c["tags"], lib_settings):
                continue
            region, district = _region_district(loc_text, title, corp, rec_settings)
            if not region:
                continue

            posted, deadline = (None, None)
            if details_used < DETAIL_CAP:
                posted, deadline = _fetch_detail_dates(client, c["url"])
                details_used += 1
            if deadline and lib_classify.is_expired(deadline):
                continue

            disp = f"[{corp}] {title}" if corp and corp != "기관명 미확인" and corp not in title else title
            jobs.append({
                "region": region,
                "district": district,
                "source": "통합포털(잡코리아)",
                "title": disp,
                "posted": posted,
                "deadline": deadline,
                "url": c["url"],
            })
    return jobs
