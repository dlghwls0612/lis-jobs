"""pull_logs.py — NAS 수집 로그를 이 PC 에 날짜별 파일로 쌓는다.

원리: NAS 가 매일 수집 결과(docs/data/jobs.json)를 GitHub 에 올리고, 그 파일의 meta.log 에
그날 수집 로그 전문이 들어 있다(2026-11-26부터). GitHub 이력에는 날마다의 파일이 전부 남으므로,
이 스크립트는 이력을 받아(git fetch — 받기만 하고 보내지 않음) 수집 한 번당 파일 하나로 풀어 놓는다.
- 이미 만든 파일은 건너뛴다(여러 번 실행해도 안전, PC 를 며칠 꺼 둬도 다음 실행 때 한꺼번에 채움).
- 로그 기능 이전 날짜는 요약(공고 수·수집 실패·파서 점검)만 남는다.
- 수집 기록이 아예 없는 날(수집 자체가 실패한 날)은 요약표에 '수집 없음'으로 표시한다.

실행: py tools/pull_logs.py [출력 폴더]   (기본 = 저장소 바깥 옆 폴더 '수집로그')
이 파일에는 개인 경로를 적지 않는다(공개 저장소). 경로는 전부 이 파일 위치 기준.
"""
import json
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
TARGET = "docs/data/jobs.json"
DEFAULT_OUT = REPO.parent / "수집로그"


def git(*args):
    r = subprocess.run(["git", "-C", str(REPO), *args], capture_output=True)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.decode("utf-8", "replace").strip())
    return r.stdout.decode("utf-8", "replace")


def main():
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_OUT
    out.mkdir(parents=True, exist_ok=True)
    try:
        git("fetch", "-q", "origin")
        ref = "origin/main"
    except RuntimeError as e:   # 인터넷이 안 되면 이 PC 에 이미 있는 이력만으로
        print(f"[알림] GitHub 에서 받지 못해 이 PC 에 있는 이력만 씁니다: {str(e)[:80]}")
        ref = "HEAD"

    rows = git("log", ref, "--format=%H|%an", "--", TARGET).splitlines()
    made, summary = 0, []
    for line in rows:
        h, author = line.split("|", 1)
        try:
            d = json.loads(git("show", f"{h}:{TARGET}"))
        except (RuntimeError, json.JSONDecodeError):
            continue
        m = d.get("meta", {})
        at = m.get("collected_at") or ""
        if len(at) < 16:
            continue
        day, hm = at[:10], at[11:16].replace(":", "")
        fails, unread = m.get("failures") or [], m.get("unreadable") or []
        rec = (m.get("records") or {}).get("job_count")
        summary.append((at, m.get("job_count"), rec, fails, unread, author, h[:7], bool(m.get("log"))))
        f = out / f"{day}_{hm}.txt"
        # 같은 시각 수집 기록이 둘인 날(수동 재실행 등)은 이름 뒤에 기록 번호를 붙여 따로 남긴다
        if f.exists() and h[:7] not in f.read_text(encoding="utf-8")[:300]:
            f = out / f"{day}_{hm}_{h[:7]}.txt"
        if f.exists():
            continue
        head = [
            f"수집 시각 : {at} (한국 시간)",
            f"올린 쪽   : {author}  (기록 {h[:7]})",
            f"공고 수   : {m.get('job_count')}건" + (f" (그중 기록관리 {rec}건)" if rec is not None else ""),
            f"수집 실패 : {len(fails)}곳" + (" - " + ", ".join(fails) if fails else ""),
            f"파서 점검 : {len(unread)}곳" + (" - " + ", ".join(unread) if unread else ""),
            "-" * 60,
        ]
        body = m.get("log") or "(이 날짜는 로그 보존 기능을 넣기 전이라 위 요약만 있습니다)"
        f.write_text("\n".join(head) + "\n" + body.rstrip() + "\n", encoding="utf-8")
        made += 1

    # 요약표: 수집 한 번당 한 줄, 최신이 위. 수집 기록이 없는 날도 표시(조용한 중단 감지용)
    summary.sort(key=lambda x: x[0], reverse=True)
    days = {s[0][:10] for s in summary}
    lines = ["수집 시각        | 공고 | 기록관리 | 실패 | 파서점검 | 로그 | 실패한 곳"]
    if summary:
        first = date.fromisoformat(min(days))
        last = date.fromisoformat(max(days))
        cur = last
        while cur >= first:
            ds = cur.isoformat()
            todays = [s for s in summary if s[0][:10] == ds]
            if not todays:
                lines.append(f"{ds}       |   -  |    -     |  -   |    -     |  -   | (수집 없음)")
            for at, jc, rec, fails, unread, _author, _hash, has_log in todays:
                lines.append(f"{at} | {jc:>4} | {rec if rec is not None else '-':>8} | {len(fails):>4} | "
                             f"{len(unread):>8} | {'있음' if has_log else '없음'} | {', '.join(fails)}")
            cur -= timedelta(days=1)
    (out / "_요약.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"수집 기록 {len(summary)}건 확인 · 새 로그 파일 {made}개 · 저장 위치: {out}")


if __name__ == "__main__":
    main()
