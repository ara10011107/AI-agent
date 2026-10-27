"""데모용 가상(합성) 데이터를 생성합니다.

주의: 여기서 만드는 데이터는 실제 통계가 아닙니다. 대회 제출 시에는
제주빅데이터센터(bigdata.jeju.go.kr) 또는 제주데이터허브(jejudatahub.net)에서
받은 실제 데이터를 data/ 폴더에 넣어 사용하세요.

실행: python scripts/make_sample_data.py
"""

from pathlib import Path

import numpy as np
import pandas as pd

OUT_DIR = Path(__file__).resolve().parent.parent / "data" / "sample"
SEED = 20261027

AREAS = {
    "제주시": ["일도1동", "연동", "노형동", "애월읍", "한림읍", "구좌읍", "조천읍", "한경면"],
    "서귀포시": ["중문동", "서귀동", "성산읍", "표선면", "남원읍", "안덕면", "대정읍"],
}
# 관광 수요가 큰 지역일수록 관광객 비중이 높게 설정
TOURIST_WEIGHT = {
    "애월읍": 1.6, "구좌읍": 1.5, "성산읍": 1.7, "중문동": 1.8, "한림읍": 1.3,
    "조천읍": 1.2, "안덕면": 1.3, "표선면": 1.1, "남원읍": 1.0, "한경면": 0.9,
    "연동": 1.1, "노형동": 0.9, "일도1동": 0.8, "서귀동": 1.2, "대정읍": 0.9,
}
INDUSTRIES = {
    "음식": ["한식", "카페", "횟집", "패스트푸드"],
    "소매": ["편의점", "기념품", "대형마트", "농수산물"],
    "숙박": ["호텔", "펜션·민박"],
    "여가": ["레저·체험", "관광지입장"],
}
BASE_AMOUNT = {
    "한식": 18000, "카페": 8000, "횟집": 45000, "패스트푸드": 11000,
    "편의점": 9000, "기념품": 22000, "대형마트": 52000, "농수산물": 30000,
    "호텔": 160000, "펜션·민박": 110000, "레저·체험": 40000, "관광지입장": 15000,
}
USER_TYPES = ["도민", "내국인관광객", "외국인관광객"]
WEEKDAYS = ["월", "화", "수", "목", "금", "토", "일"]
# 월별 관광 계절성 (여름·가을 성수기)
SEASON = {1: 0.8, 2: 0.85, 3: 0.95, 4: 1.05, 5: 1.1, 6: 1.0,
          7: 1.3, 8: 1.4, 9: 1.05, 10: 1.15, 11: 0.9, 12: 0.85}


def make_card_spending(rng: np.random.Generator) -> pd.DataFrame:
    months = pd.period_range("2025-01", "2025-12", freq="M")
    rows = []
    for ym in months:
        season = SEASON[ym.month]
        for sigungu, dongs in AREAS.items():
            for dong in dongs:
                tw = TOURIST_WEIGHT.get(dong, 1.0)
                for major, minors in INDUSTRIES.items():
                    for minor in minors:
                        for user in USER_TYPES:
                            if user == "도민":
                                base_cnt = 900 / tw
                                factor = 1.0
                            elif user == "내국인관광객":
                                base_cnt = 700 * tw
                                factor = season
                            else:
                                base_cnt = 120 * tw
                                factor = season * (1.3 if ym.month in (4, 10) else 1.0)
                            if major == "숙박" and user == "도민":
                                base_cnt *= 0.05
                            for wd in WEEKDAYS:
                                weekend = 1.35 if wd in ("토", "일") and user != "도민" else 1.0
                                cnt = rng.poisson(max(base_cnt * factor * weekend / 7, 1))
                                avg = BASE_AMOUNT[minor] * rng.normal(1.0, 0.08)
                                if user == "외국인관광객":
                                    avg *= 1.15
                                rows.append({
                                    "기준년월": str(ym),
                                    "시군구": sigungu,
                                    "읍면동": dong,
                                    "업종대분류": major,
                                    "업종": minor,
                                    "이용자구분": user,
                                    "요일": wd,
                                    "이용건수": int(cnt),
                                    "이용금액": int(round(cnt * avg, -2)),
                                })
    return pd.DataFrame(rows)


def make_visitors(rng: np.random.Generator) -> pd.DataFrame:
    days = pd.date_range("2025-01-01", "2025-12-31", freq="D")
    rows = []
    for d in days:
        season = SEASON[d.month]
        weekend = 1.25 if d.day_of_week >= 4 else 1.0
        domestic = rng.normal(36000, 2500) * season * weekend
        foreign = rng.normal(5200, 600) * season * (1.3 if d.month in (4, 10) else 1.0)
        rows.append({
            "일자": d.strftime("%Y-%m-%d"),
            "내국인입도객": int(domestic),
            "외국인입도객": int(foreign),
            "강수량_mm": round(float(max(rng.gamma(0.6, 6.0) - 2, 0)), 1),
            "평균기온_C": round(float(15 + 11 * np.sin((d.day_of_year - 110) / 365 * 2 * np.pi)
                                   + rng.normal(0, 1.5)), 1),
        })
    return pd.DataFrame(rows)


def main() -> None:
    rng = np.random.default_rng(SEED)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    card = make_card_spending(rng)
    visitors = make_visitors(rng)
    # 공공데이터 파일과 같은 조건에서 동작하는지 확인하도록 카드 데이터는 CP949로 저장
    card.to_csv(OUT_DIR / "샘플_제주_읍면동_업종별_카드소비.csv", index=False, encoding="cp949")
    visitors.to_csv(OUT_DIR / "샘플_제주_일별_입도객_기상.csv", index=False, encoding="utf-8-sig")
    print(f"카드소비 {len(card):,}행, 입도객 {len(visitors):,}행 -> {OUT_DIR}")


if __name__ == "__main__":
    main()
