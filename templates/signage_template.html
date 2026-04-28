#!/usr/bin/env python3
"""
주간 뉴스 후보 큐레이션 v3 (자산가 페르소나 정조준)

카테고리 6개:
- busan: 부산·해운대 직접 호재 (가장 우선)
- tax: 자산가 친화 세금 정책
- market: 자산 시장 동향 (서울 부동산, 코인, 주식, 금리)
- edu: 입시·서울 대학
- wealth: 고액 자산가 투자 트렌드
- global: 거시·국제 이슈 (1주 0-1개로 최소화)

흐름:
1. 네이버 뉴스 API로 카테고리별 키워드 검색
2. Claude가 자산가 관점에서 10개 선별
3. 텔레그램 발송 (Markdown 안전 이스케이프)

환경 변수: NAVER_CLIENT_ID, NAVER_CLIENT_SECRET, ANTHROPIC_API_KEY,
          TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
"""

import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
import urllib.error
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

NAVER_CLIENT_ID = os.environ.get('NAVER_CLIENT_ID', '').strip()
NAVER_CLIENT_SECRET = os.environ.get('NAVER_CLIENT_SECRET', '').strip()
ANTHROPIC_API_KEY = os.environ.get('ANTHROPIC_API_KEY', '').strip()
TELEGRAM_BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN', '').strip()
TELEGRAM_CHAT_ID = os.environ.get('TELEGRAM_CHAT_ID', '').strip()

NAVER_API_URL = 'https://openapi.naver.com/v1/search/news.json'
ANTHROPIC_API_URL = 'https://api.anthropic.com/v1/messages'
MODEL = 'claude-sonnet-4-5'

TELEGRAM_LIMIT = 3800

# 6개 카테고리 - 자산가 페르소나 정조준
SEARCH_KEYWORDS = {
    # 부산·해운대 직접 호재 (가장 핵심)
    'busan': [
        '해운대 재건축',
        '해운대 재개발',
        '마린시티 분양',
        '해운대 우동 아파트',
        '부산 부동산 정책',
        '부산 지구단위계획',
        '해운대 신축',
    ],
    # 자산가 친화 세금
    'tax': [
        '종합부동산세 완화',
        '양도소득세 인하',
        '상속세 공제 한도',
        '증여세 자녀 공제',
        '다주택자 세금 완화',
        '부동산 세제 개편',
    ],
    # 자산 시장 동향
    'market': [
        '서울 부동산 가격 상승',
        '강남 아파트 거래',
        '한국은행 기준금리',
        '코스피 급등',
        '비트코인 급등',
        '암호화폐 시장',
        '미국 연준 금리',
    ],
    # 입시·서울 대학
    'edu': [
        '대입 제도 개편',
        '의대 정원',
        '자사고 특목고',
        '서울대 입시',
        '연세대 고려대',
        '서울 주요대학',
    ],
    # 고액 자산가 투자 트렌드
    'wealth': [
        'PB 프라이빗뱅킹',
        '고액 자산가 투자',
        '미술품 경매',
        '럭셔리 부동산',
        '하이엔드 부동산',
    ],
    # 거시·국제 이슈 (자산 시장 영향 위주)
    'global': [
        '국제정세 자산시장',
        '환율 달러 영향',
        '글로벌 증시 영향',
        '안전자산 금 수요',
    ],
}


# ========== 텔레그램 안전 처리 ==========

def md_escape(text):
    """텔레그램 Markdown 충돌 문자를 전각 문자로 치환."""
    if not text: return ''
    replacements = {
        '*': '＊', '_': '－', '`': "'", '[': '〔', ']': '〕',
    }
    for bad, good in replacements.items():
        text = text.replace(bad, good)
    return text


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
        if e.code == 400 and 'parse' in body.lower():
            print(f'  ⚠️ Markdown 파싱 실패 → plain text로 재시도')
            data = urllib.parse.urlencode({
                'chat_id': TELEGRAM_CHAT_ID,
                'text': text,
                'disable_web_page_preview': 'true',
            }).encode('utf-8')
            req = urllib.request.Request(url, data=data, method='POST')
            with urllib.request.urlopen(req, timeout=15) as resp:
                return json.loads(resp.read().decode('utf-8'))
        raise RuntimeError(f'Telegram HTTP {e.code}: {body}')


def telegram_send(text):
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


# ========== 네이버 뉴스 API ==========

def search_naver(query, display=20, sort='date'):
    params = {
        'query': query,
        'display': display,
        'start': 1,
        'sort': sort,
    }
    url = f'{NAVER_API_URL}?{urllib.parse.urlencode(params)}'
    req = urllib.request.Request(url, headers={
        'X-Naver-Client-Id': NAVER_CLIENT_ID,
        'X-Naver-Client-Secret': NAVER_CLIENT_SECRET,
    })
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode('utf-8'))
    except urllib.error.HTTPError as e:
        body = e.read().decode('utf-8', errors='ignore')
        raise RuntimeError(f'Naver HTTP {e.code}: {body}')


def clean_html_tags(text):
    if not text: return ''
    text = re.sub(r'<[^>]+>', '', text)
    text = (text.replace('&quot;', '"').replace('&amp;', '&')
                .replace('&lt;', '<').replace('&gt;', '>')
                .replace('&apos;', "'").replace('&#39;', "'"))
    return text.strip()


def parse_pubdate(pubdate_str):
    try:
        from email.utils import parsedate_to_datetime
        dt = parsedate_to_datetime(pubdate_str)
        return dt.date()
    except Exception:
        return None


def collect_news(within_days=7):
    cutoff = datetime.now(ZoneInfo('Asia/Seoul')).date() - timedelta(days=within_days)
    all_articles = []

    for category, keywords in SEARCH_KEYWORDS.items():
        for keyword in keywords:
            try:
                result = search_naver(keyword, display=10)
                for item in result.get('items', []):
                    pub_date = parse_pubdate(item.get('pubDate', ''))
                    if not pub_date or pub_date < cutoff:
                        continue
                    all_articles.append({
                        'category': category,
                        'keyword': keyword,
                        'title': clean_html_tags(item.get('title', '')),
                        'description': clean_html_tags(item.get('description', '')),
                        'link': item.get('link', ''),
                        'originallink': item.get('originallink', ''),
                        'pubDate': item.get('pubDate', ''),
                        'pub_date': pub_date.isoformat(),
                    })
                time.sleep(0.1)
            except Exception as e:
                print(f'  WARN: keyword "{keyword}" failed: {e}')
                continue
        print(f'  [{category}] {len(keywords)}개 키워드 검색 완료')

    seen_titles = set()
    unique = []
    for art in all_articles:
        title_key = art['title'][:50]
        if title_key in seen_titles:
            continue
        seen_titles.add(title_key)
        unique.append(art)

    print(f'  총 {len(all_articles)}개 → 중복 제거 후 {len(unique)}개')
    return unique


# ========== Claude API ==========

def call_claude(prompt):
    headers = {
        'x-api-key': ANTHROPIC_API_KEY,
        'anthropic-version': '2023-06-01',
        'content-type': 'application/json',
    }
    body = {
        'model': MODEL,
        'max_tokens': 4096,
        'messages': [{'role': 'user', 'content': prompt}]
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
    parts = []
    for block in response.get('content', []):
        if block.get('type') == 'text':
            parts.append(block.get('text', ''))
    return '\n'.join(parts).strip()


def build_curation_prompt(articles):
    today = datetime.now(ZoneInfo('Asia/Seoul')).date()

    articles_text = ""
    for i, art in enumerate(articles, 1):
        articles_text += f"\n[{i}] ({art['category']}/{art['pub_date']}) {art['title']}\n"
        if art['description']:
            articles_text += f"     설명: {art['description'][:150]}\n"

    return f"""당신은 부산 해운대 마린시티 부동산 사무실의 디지털 사이니지 뉴스 큐레이터입니다.

**오늘 날짜:** {today.strftime('%Y년 %m월 %d일')}

**타겟 청중:** 마린시티 거주자·매수 관심층. 자산 5억 이상 보유한 재력가·투자자·자산가 계층입니다. 자기 자산을 지키고 늘리는 데 직결되는 정보에 관심이 큽니다.

**아래는 네이버 뉴스에서 수집한 실제 기사 {len(articles)}개입니다.**
이 중에서 **정확히 10개**를 선별해 주세요.

**카테고리별 권장 후보 수 (총 10개):**
- `busan` (부산·해운대 직접 호재): **2-3개** (가장 중요, 사무실 지역과 직결)
- `tax` (자산가 친화 세금 정책): **2개**
- `market` (자산 시장 동향): **2-3개**
- `edu` (입시·서울 대학): **1-2개**
- `wealth` (고액 자산가 투자 트렌드): **1-2개**
- `global` (거시·국제): **0-1개** ← 최소화. 자산 시장에 직접 영향 주는 것만.

**선별 기준 (자산가 관점):**

✅ **반드시 선택:**
- 부산·해운대 부동산 가격에 긍정적인 영향
- 부자들에게 유리한 세금 정책 변화 (감면, 한도 상향)
- 서울/강남 부동산 가격 상승 신호 (부산 자산가들이 벤치마크)
- 코인·주식의 의미 있는 급등 (개별 종목 추천 X, 시장 흐름 O)
- 입시제도 변동 (서울 주요대학 진학 영향)
- 고액 자산가 투자 트렌드 (PB, 미술품, 럭셔리 부동산)

❌ **절대 제외:**
- 부산이 아닌 타지역 일반 뉴스 (울산·의왕·안산 등 - 자산가에게 무관)
- "폭락", "위기", "부도" 등 부정적 어조
- 일반 행정 뉴스 (신고기한 안내, 절차 변경 등)
- 광고성 분양 홍보, 과장된 기사
- 정치 양극화 이슈
- 사기·범죄·사고

⚠️ **중요한 지역 필터:**
- `busan` 카테고리는 **반드시 부산광역시 또는 해운대구 관련 뉴스만**
- 제목에 "울산", "의왕", "안산", "수원" 등 타지역명이 있으면 제외
- 의심되면 description을 확인해서 진짜 부산 관련인지 판단

**출력 규칙:**
- 반드시 위 리스트의 번호 중에서만 선택
- 헤드라인은 위 리스트의 제목을 그대로 사용 또는 60자 이내로 다듬기
- 절대 새 헤드라인 만들지 말 것
- 헤드라인에 별표(*), 밑줄(_), 백틱(`), 대괄호([]) 같은 특수문자 제거

**기사 리스트:**
{articles_text}

**출력 형식 (JSON만, 설명 없이):**

```json
{{
  "candidates": [
    {{
      "n": 1,
      "tag": "busan",
      "source_index": 5,
      "headline": "위 리스트 [5]번 제목 (60자 이내)",
      "date": "MM.DD"
    }}
  ]
}}
```

**태그:** busan / tax / market / edu / wealth / global"""


def parse_candidates(text):
    if '```json' in text:
        start = text.find('```json') + 7
        end = text.find('```', start)
        json_str = text[start:end].strip()
    elif '```' in text:
        start = text.find('```') + 3
        end = text.find('```', start)
        json_str = text[start:end].strip()
    else:
        json_str = text.strip()
    return json.loads(json_str)


def format_telegram_message(data, articles, run_dt):
    today_str = run_dt.strftime('%Y.%m.%d')
    weekday = ['월', '화', '수', '목', '금', '토', '일'][run_dt.weekday()]

    lines = [
        f'📰 *주간 사이니지 뉴스 후보*  ({today_str} {weekday})',
        '',
        f'네이버 뉴스 검증된 기사 중 *10개*를 추천드립니다.',
        '이 중 *4개를 선택*해 주세요.',
        '━━━━━━━━━━━━━━━━',
    ]

    tag_emoji = {
        'busan': '🏙️',
        'tax': '💰',
        'market': '📈',
        'edu': '🎓',
        'wealth': '💎',
        'global': '🌍',
    }

    for c in data.get('candidates', []):
        n = c.get('n', '?')
        tag = c.get('tag', 'busan')
        emoji = tag_emoji.get(tag, '📰')
        headline = md_escape(c.get('headline', ''))
        date = md_escape(c.get('date', ''))
        source_idx = c.get('source_index', 0)

        source_name = ''
        if 0 < source_idx <= len(articles):
            art = articles[source_idx - 1]
            link = art.get('originallink') or art.get('link', '')
            try:
                from urllib.parse import urlparse
                domain = urlparse(link).netloc.replace('www.', '')
                source_name = md_escape(domain.split('.')[0] if domain else '')
            except:
                source_name = ''

        lines.append('')
        lines.append(f'*[{n}]* {emoji} {tag.upper()}')
        lines.append(f'    {headline}')
        meta = []
        if date: meta.append(date)
        if source_name: meta.append(source_name)
        if meta:
            lines.append(f'    ({" · ".join(meta)})')

    lines.extend([
        '',
        '━━━━━━━━━━━━━━━━',
        '💬 *다음 단계:*',
        'Claude 채팅에 답변:',
        '"이번 주 뉴스 X, Y, Z, W번 선택"',
        '→ news＿curated.json 새 내용 받기',
        '→ GitHub에서 commit',
    ])

    return '\n'.join(lines)


# ========== 메인 ==========

def main():
    missing = [k for k, v in {
        'NAVER_CLIENT_ID': NAVER_CLIENT_ID,
        'NAVER_CLIENT_SECRET': NAVER_CLIENT_SECRET,
        'ANTHROPIC_API_KEY': ANTHROPIC_API_KEY,
        'TELEGRAM_BOT_TOKEN': TELEGRAM_BOT_TOKEN,
        'TELEGRAM_CHAT_ID': TELEGRAM_CHAT_ID,
    }.items() if not v]
    if missing:
        print(f'❌ 환경 변수 누락: {", ".join(missing)}')
        sys.exit(1)

    run_dt = datetime.now(ZoneInfo('Asia/Seoul'))
    print(f'=== 주간 뉴스 큐레이션 v3 ({run_dt.isoformat()}) ===')

    print('🔍 네이버 뉴스 수집...')
    articles = collect_news(within_days=7)

    if not articles:
        msg = '⚠️ *주간 뉴스 큐레이션*\n\n네이버 검색에서 1주일 이내 기사를 찾지 못했습니다.'
        telegram_send(msg)
        return

    by_cat = {}
    for art in articles:
        by_cat.setdefault(art['category'], []).append(art)
    max_per_cat = 15
    capped = []
    for cat, arts in by_cat.items():
        capped.extend(arts[:max_per_cat])
    print(f'  Claude에 전달: {len(capped)}개 기사')

    print('🤖 Claude에 선별 요청...')
    prompt = build_curation_prompt(capped)
    try:
        response = call_claude(prompt)
    except Exception as e:
        print(f'❌ Claude API 실패: {e}')
        try:
            telegram_send(f'⚠️ 주간 뉴스 큐레이션 실패\n\n{str(e)[:500]}')
        except: pass
        sys.exit(1)

    text = extract_text_blocks(response)
    print(f'  응답 길이: {len(text)}자')

    try:
        data = parse_candidates(text)
    except Exception as e:
        print(f'❌ JSON 파싱 실패: {e}')
        fallback = f'⚠️ 주간 뉴스 자동 파싱 실패\n\n{md_escape(text[:3500])}'
        telegram_send(fallback)
        sys.exit(1)

    candidates = data.get('candidates', [])
    print(f'  추천 후보: {len(candidates)}개')

    if not candidates:
        telegram_send('⚠️ 추천할 뉴스 후보가 없습니다.')
        return

    # 카테고리 분포 출력 (디버깅용)
    cat_count = {}
    for c in candidates:
        cat_count[c.get('tag', '?')] = cat_count.get(c.get('tag', '?'), 0) + 1
    print(f'  카테고리 분포: {cat_count}')

    msg = format_telegram_message(data, capped, run_dt)
    print(f'  메시지 길이: {len(msg)}자')

    try:
        telegram_send(msg)
        print('✅ 텔레그램 전송 완료')
    except Exception as e:
        print(f'❌ 텔레그램 전송 실패: {e}')
        sys.exit(1)

    os.makedirs('cache', exist_ok=True)
    with open('cache/last_news_candidates.json', 'w', encoding='utf-8') as f:
        json.dump({
            'generated_at': run_dt.isoformat(),
            'candidates': candidates,
            'source_articles': capped,
        }, f, ensure_ascii=False, indent=2)
    print('✅ 캐시 저장 완료')


if __name__ == '__main__':
    main()
