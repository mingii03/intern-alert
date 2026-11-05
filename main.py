"""인턴 공고 알림봇: 사람인·잡코리아·링커리어 새 공고를 텔레그램으로 알려줍니다.

사용법:
  python main.py            새 공고 확인 후 텔레그램 전송
  python main.py --dry-run  전송 없이 매칭된 공고만 출력 (기록도 저장 안 함)
  python main.py --chat-id  봇에게 메시지 보낸 뒤 실행하면 내 chat_id 출력
  python main.py --test     텔레그램 테스트 메시지 전송
"""
import html
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote_plus

import requests
from bs4 import BeautifulSoup

import config

KST = timezone(timedelta(hours=9))
STATE_FILE = Path(__file__).with_name("seen.json")
KEEP_DAYS = 120
FAIL_ALERT_THRESHOLD = 2  # 연속 실패 2회(약 6시간)면 한 번 경고
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0 Safari/537.36",
    "Accept-Language": "ko-KR,ko;q=0.9",
}

TG_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
TG_CHAT = os.environ.get("TELEGRAM_CHAT_ID", "").strip()

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")


def get(url):
    r = requests.get(url, headers=HEADERS, timeout=30)
    r.raise_for_status()
    time.sleep(1.5)  # 사이트에 부담 주지 않도록 간격
    return r.text


# ---------------------------------------------------------------- 수집기
def fetch_saramin():
    jobs = []
    for q in config.SEARCH_QUERIES:
        url = (
            "https://www.saramin.co.kr/zf_user/search/recruit?searchword="
            f"{quote_plus(q)}&recruitSort=reg_dt&recruitPageCount=40"
        )
        soup = BeautifulSoup(get(url), "html.parser")
        for it in soup.select("div.item_recruit"):
            rid, a = it.get("value"), it.select_one("h2.job_tit a")
            if not rid or not a:
                continue
            corp = it.select_one(".corp_name a")
            date = it.select_one(".job_date .date")
            cond = it.select_one(".job_condition")
            sector = it.select_one(".job_sector")
            jobs.append({
                "key": f"saramin:{rid}",
                "site": "사람인",
                "title": a.get("title") or a.get_text(strip=True),
                "company": corp.get_text(strip=True) if corp else "",
                "deadline": date.get_text(strip=True) if date else "",
                "extra": " ".join(x.get_text(" ", strip=True) for x in (cond, sector) if x),
                "url": f"https://www.saramin.co.kr/zf_user/jobs/relay/view?rec_idx={rid}",
            })
    return jobs


def fetch_jobkorea():
    jobs = []
    for q in config.SEARCH_QUERIES:
        url = f"https://www.jobkorea.co.kr/Search/?stext={quote_plus(q)}&Ord=RegDtDesc"
        soup = BeautifulSoup(get(url), "html.parser")
        for card in soup.select('[data-sentry-component="CardJob"]'):
            links = [a for a in card.select('a[href*="GI_Read/"]') if a.get_text(strip=True)]
            if not links:
                continue
            m = re.search(r"GI_Read/(\d+)", links[0]["href"])
            if not m:
                continue
            title = links[0].get_text(" ", strip=True)
            company = links[1].get_text(" ", strip=True) if len(links) > 1 else ""
            parts = card.get_text(" | ", strip=True).split(" | ")
            i = parts.index(title) if title in parts else 0
            jobs.append({
                "key": f"jobkorea:{m.group(1)}",
                "site": "잡코리아",
                "title": title,
                "company": company,
                "deadline": "",
                "extra": " ".join(parts[max(0, i - 2): i + 6]),  # 배지·지역·직무·경력
                "url": f"https://www.jobkorea.co.kr/Recruit/GI_Read/{m.group(1)}",
            })
    return jobs


def fetch_linkareer():
    jobs = []
    for page in range(1, config.LINKAREER_PAGES + 1):
        text = get(f"https://linkareer.com/list/intern?page={page}")
        m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', text, re.S)
        if not m:
            raise RuntimeError("링커리어 페이지 구조가 바뀌었습니다")
        st = json.loads(m.group(1))["props"]["pageProps"]["__APOLLO_STATE__"]
        for k, a in st.items():
            if not k.startswith("Activity:") or "INTERN" not in (a.get("jobTypes") or []):
                continue
            cats = [st.get(c.get("__ref"), {}).get("name", "") for c in a.get("categories") or []]
            close = a.get("recruitCloseAt")
            deadline = (
                "~" + datetime.fromtimestamp(close / 1000, KST).strftime("%m/%d")
                if close else ("상시" if a.get("recruitType") == "ASAP" else "")
            )
            jobs.append({
                "key": f"linkareer:{a['id']}",
                "site": "링커리어",
                "title": a.get("title", ""),
                "company": a.get("organizationName", ""),
                "deadline": deadline,
                "extra": " ".join(cats) + " 인턴",
                "url": f"https://linkareer.com/activity/{a['id']}",
            })
    return jobs


SOURCES = {"사람인": fetch_saramin, "잡코리아": fetch_jobkorea, "링커리어": fetch_linkareer}


# ---------------------------------------------------------------- 필터
def has_kw(text, kw):
    if re.fullmatch(r"[A-Za-z&]+", kw):  # 영문 약어는 단어 경계로 (IR이 IRIS에 걸리지 않게)
        return re.search(rf"(?<![A-Za-z]){re.escape(kw)}(?![A-Za-z])", text) is not None
    return kw in text


def is_match(job):
    title, extra = job["title"], job["extra"]
    if any(has_kw(title, k) for k in config.EXCLUDE_KEYWORDS):
        return False
    if not any(has_kw(title + " " + extra, k) for k in config.INTERN_KEYWORDS):
        return False
    return (any(has_kw(title, k) for k in config.MATCH_KEYWORDS)
            or any(has_kw(extra, k) for k in config.CATEGORY_KEYWORDS))


# ---------------------------------------------------------------- 텔레그램
def tg_send(text):
    if not (TG_TOKEN and TG_CHAT):
        print("[경고] TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID 가 설정되지 않아 전송 생략\n" + text)
        return
    r = requests.post(
        f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
        data={"chat_id": TG_CHAT, "text": text, "parse_mode": "HTML",
              "disable_web_page_preview": "true"},
        timeout=30,
    )
    if not r.ok:
        raise RuntimeError(f"텔레그램 전송 실패: {r.status_code} {r.text}")
    time.sleep(0.5)


def format_job(j):
    e = html.escape
    line = f"<b>[{e(j['site'])}] {e(j['company'])}</b>\n{e(j['title'])}"
    if j["deadline"]:
        line += f"\n🗓 {e(j['deadline'])}"
    return line + f"\n🔗 {e(j['url'])}"


def send_jobs(jobs):
    header = f"🔔 새 인턴 공고 {len(jobs)}건"
    chunk = header
    for j in jobs:
        block = "\n\n" + format_job(j)
        if len(chunk) + len(block) > 3800:  # 텔레그램 메시지 길이 제한(4096) 대비
            tg_send(chunk)
            chunk = header + " (계속)"
        chunk += block
    tg_send(chunk)


# ---------------------------------------------------------------- 상태 저장
def load_state():
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    return {"initialized": False, "seen": {}, "fails": {}}


def save_state(state):
    cutoff = (datetime.now(KST) - timedelta(days=KEEP_DAYS)).isoformat()
    state["seen"] = {k: v for k, v in state["seen"].items() if v >= cutoff}
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=1, sort_keys=True),
                          encoding="utf-8")


# ---------------------------------------------------------------- 실행
def run(dry_run=False):
    state = load_state()
    state.setdefault("fails", {})
    now = datetime.now(KST).isoformat(timespec="seconds")

    matched = []
    for name, fetch in SOURCES.items():
        try:
            jobs = fetch()
            hits = [j for j in jobs if is_match(j)]
            print(f"{name}: 수집 {len(jobs)}건 / 조건 일치 {len(hits)}건")
            matched += hits
            state["fails"][name] = 0
        except Exception as ex:
            print(f"[오류] {name}: {ex}")
            n = state["fails"].get(name, 0) + 1
            state["fails"][name] = n
            if n == FAIL_ALERT_THRESHOLD and not dry_run:
                tg_send(f"⚠️ {name} 공고 수집이 {n}회 연속 실패했어요.\n{html.escape(str(ex))[:300]}")

    # 사이트 내 중복(여러 검색어에 걸림) + 사이트 간 중복(같은 회사·제목) 제거
    uniq, seen_keys, seen_titles = [], set(), set()
    for j in matched:
        sig = re.sub(r"\W", "", j["company"] + j["title"])
        if j["key"] in seen_keys or sig in seen_titles:
            continue
        seen_keys.add(j["key"])
        seen_titles.add(sig)
        uniq.append(j)

    new = [j for j in uniq if j["key"] not in state["seen"]]

    if dry_run:
        print(f"\n--- 조건 일치 {len(uniq)}건 (그중 처음 보는 공고 {len(new)}건) ---")
        for j in uniq:
            print(("[NEW] " if j in new else "      ") + f"[{j['site']}] {j['company']} | {j['title']} | {j['url']}")
        return

    if not state["initialized"]:
        # 첫 실행: 기존 공고를 한꺼번에 보내지 않고 '이미 본 것'으로 등록만
        tg_send(f"✅ 인턴 공고 알림을 시작합니다.\n현재 조건에 맞는 공고 {len(new)}건을 기준으로, "
                "이후 새로 올라오는 공고만 알려드릴게요.")
        state["initialized"] = True
    elif new:
        send_jobs(new)
    print(f"새 공고 {len(new)}건")

    for j in new:
        state["seen"][j["key"]] = now
    save_state(state)


def print_chat_id():
    if not TG_TOKEN:
        sys.exit("TELEGRAM_BOT_TOKEN 환경변수를 먼저 설정하세요.")
    r = requests.get(f"https://api.telegram.org/bot{TG_TOKEN}/getUpdates", timeout=30).json()
    chats = {u["message"]["chat"]["id"]: u["message"]["chat"].get("first_name", "")
             for u in r.get("result", []) if "message" in u}
    if not chats:
        print("메시지를 찾지 못했어요. 텔레그램에서 봇에게 아무 메시지나 보낸 뒤 다시 실행하세요.")
    for cid, name in chats.items():
        print(f"chat_id: {cid}  ({name})")


if __name__ == "__main__":
    arg = sys.argv[1] if len(sys.argv) > 1 else ""
    if arg == "--chat-id":
        print_chat_id()
    elif arg == "--test":
        tg_send("👋 인턴 공고 알림봇 테스트 메시지입니다.")
        print("전송 완료")
    else:
        run(dry_run=(arg == "--dry-run"))
