#!/usr/bin/env python3
"""
뉴제니스마린 부동산 - 우동 실거래 일일 알림

매일 아침 7시 (KST)에 GitHub Actions가 자동 실행:
1. 국토부 API에서 해운대구 이번 달/지난 달 거래 조회
2. 우동(umdNm == '우동') 거래만 필터
3. 이전에 본 거래 ID와 비교 → 신규/취소 변경분 추출
4. 텔레그램 봇으로 메시지 전송
5. 본 거래 ID들을 cache/seen_ids.json에 저장 (다음 실행 시 비교 기준)

환경 변수 (GitHub Secrets):
- SERVICE_KEY: 국토부 실거래가 API 키
- TELEGRAM_BOT_TOKEN: 텔레그램 봇 토큰
- TELEGRAM_CHAT_ID: 메시지 받을 본인 Chat ID
"""

import json
import os
import sys
import time
import urllib.parse
import urllib.request
import urllib.error
import xml.etree.ElementTree as ET
from datetime import date, datetime
from collections import defaultdict
from zoneinfo import ZoneInfo

# Windows 콘솔 한글 안전화 (GitHub Actions는 Linux이므로 무관, 로컬 테스트용)
if sys.platform == 'win32':
    try:
        import io
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    except Exception:
        pass


# ========== 설정 ==========

# 환경 변수에서 키 로드 (GitHub Secrets)
SERVICE_KEY = os.environ.get('SERVICE_KEY', '').strip()
TELEGRAM_BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN', '').strip()
TELEGRAM_CHAT_ID = os.environ.get('TELEGRAM_CHAT_ID', '').strip()

LAWD_CD = '26350'  # 해운대구
TARGET_DONG = '우동'

API_URL = 'https://apis.data.go.kr/1613000/RTMSDataSvcAptTradeDev/getRTMSDataSvcAptTradeDev'
CACHE_DIR = 'cache'
SEEN_PATH = os.path.join(CACHE_DIR, 'seen_ids.json')

# 마린시티 단지 (강조 표시용)
MARINE_CITY_KEYWORDS = [
    '경남마리나', '대우마리나', '대우트럼프월드마린', '두산위브포세이돈',
    '마린시티두산위브포세이돈', '마린시티자이', '해운대두산위브더제니스',
    '해운대아이파크', '트럼프월드마린',
]

# 평형대 분류 (전용면적 m²)
SIZE_BUCKETS = [
    ('소형', 0, 55),
    ('25평대', 55, 75),
    ('34평대', 75, 100),
    ('45평대', 100, 130),
    ('55평대', 130, 165),
    ('65평+', 165, 9999),
]


# ========== 유틸 ==========

def to_float(s):
    try:
        return float(str(s).replace(',', '').strip())
    except (ValueError, AttributeError):
        return None


def to_int(s):
    try:
        return int(str(s).replace(',', '').strip())
    except (ValueError, AttributeError):
        return None


def m2_to_pyung(m2):
    return round(m2 / 3.3058, 1) if m2 else None


def bucket_of(m2):
    if m2 is None:
        return '미상'
    for name, lo, hi in SIZE_BUCKETS:
        if lo <= m2 < hi:
            return name
    return '미상'


def is_marine_city(apt_name):
    if not apt_name:
        return False
    return any(k in apt_name for k in MARINE_CITY_KEYWORDS)


# ========== 국토부 API ==========

def fetch_page(yyyymm, page_no=1, num_rows=1000):
    params = {
        'serviceKey': SERVICE_KEY,
        'LAWD_CD': LAWD_CD,
        'DEAL_YMD': yyyymm,
        'numOfRows': str(num_rows),
        'pageNo': str(page_no),
    }
    url = f'{API_URL}?{urllib.parse.urlencode(params)}'
    req = urllib.request.Request(url, headers={'User-Agent': 'marine-alert/1.0'})

    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return resp.read().decode('utf-8')
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError):
            if attempt == 2:
                raise
            time.sleep(2 ** attempt)


def fetch_month(yyyymm):
    """한 달치 전체 데이터 (페이지네이션). 캐시 안 함 - 최신 상태가 중요."""
    all_items = []
    page_no = 1
    num_rows = 1000

    while True:
        xml_text = fetch_page(yyyymm, page_no=page_no, num_rows=num_rows)
        root = ET.fromstring(xml_text)

        rc = root.findtext('.//resultCode') or root.findtext('.//returnReasonCode')
        if rc and rc != '000':
            msg = root.findtext('.//resultMsg') or root.findtext('.//returnAuthMsg')
            raise RuntimeError(f'API 오류 ({yyyymm}): code={rc} msg={msg}')

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


# ========== 거래 정규화 ==========

def make_deal_id(raw):
    """거래 고유 식별자: 국토부의 aptSeq + 계약일 + 면적 + 층 + 금액 조합."""
    return '|'.join([
        raw.get('aptSeq', ''),
        raw.get('dealYear', ''),
        raw.get('dealMonth', ''),
        raw.get('dealDay', ''),
        raw.get('excluUseAr', ''),
        raw.get('floor', ''),
        raw.get('dealAmount', '').replace(',', ''),
    ])


def normalize(raw):
    exclu_m2 = to_float(raw.get('excluUseAr', ''))
    amount_manwon = to_int(raw.get('dealAmount', ''))
    pyung = m2_to_pyung(exclu_m2)

    try:
        y = int(raw.get('dealYear', '0'))
        m = int(raw.get('dealMonth', '0'))
        d = int(raw.get('dealDay', '0'))
        deal_date = f'{y:04d}-{m:02d}-{d:02d}' if y else ''
    except (ValueError, TypeError):
        deal_date = ''

    cancelled = bool((raw.get('cdealType', '') or '').strip())

    return {
        'deal_id': make_deal_id(raw),
        'deal_date': deal_date,
        'apt_name': raw.get('aptNm', '').strip(),
        'apt_dong': raw.get('aptDong', '').strip(),
        'umd_nm': raw.get('umdNm', '').strip(),
        'floor': to_int(raw.get('floor', '')),
        'exclu_m2': exclu_m2,
        'pyung': pyung,
        'size_bucket': bucket_of(exclu_m2),
        'amount_manwon': amount_manwon,
        'amount_eok': round(amount_manwon / 10000, 2) if amount_manwon else None,
        'price_per_pyung': round(amount_manwon / pyung) if amount_manwon and pyung else None,
        'build_year': to_int(raw.get('buildYear', '')),
        'cancelled': cancelled,
        'cancel_day': raw.get('cdealDay', '').strip(),
        'is_marine_city': is_marine_city(raw.get('aptNm', '')),
    }


# ========== 비교 분석: 같은 단지 같은 평형 직전 거래 / 전고점 ==========

def find_prev_deal(deal, history):
    """같은 단지·같은 평형대의 가장 최근(이전) 활성 거래."""
    candidates = [
        h for h in history
        if h['apt_name'] == deal['apt_name']
        and h['size_bucket'] == deal['size_bucket']
        and not h['cancelled']
        and h['deal_date'] < deal['deal_date']
        and h['amount_eok']
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda h: h['deal_date'])


def find_all_time_high(deal, history):
    """같은 단지·같은 평형대 전고점."""
    candidates = [
        h for h in history
        if h['apt_name'] == deal['apt_name']
        and h['size_bucket'] == deal['size_bucket']
        and not h['cancelled']
        and h['amount_eok']
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda h: h['amount_eok'])


# ========== 캐시 (어제까지 본 거래 ID들) ==========

def load_seen():
    if not os.path.exists(SEEN_PATH):
        return {'ids': [], 'cancelled_ids': [], 'last_run': None}
    with open(SEEN_PATH, 'r', encoding='utf-8') as f:
        return json.load(f)


def save_seen(seen):
    os.makedirs(CACHE_DIR, exist_ok=True)
    with open(SEEN_PATH, 'w', encoding='utf-8') as f:
        json.dump(seen, f, ensure_ascii=False, indent=2)


# ========== 텔레그램 ==========

def telegram_send(text):
    """Telegram Bot API로 메시지 전송 (Markdown 형식)."""
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


def fmt_eok(eok):
    """억 단위 포맷. 12.5 → '12.5억'."""
    if eok is None:
        return '?'
    if eok == int(eok):
        return f'{int(eok)}억'
    return f'{eok:.2f}억'.rstrip('0').rstrip('.')


def fmt_change(curr, prev):
    """등락 포맷. (curr_eok, prev_eok) → '↑ 1.2억 (+8.5%)' 또는 '↓ ...' 또는 '동일'."""
    if not curr or not prev or prev == 0:
        return ''
    diff = curr - prev
    pct = diff / prev * 100
    if abs(pct) < 0.5:
        return '➡️ 비슷'
    arrow = '🔺' if diff > 0 else '🔻'
    sign = '+' if diff > 0 else ''
    return f'{arrow} {sign}{diff:+.2f}억 ({sign}{pct:+.1f}%)'.replace('++', '+').replace('--', '-')


def build_message(new_deals, cancelled_deals, history, run_dt):
    """텔레그램 메시지 생성."""
    lines = []
    date_str = run_dt.strftime('%Y.%m.%d')
    weekday = ['월', '화', '수', '목', '금', '토', '일'][run_dt.weekday()]

    if not new_deals and not cancelled_deals:
        lines.append(f'☀️ *우동 실거래 알림*  ({date_str} {weekday})')
        lines.append('')
        lines.append('새로 신고된 거래가 없습니다.')
        lines.append('')
        lines.append('_내일 아침 7시에 다시 확인합니다._')
        return '\n'.join(lines)

    # 헤더
    lines.append(f'🏢 *우동 실거래 알림*  ({date_str} {weekday})')
    lines.append('')
    parts = []
    if new_deals:
        parts.append(f'신규 *{len(new_deals)}건*')
    if cancelled_deals:
        parts.append(f'취소 *{len(cancelled_deals)}건*')
    lines.append(f"📊 {' · '.join(parts)}")
    lines.append('━━━━━━━━━━━━━━━━')

    # 신규 거래 (마린시티 우선 → 비마린시티)
    if new_deals:
        marine = [d for d in new_deals if d['is_marine_city']]
        others = [d for d in new_deals if not d['is_marine_city']]
        ordered = marine + others

        for i, d in enumerate(ordered, 1):
            lines.append('')
            star = '⭐ ' if d['is_marine_city'] else ''
            lines.append(f"*{i}. {star}{d['apt_name']}*")

            # 기본 정보 한 줄
            info_parts = []
            if d['apt_dong']:
                info_parts.append(f"{d['apt_dong']}동")
            if d['floor']:
                info_parts.append(f"{d['floor']}층")
            if d['size_bucket'] != '미상':
                info_parts.append(d['size_bucket'])
            if d['pyung']:
                info_parts.append(f"전용 {d['pyung']}평")
            if info_parts:
                lines.append(f"   {' · '.join(info_parts)}")

            # 가격
            price_line = f"   💰 *{fmt_eok(d['amount_eok'])}*"
            if d['price_per_pyung']:
                price_line += f"  (평당 {d['price_per_pyung']:,}만)"
            lines.append(price_line)

            # 계약일
            lines.append(f"   📅 계약일 {d['deal_date']}")

            # 직전 거래 비교
            prev = find_prev_deal(d, history)
            if prev:
                change = fmt_change(d['amount_eok'], prev['amount_eok'])
                lines.append(f"   📈 직전 동평형 {fmt_eok(prev['amount_eok'])} ({prev['deal_date']})  {change}")

            # 전고점 돌파 체크
            ath = find_all_time_high(d, history)
            if ath and d['amount_eok'] and d['amount_eok'] > ath['amount_eok']:
                lines.append(f"   🎯 *전고점 돌파!* (이전 최고 {fmt_eok(ath['amount_eok'])})")
            elif ath and d['amount_eok'] and d['amount_eok'] >= ath['amount_eok'] * 0.97:
                lines.append(f"   🔥 전고점 근접 (이전 최고 {fmt_eok(ath['amount_eok'])})")

    # 취소 거래
    if cancelled_deals:
        lines.append('')
        lines.append('━━━━━━━━━━━━━━━━')
        lines.append('❌ *취소된 거래*')
        for d in cancelled_deals:
            star = '⭐ ' if d['is_marine_city'] else ''
            cancel_info = f" (취소일 {d['cancel_day']})" if d['cancel_day'] else ''
            lines.append(f"   • {star}{d['apt_name']}  {d['apt_dong']}동 {d['floor']}층  {fmt_eok(d['amount_eok'])} ({d['deal_date']}){cancel_info}")

    return '\n'.join(lines)


# ========== 메인 ==========

def yymm(y, m):
    return f'{y:04d}{m:02d}'


def main():
    # 0. 환경 변수 점검
    missing = [k for k, v in {
        'SERVICE_KEY': SERVICE_KEY,
        'TELEGRAM_BOT_TOKEN': TELEGRAM_BOT_TOKEN,
        'TELEGRAM_CHAT_ID': TELEGRAM_CHAT_ID,
    }.items() if not v]
    if missing:
        print(f'❌ 필수 환경 변수 누락: {", ".join(missing)}')
        sys.exit(1)

    run_dt = datetime.now(ZoneInfo('Asia/Seoul'))
    print(f'=== 우동 실거래 알림 실행 ({run_dt.isoformat()}) ===')

    # 1. 이번 달 + 지난 달 데이터 받기 (월 초에는 지난달 신고분이 늦게 들어옴)
    today = run_dt.date()
    months_to_fetch = [yymm(today.year, today.month)]
    if today.month == 1:
        months_to_fetch.insert(0, yymm(today.year - 1, 12))
    else:
        months_to_fetch.insert(0, yymm(today.year, today.month - 1))

    raw_all = []
    for ym in months_to_fetch:
        try:
            items = fetch_month(ym)
            print(f'  {ym}: {len(items)}건 수신')
            raw_all.extend(items)
            time.sleep(0.3)
        except Exception as e:
            print(f'❌ API 호출 실패 ({ym}): {e}')
            telegram_send(f'⚠️ *우동 알림 실패*\n\nAPI 호출 오류 ({ym}):\n`{e}`')
            sys.exit(1)

    # 2. 우동 거래만 필터
    udong = []
    for raw in raw_all:
        n = normalize(raw)
        if n['umd_nm'] == TARGET_DONG:
            udong.append(n)
    print(f'  우동 필터: {len(udong)}건')

    # 3. 이전에 본 ID와 비교
    seen = load_seen()
    seen_ids = set(seen.get('ids', []))
    seen_cancel_ids = set(seen.get('cancelled_ids', []))

    new_deals = []
    newly_cancelled = []
    current_ids = set()
    current_cancel_ids = set()

    for d in udong:
        if d['cancelled']:
            current_cancel_ids.add(d['deal_id'])
            # 처음 발견된 취소
            if d['deal_id'] not in seen_cancel_ids:
                newly_cancelled.append(d)
        else:
            current_ids.add(d['deal_id'])
            # 처음 발견된 신규 거래
            if d['deal_id'] not in seen_ids:
                new_deals.append(d)

    # 신규 거래는 계약일 최신순으로 정렬
    new_deals.sort(key=lambda x: x['deal_date'], reverse=True)
    newly_cancelled.sort(key=lambda x: x['deal_date'], reverse=True)

    print(f'  신규: {len(new_deals)}건  ·  새 취소: {len(newly_cancelled)}건')

    # 4. 텔레그램 발송 — history는 우동 활성 거래 전체 (직전·전고점 비교용)
    history = [d for d in udong if not d['cancelled']]
    msg = build_message(new_deals, newly_cancelled, history, run_dt)
    print('--- 메시지 미리보기 ---')
    print(msg)
    print('-----------------------')

    try:
        telegram_send(msg)
        print('✅ 텔레그램 전송 완료')
    except Exception as e:
        print(f'❌ 텔레그램 전송 실패: {e}')
        sys.exit(1)

    # 5. 본 ID 저장 (다음 실행 비교 기준)
    save_seen({
        'ids': sorted(seen_ids | current_ids),
        'cancelled_ids': sorted(seen_cancel_ids | current_cancel_ids),
        'last_run': run_dt.isoformat(),
    })
    print('✅ 캐시 저장 완료')


if __name__ == '__main__':
    main()
