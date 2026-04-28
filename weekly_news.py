#!/usr/bin/env python3
"""
주간 뉴스 후보 큐레이션 (월요일 자동 실행)

매주 월요일 KST 06:50:
1. Claude API에 한 주간 뉴스 검색 요청
2. 해운대/마린시티/우동 부동산 + 부산 정책 + 지역 호재 위주
3. 10개 후보 추출 + 태그 분류
4. 텔레그램으로 발송 (사람이 4개 선택할 수 있도록)

소장님이 텔레그램에서 후보 보고 → Claude 채팅에서 "1,3,5,7번 골랐어"
→ news_curated.json 갱신 코드 받아서 GitHub commit

환경 변수 (GitHub Secrets):
- ANTHROPIC_API_KEY: Claude API 키
- TELEGRAM_BOT_TOKEN: 봇 토큰 (기존)
- TELEGRAM_CHAT_ID: 그룹 ID (기존)
"""

import json
import os
import sys
import time
import urllib.parse
import urllib.request
import urllib.error
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

ANTHROPIC_API_KEY = os.environ.get('ANTHROPIC_API_KEY', '').strip()
TELEGRAM_BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN', '').strip()
TELEGRAM_CHAT_ID = os.environ.get('TELEGRAM_CHAT_ID', '').strip()

ANTHROPIC_API_URL = 'https://api.anthropic.com/v1/messages'
MODEL = 'claude-sonnet-4-5'

TELEGRAM_LIMIT = 3800


# ========== 텔레그램 전송 ==========

def telegram_send_raw(text):
    url = f'https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage'
    data = urllib.parse.urlencode({
        'chat_id': TELEGRAM_CHAT_ID,
        'text': text,
        'parse_mode': 'Markdown',
        'disable_web_page_preview': 'true',
    }).encode('utf-8')
    req = urllib.request.Request(url, data=data, method='POST')
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode('utf-8'))
    except urllib.error.HTTPError as e:
        body = e.read().decode('utf-8', errors='ignore')
        raise RuntimeError(f'Telegram HTTP {e.code}: {body}')


def telegram_send(text):
    """길이 제한 시 자동 분할."""
    if len(text) <= TELEGRAM_LIMIT:
        return telegram_send_raw(text)
    blocks = text.split('\n\n')
    chunks = []
    current = ''
    for block in blocks:
        candidate = current + ('\n\n' if current else '') + block
        if len(candidate) > TELEGRAM_LIMIT and current:
            chunks.append(current)
            current = block
        else:
            current = candidate
    if current:
        chunks.append(current)
    total = len(chunks)
    for i, chunk in enumerate(chunks, 1):
        suffix = f'\n\n_({i}/{total})_'
        telegram_send_raw(chunk + suffix)
        time.sleep(0.5)


# ========== Claude API 호출 ==========

def call_claude(prompt):
    """Claude API에 검색 + 추천 요청."""
    headers = {
        'x-api-key': ANTHROPIC_API_KEY,
        'anthropic-version': '2023-06-01',
        'content-type': 'application/json',
    }
    body = {
        'model': MODEL,
        'max_tokens': 4096,
        'tools': [{
            'type': 'web_search_20250305',
            'name': 'web_search',
            'max_uses': 8,  # 검색 횟수 제한 (비용 관리)
        }],
        'messages': [
            {'role': 'user', 'content': prompt}
        ]
    }
    data = json.dumps(body).encode('utf-8')
    req = urllib.request.Request(ANTHROPIC_API_URL, data=data, method='POST', headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            return json.loads(resp.read().decode('utf-8'))
    except urllib.error.HTTPError as e:
        body_text = e.read().decode('utf-8', errors='ignore')
        raise RuntimeError(f'Claude API HTTP {e.code}: {body_text}')


def extract_text_blocks(response):
    """응답에서 text 블록만 추출 (tool_use, server_tool_use 등은 무시)."""
    parts = []
    for block in response.get('content', []):
        if block.get('type') == 'text':
            parts.append(block.get('text', ''))
    return '\n'.join(parts).strip()


# ========== 메인 ==========

def build_search_prompt():
    today = datetime.now(ZoneInfo('Asia/Seoul')).date()
    week_ago = today - timedelta(days=7)

    return f"""당신은 부산 해운대 마린시티 부동산 사무실의 디지털 사이니지 뉴스 큐레이터입니다.

**오늘 날짜:** {today.strftime('%Y년 %m월 %d일')}
**검색 기간:** 지난 한 주 ({week_ago.strftime('%Y.%m.%d')} ~ {today.strftime('%Y.%m.%d')})

**타겟 청중:** 마린시티 부동산 사무실 앞을 지나가는 분들. 주로 재력가·투자자·자산가 계층입니다. 이분들이 멈춰서 보고 싶어하는 정보는 자기 자산을 지키고 늘리는 데 직결되는 뉴스입니다.

**작업:** 웹 검색을 통해 다음 영역의 뉴스를 찾아 **정확히 10개의 후보**를 추려주세요. 영역별로 골고루 분포되도록 배분하세요.

**검색 영역 (4가지):**

**1. 세금 관련 (3개 후보 권장)** — 부동산·상속·증여 위주
   - 양도소득세 / 종합부동산세 / 재산세 변경
   - 상속세 / 증여세 개정·완화 동향
   - 부동산 세제 정책 변경
   - 다주택자·고가주택 세금 이슈

**2. 투자 관련 (3개 후보 권장)** — 자산 운용 정보
   - 부동산 시장 전망 (전국 또는 부산 광역)
   - 주식 시장 큰 흐름 (코스피/나스닥 주요 변동, 주요 종목 이슈)
   - 가상자산(코인) 주요 이슈
   - 금리·환율 동향
   - 자산 배분 전략 관련 보도

**3. 교육 관련 (2개 후보 권장)** — 입시·교육제도
   - 대입 제도 개편 (수능, 학종, 정시·수시)
   - 의대·약대 정원 / 자사고·특목고 정책
   - 사교육·학군 트렌드
   - 해외 대학·유학 관련 정책

**4. 해운대구 우동 직접 호재 (2개 후보 권장)** — 반드시 긍정적인 뉴스만
   - 우동/마린시티 신축 분양·재건축
   - 인프라 개선 (지하철, 도로, 공원)
   - 학군 강화, 학교 신설/이전
   - 상권 호재, 대형 시설 입주
   - **부정적 뉴스는 절대 제외** (PF 위기, 시행사 부도, 가격 하락 등)

**❌ 절대 제외:**
- 시장 신뢰를 떨어뜨리는 부정적 뉴스 ("폭락", "위기", "부도")
- 사기·범죄·사고 관련 뉴스
- AI 생성 가짜 정보, 광고성 게시물
- 1주일보다 오래된 뉴스
- 정치적으로 양극화된 논쟁성 이슈

**✅ 우선:**
- 객관적 사실 + 재력가 관심 + 긍정/중립적 톤
- 정책 발표, 통계 발표, 시장 분석 보도
- 출처가 명확한 주요 언론사 기사

**출력 형식 (반드시 이 JSON 형식으로만 답하세요. 설명 없이 JSON만):**

```json
{{
  "candidates": [
    {{
      "n": 1,
      "tag": "tax",
      "headline": "헤드라인 (60자 이내, 핵심만 간결하게)",
      "date": "MM.DD",
      "source": "출처(언론사명)"
    }},
    ...
  ]
}}
```

**태그 종류 (4가지):**
- `tax`: 세금 관련
- `invest`: 투자 (부동산·주식·코인·금리)
- `edu`: 교육·입시
- `local`: 우동·마린시티 직접 호재 (긍정적만)

**중요한 표현 가이드:**
- 헤드라인은 60자 이내로 간결하게
- 광고문구처럼 보이지 않게 객관적·정보적 톤으로
- "단독", "충격", "OO이래 처음" 같은 자극적 단어 자제
- 숫자·통계가 있으면 헤드라인에 포함하면 임팩트 ↑"""


def parse_candidates(text):
    """Claude 응답에서 JSON 추출."""
    # ```json ... ``` 블록 찾기
    if '```json' in text:
        start = text.find('```json') + 7
        end = text.find('```', start)
        json_str = text[start:end].strip()
    elif '```' in text:
        start = text.find('```') + 3
        end = text.find('```', start)
        json_str = text[start:end].strip()
    else:
        # JSON 직접 시도
        json_str = text.strip()

    return json.loads(json_str)


def format_telegram_message(data, run_dt):
    today_str = run_dt.strftime('%Y.%m.%d')
    weekday = ['월', '화', '수', '목', '금', '토', '일'][run_dt.weekday()]

    lines = []
    lines.append(f'📰 *주간 사이니지 뉴스 후보*  ({today_str} {weekday})')
    lines.append('')
    lines.append('이번 주 사이니지에 띄울 뉴스 *4개를 선택*하세요.')
    lines.append('━━━━━━━━━━━━━━━━')

    tag_emoji = {
        'tax': '💰',
        'invest': '📈',
        'edu': '🎓',
        'local': '📍',
    }

    for c in data.get('candidates', []):
        n = c.get('n', '?')
        tag = c.get('tag', 'local')
        emoji = tag_emoji.get(tag, '📰')
        headline = c.get('headline', '')
        date = c.get('date', '')
        source = c.get('source', '')

        lines.append('')
        lines.append(f'*[{n}]* {emoji} `{tag.upper()}`')
        lines.append(f'    {headline}')
        meta = []
        if date: meta.append(date)
        if source: meta.append(source)
        if meta:
            lines.append(f'    _{" · ".join(meta)}_')

    lines.append('')
    lines.append('━━━━━━━━━━━━━━━━')
    lines.append('💬 *다음 단계:*')
    lines.append('Claude 채팅에 답변:')
    lines.append('`이번 주 뉴스 X, Y, Z, W번 선택`')
    lines.append('→ Claude가 `news_curated.json` 새 내용 만들어드림')
    lines.append('→ GitHub에서 commit')

    return '\n'.join(lines)


def main():
    # 환경 변수 점검
    missing = [k for k, v in {
        'ANTHROPIC_API_KEY': ANTHROPIC_API_KEY,
        'TELEGRAM_BOT_TOKEN': TELEGRAM_BOT_TOKEN,
        'TELEGRAM_CHAT_ID': TELEGRAM_CHAT_ID,
    }.items() if not v]
    if missing:
        print(f'❌ 필수 환경 변수 누락: {", ".join(missing)}')
        sys.exit(1)

    run_dt = datetime.now(ZoneInfo('Asia/Seoul'))
    print(f'=== 주간 뉴스 큐레이션 ({run_dt.isoformat()}) ===')

    # Claude API 호출
    print('🤖 Claude에게 뉴스 검색 + 추천 요청 중...')
    prompt = build_search_prompt()
    try:
        response = call_claude(prompt)
    except Exception as e:
        print(f'❌ Claude API 실패: {e}')
        try:
            telegram_send(f'⚠️ *주간 뉴스 큐레이션 실패*\n\n`{e}`')
        except: pass
        sys.exit(1)

    # 응답 파싱
    text = extract_text_blocks(response)
    print(f'  응답 길이: {len(text)}자')
    print(f'  응답 미리보기: {text[:300]}...')

    try:
        data = parse_candidates(text)
    except Exception as e:
        print(f'❌ JSON 파싱 실패: {e}')
        # 파싱 실패 시 원본을 그대로 텔레그램으로 (사람이 직접 처리할 수 있게)
        fallback = f'⚠️ *주간 뉴스 (자동 파싱 실패, 원본 전송)*\n\n{text[:3500]}'
        telegram_send(fallback)
        sys.exit(1)

    candidates = data.get('candidates', [])
    print(f'  추천 후보: {len(candidates)}개')

    if not candidates:
        telegram_send('⚠️ 이번 주 추천할 뉴스 후보가 없습니다.')
        return

    # 텔레그램 메시지 구성 + 발송
    msg = format_telegram_message(data, run_dt)
    print(f'  메시지 길이: {len(msg)}자')

    try:
        telegram_send(msg)
        print('✅ 텔레그램 전송 완료')
    except Exception as e:
        print(f'❌ 텔레그램 전송 실패: {e}')
        sys.exit(1)

    # 후보 데이터 저장 (선택사항: 나중에 참조용)
    os.makedirs('cache', exist_ok=True)
    with open('cache/last_news_candidates.json', 'w', encoding='utf-8') as f:
        json.dump({
            'generated_at': run_dt.isoformat(),
            'candidates': candidates,
        }, f, ensure_ascii=False, indent=2)
    print('✅ 후보 캐시 저장')


if __name__ == '__main__':
    main()
