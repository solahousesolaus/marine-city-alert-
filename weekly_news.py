#!/usr/bin/env python3
"""
주간 뉴스 후보 큐레이션 v2.1 (네이버 API + Markdown 안전 이스케이프)

흐름:
1. 네이버 뉴스 API로 키워드별 실제 기사 수집 (가짜 불가능)
2. Claude API에 "이 중 10개 선별" 요청 (선별만, 생성 X)
3. 텔레그램으로 발송 (Markdown 특수문자 자동 이스케이프)

환경 변수 (GitHub Secrets):
- NAVER_CLIENT_ID
- NAVER_CLIENT_SECRET
- ANTHROPIC_API_KEY
- TELEGRAM_BOT_TOKEN
- TELEGRAM_CHAT_ID
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

SEARCH_KEYWORDS = {
    'tax': [
        '양도소득세 부동산',
        '종합부동산세',
        '상속세 개정',
        '증여세',
        '다주택자 세금',
    ],
    'invest': [
        '부동산 시장 전망',
        '한국은행 기준금리',
        '코스피 시황',
        '부산 부동산',
    ],
    'edu': [
        '대입 제도 개편',
        '의대 정원',
        '자사고 특목고',
    ],
    'local': [
        '해운대 우동 아파트',
        '마린시티 분양',
        '해운대 재건축',
    ],
}


# ========== 텔레그램 안전 처리 ==========

def md_escape(text):
    """
    텔레그램 Markdown(legacy) 모드에서 사용자 입력 텍스트를 안전하게.
    문제 문자: * _ ` [
    이 문자들이 짝이 안 맞으면 메시지 전체 파싱 실패.
    가장 안전한 방법은 모두 제거하거나 비슷한 문자로 치환.
    """
    if not text: return ''
    # Markdown 특수문자를 비슷한 안전 문자로 치환
    replacements = {
        '*': '＊',  # 전각 별표 (시각적으로 비슷, Markdown 영향 없음)
        '_': '－',  # 전각 하이픈
        '`': "'",   # 작은따옴표
        '[': '〔',  # 전각 대괄호
        ']': '〕',
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
        # Markdown 파싱 실패 시 plain text로 재시도
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
                result = search_naver(keyword, display=15)
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

**타겟 청중:** 마린시티 거주·매수 관심층 (재력가·투자자·자산가)

**아래는 네이버 뉴스에서 수집한 실제 기사 {len(articles)}개입니다.**
이 중에서 **정확히 10개**를 선별해 주세요. 영역별 균형:
- 세금(tax): 2-3개
- 투자(invest): 3개
- 교육(edu): 2개
- 우동·마린시티(local): 2-3개

**선별 기준:**
- ✅ 자산가가 관심 가질만한 객관적 정보
- ✅ 긍정/중립적 톤
- ✅ 정책 발표, 통계, 시장 분석
- ❌ 부정적 뉴스 (폭락, 부도, 위기) 제외
- ❌ 사기·범죄·사고 제외
- ❌ 정치 양극화 이슈 제외

**중요한 규칙:**
- **반드시 위 리스트의 번호 중에서만 선택**
- 헤드라인은 위 리스트의 제목을 **그대로 사용** 또는 60자 이내로 약간 다듬기
- 절대 **새 헤드라인 만들거나 리스트에 없는 뉴스 추가 금지**
- 헤드라인에서 별표(*), 밑줄(_), 백틱(`), 대괄호([]) 등 특수문자는 제거하거나 일반 문자로 변경

**기사 리스트:**
{articles_text}

**출력 형식 (JSON만):**

```json
{{
  "candidates": [
    {{
      "n": 1,
      "tag": "tax",
      "source_index": 5,
      "headline": "위 리스트 [5]번 제목 (60자 이내, 특수문자 없음)",
      "date": "MM.DD"
    }}
  ]
}}
```

**태그:** tax / invest / edu / local"""


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
        'tax': '💰',
        'invest': '📈',
        'edu': '🎓',
        'local': '📍',
    }

    for c in data.get('candidates', []):
        n = c.get('n', '?')
        tag = c.get('tag', 'local')
        emoji = tag_emoji.get(tag, '📰')
        headline = md_escape(c.get('headline', ''))  # ⭐ 이스케이프
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
    print(f'=== 주간 뉴스 큐레이션 v2.1 ({run_dt.isoformat()}) ===')

    print('🔍 네이버 뉴스 수집...')
    articles = collect_news(within_days=7)

    if not articles:
        msg = '⚠️ *주간 뉴스 큐레이션*\n\n네이버 검색에서 1주일 이내 기사를 찾지 못했습니다.'
        telegram_send(msg)
        return

    by_cat = {}
    for art in articles:
        by_cat.setdefault(art['category'], []).append(art)
    max_per_cat = 20
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
