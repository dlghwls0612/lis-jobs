"""alio.py — 잡알리오(ALIO) 공공기관 채용정보 수집기 (td[2] 제목 교정 버전)."""
import re
import urllib.parse
import httpx
from bs4 import BeautifulSoup
from . import classify
from . import parsers

BASE_URL = "https://job.alio.go.kr"
LIST_URL = "https://job.alio.go.kr/recruit.do"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Referer": "https://job.alio.go.kr/recruit.do",
}


def crawl_alio(settings, keywords=None, include_recent_closed=True):
    """잡알리오에서 기록관리 관련 서울·경기 공공기관 채용공고 수집.
    include_recent_closed: True면 최근 마감된 공고도 [마감] 상태로 수집."""
    if keywords is None:
        keywords = ["기록물", "기록연구", "아키비스트", "기록관리"]

    jobs = []
    seen_ids = set()

    with httpx.Client(headers=HEADERS, timeout=12.0, follow_redirects=True) as client:
        for kw in keywords:
            params = {
                "keyword": kw,
            }
            try:
                res = client.get(LIST_URL, params=params)
                if res.status_code != 200:
                    continue
            except Exception as e:
                print(f"[ALIO] 검색어 '{kw}' 호출 실패: {e}")
                continue

            soup = BeautifulSoup(res.text, "html.parser")
            table = soup.find("table", class_="type_03")
            if not table:
                continue

            rows = table.find_all("tr")
            for r in rows[1:]:
                tds = r.find_all("td")
                if len(tds) < 8:
                    continue

                col_texts = [td.get_text(strip=True) for td in tds]

                # tds[2]가 실제 채용제목 (a 태그는 비어있고 td 내부에 텍스트 존재)
                title = col_texts[2]
                a_tag = tds[2].find("a", href=True) or r.find("a", href=True)
                href = a_tag["href"] if a_tag else ""

                idx_match = re.search(r'idx=(\d+)', href)
                item_id = idx_match.group(1) if idx_match else href
                if not item_id or item_id in seen_ids:
                    continue
                seen_ids.add(item_id)

                full_url = urllib.parse.urljoin(BASE_URL, href)

                # 결과 공고 제외
                if classify.is_result_post(title, settings):
                    continue

                org = col_texts[3] if len(col_texts) > 3 else ""
                location = classify.primary_area(col_texts[4] if len(col_texts) > 4 else "", org, title)   # 여러 곳이면 한 곳
                raw_jobtype = col_texts[5] if len(col_texts) > 5 else ""
                raw_deadline = col_texts[7] if len(col_texts) > 7 else ""
                state_text = col_texts[8] if len(col_texts) > 8 else ""

                # 서울·경기 지역 판별
                region = classify.detect_region(title, location, org, settings)
                if not region:
                    continue

                # 기록관리 직무 적합성 확인
                if not classify.is_records_relevant(title, f"{org} {raw_jobtype}", settings):
                    continue

                # 마감일 정규화
                deadline = parsers.normalize_date(raw_deadline)

                # 만료 공고 필터 (include_recent_closed=False일 때만 제외)
                is_closed = ("마감" in state_text) or (classify.is_expired(deadline) if deadline else False)
                if is_closed and not include_recent_closed:
                    continue

                # 고용 형태 및 전문요원 자격 태깅
                jobtype = classify.tag_jobtype(title, raw_jobtype, settings)
                is_specialist = classify.has_specialist_credential(title, raw_jobtype, settings)

                status_text, dday_label, badge_color = classify.calc_status(None, deadline)
                if is_closed:
                    status_text = "마감"
                    dday_label = "마감"
                    badge_color = "badge-expired"

                jobs.append({
                    "id": f"alio-{item_id}",
                    "source": "잡알리오",
                    "category": "공공기관·공기업",
                    "title": title,
                    "organization": org,
                    "region": region,
                    "location": location.replace("\r", " ").replace("\n", " ").replace("\t", "").strip(),
                    "jobtype": jobtype,
                    "is_specialist": is_specialist,
                    "start_date": None,
                    "deadline": deadline,
                    "status": status_text,
                    "dday": dday_label,
                    "badge_color": badge_color,
                    "url": full_url,
                })

    return jobs


_LIB_FACILITY_EXCLUDES = ("미화", "청소", "경비", "방호", "조리", "취사", "운전", "셔틀", "주차", "시설관리")
_SASEO_FALSE_RE = re.compile(r"(?:공|회|구|복|인|감|행|입|심|조|수|문)사서|사서(?:비스|울|함|류|지)")


def crawl_alio_library(lib_settings, rec_settings, keywords=None):
    """잡알리오에서 서울·경기 공공기관 도서관·사서 채용공고 수집(도서관 탭 공유용)."""
    from .. import classify as lib_classify
    from .. import parsers as lib_parsers

    if keywords is None:
        keywords = ["사서", "도서관"]

    jobs = []
    seen_ids = set()
    with httpx.Client(headers=HEADERS, timeout=12.0, follow_redirects=True) as client:
        for kw in keywords:
            try:
                res = client.get(LIST_URL, params={"keyword": kw})
                if res.status_code != 200:
                    continue
            except Exception as e:
                print(f"[ALIO-도서관] 검색어 '{kw}' 호출 실패: {e}")
                continue
            soup = BeautifulSoup(res.text, "html.parser")
            table = soup.find("table", class_="type_03")
            if not table:
                continue
            for r in table.find_all("tr")[1:]:
                tds = r.find_all("td")
                if len(tds) < 8:
                    continue
                cols = [td.get_text(strip=True) for td in tds]
                title = cols[2]
                a_tag = tds[2].find("a", href=True) or r.find("a", href=True)
                href = a_tag["href"] if a_tag else ""
                m = re.search(r"idx=(\d+)", href)
                item_id = m.group(1) if m else href
                if not item_id or item_id in seen_ids:
                    continue
                seen_ids.add(item_id)

                org = cols[3] if len(cols) > 3 else ""
                location = classify.primary_area(cols[4] if len(cols) > 4 else "", org, title)   # 여러 곳이면 한 곳
                raw_deadline = cols[7] if len(cols) > 7 else ""
                state_text = cols[8] if len(cols) > 8 else ""

                if "마감" in state_text:
                    continue
                if lib_classify.is_result_post(title, lib_settings):
                    continue
                if lib_parsers.has_exclude_word(title) or any(w in title for w in _LIB_FACILITY_EXCLUDES):
                    continue
                if not lib_classify.is_library_relevant(_SASEO_FALSE_RE.sub(" ", title), lib_settings):
                    continue
                region = classify.detect_region(title, location, org, rec_settings)
                if not region:
                    continue
                deadline = parsers.normalize_date(raw_deadline)
                if deadline and lib_classify.is_expired(deadline):
                    continue

                jobs.append({
                    "region": region,
                    "district": "전역·통합",
                    "source": "통합포털(잡알리오)",
                    "title": f"[{org}] {title}" if org and org not in title else title,
                    "posted": None,
                    "deadline": deadline,
                    "url": urllib.parse.urljoin(BASE_URL, href),
                })
    return jobs
