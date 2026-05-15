#!/usr/bin/env python3
"""
디지털 사이니지 HTML 빌더

기능:
1. 우동 거래 데이터 가져오기 (daily_check.py와 동일 API)
2. 4개 페이지에 들어갈 데이터 가공
3. signage_template.html 채워서 docs/signage.html 생성
4. docs/qr.png는 텔레그램 그룹 QR (수동 배치)

GitHub Pages 활성화 시: docs/ 폴더가 자동 호스팅됨
출력 URL: https://<username>.github.io/<repo>/signage.html
"""

import json
import os
import sys
import time
import urllib.parse
import urllib.request
import urllib.error
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta
from collections import defaultdict
from zoneinfo import ZoneInfo

SERVICE_KEY = os.environ.get('SERVICE_KEY', '').strip()
LAWD_CD = '26350'
TARGET_DONG = '우동'

API_URL = 'https://apis.data.go.kr/1613000/RTMSDataSvcAptTradeDev/getRTMSDataSvcAptTradeDev'

MARINE_CITY_KEYWORDS = [
    '경남마리나', '대우마리나', '대우트럼프월드마린', '두산위브포세이돈',
    '마린시티두산위브포세이돈', '마린시티자이', '해운대두산위브더제니스',
]

SIZE_BUCKETS = [
    ('소형', 0, 55), ('25평대', 55, 75), ('34평대', 75, 100),
    ('45평대', 100, 130), ('55평대', 130, 165), ('65평+', 165, 9999),
]

OUT_DIR = 'docs'
TEMPLATE_PATH = 'templates/signage_template.html'
NEWS_PATH = 'news_curated.json'


# ========== 유틸 ==========

def to_float(s):
    try: return float(str(s).replace(',', '').strip())
    except: return None

def to_int(s):
    try: return int(str(s).replace(',', '').strip())
    except: return None

def m2_to_pyung(m2):
    return round(m2 / 3.3058, 1) if m2 else None

def bucket_of(m2):
    if m2 is None: return '미상'
    for name, lo, hi in SIZE_BUCKETS:
        if lo <= m2 < hi: return name
    return '미상'

def is_marine(apt):
    return any(k in apt for k in MARINE_CITY_KEYWORDS)


# ========== API ==========

def fetch_page(yyyymm, page_no=1, num_rows=1000):
    params = {
        'serviceKey': SERVICE_KEY, 'LAWD_CD': LAWD_CD,
        'DEAL_YMD': yyyymm, 'numOfRows': str(num_rows), 'pageNo': str(page_no),
    }
    url = f'{API_URL}?{urllib.parse.urlencode(params)}'
    req = urllib.request.Request(url, headers={'User-Agent': 'signage-builder/1.0'})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return resp.read().decode('utf-8')
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError):
            if attempt == 2: raise
            time.sleep(2 ** attempt)


def fetch_month(yyyymm):
    all_items = []
    page_no = 1
    num_rows = 1000
    while True:
        xml_text = fetch_page(yyyymm, page_no=page_no, num_rows=num_rows)
        root = ET.fromstring(xml_text)
        rc = root.findtext('.//resultCode') or root.findtext('.//returnReasonCode')
        if rc and rc != '000':
            raise RuntimeError(f'API 오류 ({yyyymm}): code={rc}')
        items = []
        for item in root.findall('.//item'):
            row = {child.tag: (child.text or '').strip() for child in item}
            items.append(row)
        all_items.extend(items)
        total = to_int(root.findtext('.//totalCount')) or 0
        if len(all_items) >= total or len(items) < num_rows:
            break
        page_no += 1
        time.sleep(0.2)
    return all_items


def safe_text(text):
    """Jinja2 템플릿에서 안전하게 표시할 수 있도록 위험 문자 제거."""
    if not text: return ''
    s = str(text)
    # Jinja2 문법 충돌 방지: {{ }} {% %}
    s = s.replace('{', '〔').replace('}', '〕')
    return s


def normalize(raw):
    exclu_m2 = to_float(raw.get('excluUseAr', ''))
    amount_manwon = to_int(raw.get('dealAmount', ''))
    pyung = m2_to_pyung(exclu_m2)
    try:
        y = int(raw.get('dealYear', '0'))
        m = int(raw.get('dealMonth', '0'))
        d = int(raw.get('dealDay', '0'))
        deal_date = f'{y:04d}-{m:02d}-{d:02d}' if y else ''
    except: deal_date = ''
    cancelled = bool((raw.get('cdealType', '') or '').strip())
    apt_name = safe_text(raw.get('aptNm', '').strip())
    return {
        'deal_date': deal_date,
        'apt_name': apt_name,
        'apt_dong': safe_text(raw.get('aptDong', '').strip()),
        'umd_nm': raw.get('umdNm', '').strip(),
        'floor': to_int(raw.get('floor', '')),
        'exclu_m2': exclu_m2,
        'pyung': pyung,
        'size_bucket': bucket_of(exclu_m2),
        'amount_manwon': amount_manwon,
        'amount_eok': round(amount_manwon / 10000, 2) if amount_manwon else None,
        'price_per_pyung': round(amount_manwon / pyung) if amount_manwon and pyung else None,
        'cancelled': cancelled,
        'is_marine': is_marine(apt_name),
    }


# ========== 데이터 분석 ==========

def fmt_eok(eok):
    if eok is None: return '?'
    if eok == int(eok): return f'{int(eok)}억'
    return f'{eok:.2f}'.rstrip('0').rstrip('.') + '억'


def find_prev_deal(deal, history):
    cands = [h for h in history
             if h['apt_name'] == deal['apt_name']
             and h['size_bucket'] == deal['size_bucket']
             and not h['cancelled']
             and h['deal_date'] < deal['deal_date']
             and h['amount_eok']]
    return max(cands, key=lambda h: h['deal_date']) if cands else None


def find_ath(deal, history):
    cands = [h for h in history
             if h['apt_name'] == deal['apt_name']
             and h['size_bucket'] == deal['size_bucket']
             and not h['cancelled']
             and h['amount_eok']]
    return max(cands, key=lambda h: h['amount_eok']) if cands else None


def deal_change_html(deal, history):
    prev = find_prev_deal(deal, history)
    ath = find_ath(deal, history)
    parts = []
    if prev:
        diff = deal['amount_eok'] - prev['amount_eok']
        pct = diff / prev['amount_eok'] * 100 if prev['amount_eok'] else 0
        if abs(pct) < 0.5:
            change = f'<span class="change-flat">➡️ 비슷</span>'
        elif diff > 0:
            change = f'<span class="change-up">🔺 +{diff:.2f}억 (+{pct:.1f}%)</span>'
        else:
            change = f'<span class="change-down">🔻 {diff:.2f}억 ({pct:.1f}%)</span>'
        parts.append(f'직전 동평형 {fmt_eok(prev["amount_eok"])} → {change}')
    elif ath:
        parts.append(f'<span class="change-flat">📊 동평형 첫 거래</span>')

    if ath and deal['amount_eok'] and deal['amount_eok'] > ath['amount_eok']:
        parts.append('<span class="change-up">🎯 신고가 돌파!</span>')
    return ' · '.join(parts) if parts else '동평형 비교 거래 없음'


def is_new_high(deal, history):
    """신고가 여부 (페이지 1 highlight용)"""
    ath = find_ath(deal, history)
    if not ath: return False
    return deal['amount_eok'] >= ath['amount_eok'] * 0.99  # 전고점 수준이면 highlight


def make_deal_info(deal):
    parts = []
    if deal['floor']: parts.append(f"{deal['floor']}F")
    if deal['size_bucket'] != '미상': parts.append(deal['size_bucket'])
    if deal['pyung']: parts.append(f"전용 {deal['pyung']}평")
    return ' · '.join(parts)


# ========== 사이니지 데이터 빌드 ==========

def build_signage_data(udong_active, run_dt):
    """4개 페이지 데이터를 한꺼번에 빌드."""
    history = udong_active

    # === 페이지 1: 최근 거래 (마린시티 우선, 최신 4건) ===
    sorted_by_date = sorted(udong_active, key=lambda d: d['deal_date'], reverse=True)
    marine_recent = [d for d in sorted_by_date if d['is_marine']][:4]
    others_recent = [d for d in sorted_by_date if not d['is_marine']][:4]
    page1_deals = (marine_recent + others_recent)[:4]

    recent_deals = []
    for d in page1_deals:
        if not d['amount_eok']: continue
        recent_deals.append({
            'apt': d['apt_name'],
            'is_marine': d['is_marine'],
            'info': make_deal_info(d),
            'price': fmt_eok(d['amount_eok']),
            'pp': f"평당 {d['price_per_pyung']:,}만" if d['price_per_pyung'] else '',
            'change_html': deal_change_html(d, history),
            'highlight': is_new_high(d, history),
        })

    # 페이지 1 제목·서브
    if recent_deals:
        latest_date = sorted_by_date[0]['deal_date']
        page1_title_html = '최근 신고된 <span class="accent">우동 거래</span>'
        page1_sub = f"국토부 실거래가 기준 · 최신 신고일 {latest_date}"
    else:
        page1_title_html = '<span class="accent">우동 거래</span> 정보'
        page1_sub = "현재 신고된 신규 거래가 없습니다"

    # === 페이지 2: 주간 시세 ===
    last_7d = (run_dt.date() - timedelta(days=7)).isoformat()
    week_deals = [d for d in udong_active if d['deal_date'] >= last_7d]
    marine_week = [d for d in week_deals if d['is_marine']]

    # 단지별·평형별 평균
    apt_size_groups = defaultdict(list)
    for d in week_deals:
        if d['is_marine'] and d['amount_eok']:
            apt_size_groups[(d['apt_name'], d['size_bucket'])].append(d['amount_eok'])

    week_table = []
    for (apt, size), prices in sorted(apt_size_groups.items(), key=lambda x: -sum(x[1])/len(x[1])):
        avg = sum(prices) / len(prices)
        # 변동 계산 (이번 주 vs 그 이전)
        prev_pool = [d['amount_eok'] for d in udong_active
                     if d['apt_name'] == apt and d['size_bucket'] == size
                     and d['deal_date'] < last_7d and not d['cancelled'] and d['amount_eok']]
        if prev_pool:
            prev_avg = sum(prev_pool) / len(prev_pool)
            pct = (avg - prev_avg) / prev_avg * 100 if prev_avg else 0
            if abs(pct) < 0.5:
                change, cls = '+0.0%', 'flat'
            elif pct > 0:
                change, cls = f'+{pct:.1f}%', 'up'
            else:
                change, cls = f'{pct:.1f}%', 'down'
        else:
            change, cls = '신규', 'flat'
        week_table.append({
            'apt': apt, 'size': size,
            'price': fmt_eok(avg), 'change': change, 'change_class': cls,
        })

    if not week_table:
        # 주간 마린시티 거래가 없을 때 → 우동 전체로
        for d in marine_week[:5]:
            if d['amount_eok']:
                week_table.append({
                    'apt': d['apt_name'], 'size': d['size_bucket'],
                    'price': fmt_eok(d['amount_eok']),
                    'change': '단일 거래', 'change_class': 'flat',
                })

    # 주간 통계
    active_amounts = [d['amount_eok'] for d in week_deals if d['amount_eok']]
    cancel_count = len([d for d in udong_active if d['cancelled']
                       and d['deal_date'] >= last_7d])
    pp_values = [d['price_per_pyung'] for d in week_deals if d['price_per_pyung']]
    week_stats = [
        {'label': '우동 신규 거래', 'value': len(week_deals), 'unit': '건'},
        {'label': '⭐ 마린시티 거래', 'value': len(marine_week), 'unit': '건'},
        {'label': '취소 거래', 'value': cancel_count, 'unit': '건'},
        {'label': '최고가', 'value': fmt_eok(max(active_amounts)) if active_amounts else '-', 'unit': ''},
        {'label': '평균 평당가', 'value': f'{int(sum(pp_values)/len(pp_values)):,}' if pp_values else '-', 'unit': '만'},
    ]

    week_start = (run_dt.date() - timedelta(days=7))
    week_range = f"{week_start.strftime('%Y.%m.%d')} ~ {run_dt.date().strftime('%m.%d')}"

    # === 페이지 4: 티커 (마린시티 우선, 최근 활성 거래 8건) ===
    ticker_pool = sorted([d for d in udong_active if d['amount_eok'] and not d['cancelled']],
                        key=lambda d: (not d['is_marine'], d['deal_date']), reverse=False)
    ticker_pool.sort(key=lambda d: (not d['is_marine'], -ord(d['deal_date'][0]) if d['deal_date'] else 0))
    # 마린시티 우선 + 최신순
    marine_ticker = sorted([d for d in udong_active if d['is_marine'] and d['amount_eok']],
                          key=lambda d: d['deal_date'], reverse=True)[:5]
    other_ticker = sorted([d for d in udong_active if not d['is_marine'] and d['amount_eok']],
                         key=lambda d: d['deal_date'], reverse=True)[:5]
    ticker_deals = marine_ticker + other_ticker

    ticker_html_parts = []
    for d in ticker_deals:
        prev = find_prev_deal(d, history)
        if prev and prev['amount_eok']:
            diff = d['amount_eok'] - prev['amount_eok']
            pct = diff / prev['amount_eok'] * 100 if prev['amount_eok'] else 0
            if abs(pct) < 0.5:
                change_html = '<span class="up">신규</span>'
            elif diff > 0:
                change_html = f'<span class="up">🔺 +{pct:.1f}%</span>'
            else:
                change_html = f'<span class="down">🔻 {pct:.1f}%</span>'
        else:
            change_html = '<span class="up">신규</span>'

        star = '⭐ ' if d['is_marine'] else ''
        ticker_html_parts.append(
            f'<span class="ticker-item">'
            f'<span class="apt">{star}{d["apt_name"]}</span>'
            f'<span>{d["size_bucket"]}</span>'
            f'<span class="price">{fmt_eok(d["amount_eok"])}</span>'
            f'{change_html}'
            f'</span><span class="ticker-sep">◆</span>'
        )

    ticker_html = ''.join(ticker_html_parts)
    if not ticker_html:
        ticker_html = '<span class="ticker-item"><span>실시간 거래 정보 갱신 중...</span></span>'

    # === 뉴스 (수동 큐레이션, 최대 5건) ===
    news_items = []
    if os.path.exists(NEWS_PATH):
        with open(NEWS_PATH, 'r', encoding='utf-8') as f:
            news_data = json.load(f)
        for n in news_data.get('items', [])[:5]:
            news_items.append({
                'tag': safe_text(n.get('tag', 'local').upper()),
                'tag_class': safe_text(n.get('tag', 'local')),
                'headline': safe_text(n.get('headline', '')),
                'date': safe_text(n.get('date', '')),
            })

    return {
        'data_updated': run_dt.strftime('%Y.%m.%d %H:%M KST'),
        'page1_title': page1_title_html,
        'page1_sub': page1_sub,
        'recent_deals': recent_deals,
        'week_range': week_range,
        'week_table': week_table,
        'week_stats': week_stats,
        'news_items': news_items,
        'ticker_html': ticker_html,
    }


# ========== 템플릿 렌더링 ==========

def render(template, data):
    """Jinja2로 템플릿 렌더링. safe 필터는 기본 제공됨."""
    try:
        from jinja2 import Environment
    except ImportError:
        print('❌ jinja2 미설치. pip install jinja2 필요')
        sys.exit(1)

    # autoescape=False: HTML 템플릿이므로 자동 이스케이프 비활성화
    # safe 필터는 jinja2 기본 제공 (markupsafe.Markup) - 덮어쓰지 않음
    env = Environment(autoescape=False)
    try:
        return env.from_string(template).render(**data)
    except Exception as e:
        # 디버깅: 어디가 문제인지 자세히 출력
        print(f'❌ 템플릿 렌더링 실패: {type(e).__name__}: {e}')
        # 라인 번호가 있으면 해당 줄 보여주기
        import re
        m = re.search(r'line (\d+)', str(e))
        if m:
            line_no = int(m.group(1))
            lines = template.split('\n')
            print(f'\n=== 템플릿 {line_no}번째 줄 주변 ===')
            for i in range(max(0, line_no - 3), min(len(lines), line_no + 3)):
                marker = '>>>' if i + 1 == line_no else '   '
                print(f'{marker} {i+1:4d}: {lines[i]}')
        # 데이터 키들 출력
        print('\n=== 전달된 데이터 ===')
        for k, v in data.items():
            preview = str(v)[:200] if not isinstance(v, list) else f'list(len={len(v)})'
            print(f'  {k}: {preview}')
        raise


# ========== 메인 ==========

def main():
    if not SERVICE_KEY:
        print('❌ SERVICE_KEY 누락'); sys.exit(1)

    run_dt = datetime.now(ZoneInfo('Asia/Seoul'))
    print(f'=== 사이니지 빌드 시작 ({run_dt.isoformat()}) ===')

    # 이번 달 + 지난 달 데이터
    today = run_dt.date()
    months = [f'{today.year:04d}{today.month:02d}']
    if today.month == 1:
        months.insert(0, f'{today.year - 1}12')
    else:
        months.insert(0, f'{today.year}{today.month - 1:02d}')

    raw_all = []
    for ym in months:
        items = fetch_month(ym)
        print(f'  {ym}: {len(items)}건')
        raw_all.extend(items)
        time.sleep(0.3)

    # 우동 + 활성 거래만
    udong_active = []
    for raw in raw_all:
        n = normalize(raw)
        if n['umd_nm'] == TARGET_DONG and not n['cancelled']:
            udong_active.append(n)
    print(f'  우동 활성 거래: {len(udong_active)}건')

    # 데이터 빌드
    data = build_signage_data(udong_active, run_dt)

    # 템플릿 로드 + 렌더
    with open(TEMPLATE_PATH, 'r', encoding='utf-8') as f:
        template = f.read()
    html = render(template, data)

    # 출력
    os.makedirs(OUT_DIR, exist_ok=True)
    out_path = os.path.join(OUT_DIR, 'signage.html')
    with open(out_path, 'w', encoding='utf-8') as f:
        f.write(html)
    print(f'✅ 사이니지 생성: {out_path}')
    print(f'   페이지1 거래: {len(data["recent_deals"])}건')
    print(f'   주간 테이블: {len(data["week_table"])}행')
    print(f'   티커: {len(data["ticker_html"]) // 100}KB 가량')


if __name__ == '__main__':
    main()
