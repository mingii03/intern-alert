# 인턴 공고 알림봇

사람인·잡코리아·링커리어에서 **전략/기획/컨설팅, 재무/회계/IR** 인턴 공고를 3시간마다 확인하고,
새 공고가 올라오면 텔레그램으로 알려줍니다. GitHub Actions에서 돌아가므로 PC를 꺼 둬도 동작합니다.

## 처음 설정 (약 10분)

### 1. 텔레그램 봇 만들기
1. 텔레그램에서 **@BotFather** 검색 → `/newbot` 입력 → 봇 이름과 아이디(`..._bot`으로 끝나야 함)를 입력
2. 받은 **토큰**(`1234567890:AAxx...` 형태)을 복사
3. 방금 만든 봇과의 대화창을 열고 아무 메시지나 보내기 (예: `안녕`)
4. PowerShell에서 내 chat_id 확인:
   ```powershell
   cd C:\Users\MGC\intern-alert
   $env:TELEGRAM_BOT_TOKEN="여기에_토큰"
   python main.py --chat-id
   $env:TELEGRAM_CHAT_ID="출력된_숫자"
   python main.py --test      # 휴대폰에 테스트 메시지가 오면 성공
   ```

### 2. GitHub에 올리기
1. https://github.com/new 에서 저장소 생성: 이름 `intern-alert`, **Private**, README 추가하지 않기
2. PowerShell에서:
   ```powershell
   cd C:\Users\MGC\intern-alert
   git remote add origin https://github.com/<내아이디>/intern-alert.git
   git push -u origin main
   ```
3. 저장소 → **Settings → Secrets and variables → Actions → New repository secret** 에서 두 개 등록
   - `TELEGRAM_BOT_TOKEN` = 봇 토큰
   - `TELEGRAM_CHAT_ID` = chat_id 숫자

### 3. 실행
저장소 → **Actions** 탭 → `인턴 공고 확인` → **Run workflow** 클릭.
"✅ 인턴 공고 알림을 시작합니다" 메시지가 오면 끝입니다. 이후 3시간마다 자동으로 확인합니다.

> 첫 실행에서는 현재 올라와 있는 공고를 기준으로 등록만 하고, **그 이후 새로 올라오는 공고만** 알려줍니다.

## 조건 바꾸기
`config.py`만 수정하고 GitHub에 다시 올리면 됩니다 (`git commit -am "조건 수정"; git pull --rebase; git push`).

| 항목 | 의미 |
|---|---|
| `SEARCH_QUERIES` | 사람인/잡코리아 검색어 |
| `MATCH_KEYWORDS` | 공고 **제목**에 있으면 알림 |
| `CATEGORY_KEYWORDS` | 사이트 **직무 분류**에 있으면 알림 |
| `EXCLUDE_KEYWORDS` | 제목에 있으면 제외 |

수정한 다음 `python main.py --dry-run`을 실행하면, 알림을 보내지 않고 어떤 공고가 걸리는지만 확인할 수 있습니다.

## 참고
- 한 사이트에서 수집이 연속으로 실패하면(약 6시간) 텔레그램으로 경고를 한 번 보냅니다. 사이트 구조가 바뀐 경우일 수 있습니다.
- GitHub 무료 플랜(Private 저장소 기준 월 2,000분)으로 충분합니다. 1회 실행에 약 1분이 걸려 한 달에 약 240분을 씁니다.
- 저장소에 60일 동안 활동이 없으면 GitHub이 스케줄 실행을 멈출 수 있습니다. 알림이 끊기면 Actions 탭에서 다시 활성화하세요.
