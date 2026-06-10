#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
DERIV-READ 데이터 파이프라인 v1.0
GitHub Actions에서 일 2회 실행 → deriv_data.json 생성 → 판독기가 자동 로드
- 미국: yfinance (VIX, VIX9D, SPX)  [검증 완료]
- 한국: 네이버 금융 (VKOSPI, KOSPI200)  [표준 경로, Actions에서 작동]
- KRX 파생(P/C OI, 외인 옵션): 선택 모듈 — 하단 TODO 참고
- CBOE Equity P/C: best-effort (실패 시 null → 판독기에서 수동 입력)
"""
import json, re, sys, datetime, traceback
import requests

HDR = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
KST = datetime.timezone(datetime.timedelta(hours=9))
out = {"updated_kst": datetime.datetime.now(KST).strftime("%Y-%m-%d %H:%M"), "errors": []}

def safe(fn, label):
    try:
        fn()
    except Exception as e:
        out["errors"].append(f"{label}: {e.__class__.__name__}")
        traceback.print_exc()

# ─────────────────────────────── 미국 ───────────────────────────────
def fetch_us():
    import yfinance as yf
    for tk, key in [("^VIX", "vix"), ("^VIX9D", "vix9d"), ("^GSPC", "spx")]:
        d = yf.download(tk, period="1mo", progress=False)["Close"]
        # yfinance 멀티인덱스 대응
        if hasattr(d, "columns"):
            d = d[tk]
        c = d.dropna()
        if len(c) >= 2:
            out[key] = round(float(c.iloc[-1]), 2)
            out[key + "_chg_pct"] = round(float(c.iloc[-1] / c.iloc[-2] - 1) * 100, 2)

# ─────────────────────────────── 한국 (네이버) ───────────────────────────────
def naver_index(code):
    """1차: 모바일 JSON API → 2차: PC 페이지 정규식"""
    try:
        r = requests.get(f"https://m.stock.naver.com/api/index/{code}/basic", headers=HDR, timeout=10)
        j = r.json()
        return float(str(j["closePrice"]).replace(",", "")), float(j["fluctuationsRatio"])
    except Exception:
        r = requests.get(f"https://finance.naver.com/sise/sise_index.naver?code={code}", headers=HDR, timeout=10)
        m = re.search(r'id="now_value">\s*([\d,.]+)', r.text)
        c = re.search(r'id="change_value_and_rate"[^>]*>.*?([+\-]?[\d.]+)\s*%', r.text, re.S)
        return float(m.group(1).replace(",", "")), (float(c.group(1)) if c else None)

def fetch_kr():
    v, vc = naver_index("VKOSPI")
    out["vkospi"], out["vkospi_chg_pct"] = v, vc
    k, kc = naver_index("KPI200")
    out["k200"], out["k200_chg_pct"] = k, kc

# ─────────────────────────────── CBOE P/C (best-effort) ───────────────────────────────
def fetch_cboe():
    # CBOE 일일 통계 엔드포인트는 변경이 잦음 — 실패해도 전체 파이프라인은 진행
    r = requests.get(
        "https://cdn.cboe.com/api/global/us_indices/daily_prices/market_statistics.json",
        headers=HDR, timeout=10)
    if r.ok:
        j = r.json()
        # 구조 확인 후 equity P/C 키를 매핑할 것 (배포 후 1회 점검)
        out["us_equity_pc_raw"] = j if isinstance(j, dict) else None

# ─────────────────────────────── KRX 파생 (선택 모듈 / TODO) ───────────────────────────────
def fetch_krx():
    """
    KRX 정보데이터시스템은 공개 REST가 아니라 화면별 bld 파라미터 POST 방식.
    활성화 방법 (5분 작업):
      1. data.krx.co.kr 접속 → [파생상품] → [거래실적] → 투자자별 거래실적(옵션) 화면 열기
      2. F12 → Network 탭 → 조회 클릭 → 'getJsonData.cmd' 요청 선택
      3. Payload(Form Data) 전체 복사 → 아래 PAYLOAD에 dict로 붙여넣기
      4. 미결제약정(P/C) 화면도 동일하게 반복
    bld 코드는 화면마다 고정이므로 한 번 캡처하면 계속 작동한다.
    """
    PAYLOAD = None  # ← 캡처한 Form Data를 dict로
    if not PAYLOAD:
        return
    r = requests.post("https://data.krx.co.kr/comm/bldAttendant/getJsonData.cmd",
                      data=PAYLOAD, headers={**HDR, "Referer": "https://data.krx.co.kr/"}, timeout=15)
    j = r.json()
    # 예: 콜/풋 미결제 합산 → out["kr_pc_oi"], 외인 풋/콜 순매수 → out["kr_foreign_opt"]
    out["krx_raw_sample"] = str(j)[:200]

safe(fetch_us, "US")
safe(fetch_kr, "KR-Naver")
safe(fetch_cboe, "CBOE")
safe(fetch_krx, "KRX")

with open("deriv_data.json", "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=2)
print(json.dumps(out, ensure_ascii=False, indent=2))
sys.exit(0)
