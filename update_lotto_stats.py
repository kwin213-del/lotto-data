
import json
import time
import urllib.request
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path

# ==========================================
# 설정
# ==========================================

API_URL = (
    "https://www.dhlottery.co.kr/"
    "lt645/selectPstLt645InfoNew.do"
)

OUTPUT_FILE = Path(__file__).with_name("method-stats.json")
LOTTO_FILE = Path(__file__).with_name("lotto.json")

FIRST_AVAILABLE_ROUND = 5

# 요청 사이 대기 시간
REQUEST_DELAY = 0.2

# 요청당 최대 대기 시간
REQUEST_TIMEOUT = 8

# ==========================================
# 최신 회차 확인
# ==========================================

def get_latest_round():
    if not LOTTO_FILE.exists():
        raise FileNotFoundError(
            f"당첨번호 파일을 찾을 수 없습니다: {LOTTO_FILE}"
        )

    data = json.loads(
        LOTTO_FILE.read_text(encoding="utf-8")
    )

    if not isinstance(data, list) or not data:
        raise ValueError("lotto.json 데이터가 비어 있습니다.")

    rounds = [
        item.get("round", item.get("draw_no"))
        for item in data
        if isinstance(item, dict)
    ]

    rounds = [
        number for number in rounds
        if isinstance(number, int)
        and not isinstance(number, bool)
        and number > 0
    ]

    if not rounds:
        raise ValueError("유효한 회차를 찾지 못했습니다.")

    return max(rounds)


# ==========================================
# API 요청
# 실패하면 None 반환 후 다음 요청 진행
# ==========================================

def fetch_round_data(center_round):
    params = urllib.parse.urlencode({
        "srchDir": "center",
        "srchLtEpsd": center_round,
    })

    url = f"{API_URL}?{params}"

    headers = {
        "User-Agent": "Mozilla/5.0",
        "Accept": "application/json, text/plain, */*",
        "Referer": "https://www.dhlottery.co.kr/lt645/result",
        "Connection": "close",
    }

    try:
        request = urllib.request.Request(
            url,
            headers=headers,
        )

        with urllib.request.urlopen(
            request,
            timeout=REQUEST_TIMEOUT,
        ) as response:
            body = response.read().decode("utf-8")

        result = json.loads(body)

        if not isinstance(result, dict):
            raise ValueError("API 응답 형식 오류")

        if result.get("resultCode") not in (
            None, "0000", "200"
        ):
            raise ValueError(
                f"API 오류: {result.get('resultMessage')}"
            )

        rows = (result.get("data") or {}).get("list")

        if not isinstance(rows, list) or not rows:
            raise ValueError("응답 데이터가 없습니다.")

        print(
            f"  성공: {center_round}회차 기준 "
            f"({len(rows)}건)"
        )

        return rows

    except Exception as error:
        print(
            f"  미확인: {center_round}회차 기준 요청 실패"
        )
        print(f"  원인: {error}")
        print("  재시도 없이 다음 요청으로 이동합니다.")
        return None


# ==========================================
# 회차 데이터 검증
# ==========================================

def require_nonnegative_int(row, field, round_no):
    value = row.get(field)

    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or value < 0
    ):
        raise ValueError(
            f"{round_no}회차 {field} 값 오류: {value!r}"
        )

    return value


def parse_round(row):
    round_no = row.get("ltEpsd")

    if (
        not isinstance(round_no, int)
        or isinstance(round_no, bool)
        or round_no <= 0
    ):
        raise ValueError(
            f"잘못된 회차 번호: {round_no!r}"
        )

    numbers = [
        row.get(f"tm{i}WnNo")
        for i in range(1, 7)
    ]

    bonus = row.get("bnsWnNo")

    valid_numbers = (
        all(
            isinstance(number, int)
            and not isinstance(number, bool)
            and 1 <= number <= 45
            for number in numbers
        )
        and len(set(numbers)) == 6
        and isinstance(bonus, int)
        and not isinstance(bonus, bool)
        and 1 <= bonus <= 45
        and bonus not in numbers
    )

    if not valid_numbers:
        raise ValueError(
            f"{round_no}회차 당첨번호 검증 실패"
        )

    first_prize_games = require_nonnegative_int(
        row, "rnk1WnNope", round_no
    )

    automatic = None
    manual = None
    semi_automatic = None

    method_status = "unverified"
    method_note = "구매 방식 데이터 미확인"

    raw_modes = [
        row.get("winType1"),
        row.get("winType2"),
        row.get("winType3"),
    ]

    fields_valid = all(
        isinstance(value, int)
        and not isinstance(value, bool)
        and value >= 0
        for value in raw_modes
    )

    if fields_valid:
        a, m, s = raw_modes
        method_sum = a + m + s

        if first_prize_games == 0 and method_sum == 0:
            method_status = "no_first_prize_winner"
            method_note = "해당 회차의 1등 당첨 게임 수가 0입니다."

            automatic = 0
            manual = 0
            semi_automatic = 0

        elif method_sum == first_prize_games:
            method_status = "verified"
            method_note = "구매 방식 합계 검증 완료"

            automatic = a
            manual = m
            semi_automatic = s

        else:
            method_note = (
                "구매 방식 합계와 1등 당첨 게임 수가 불일치합니다."
            )

    return {
        "round": round_no,
        "date": row.get("ltRflYmd"),
        "numbers": numbers,
        "bonus": bonus,
        "firstPrizeGames": first_prize_games,
        "purchaseMethod": {
            "automatic": automatic,
            "manual": manual,
            "semiAutomatic": semi_automatic,
        },
        "methodDataStatus": method_status,
        "methodDataNote": method_note,
        "winType0": row.get("winType0"),
    }


# ==========================================
# 미확인 회차 생성
# ==========================================

def make_unavailable_round(round_no):
    return {
        "round": round_no,
        "date": None,
        "numbers": None,
        "bonus": None,
        "firstPrizeGames": None,
        "purchaseMethod": {
            "automatic": None,
            "manual": None,
            "semiAutomatic": None,
        },
        "methodDataStatus": "unavailable",
        "methodDataNote": (
            "API 응답 또는 회차 데이터 확인 실패"
        ),
        "winType0": None,
    }


# ==========================================
# 전체 회차 수집
# ==========================================

def main():
    collected = {}
    latest_round = get_latest_round()

    print(f"lotto.json 기준 최신 회차: {latest_round}")

    centers = list(range(10, latest_round + 1, 10))

    if not centers or centers[-1] != latest_round:
        centers.append(latest_round)

    total_requests = len(centers)

    for index, center in enumerate(centers, start=1):
        print(
            f"\n[{index}/{total_requests}] "
            f"{center}회차 기준 요청 중..."
        )

        rows = fetch_round_data(center)

        if rows is not None:
            for row in rows:
                try:
                    item = parse_round(row)
                    round_no = item["round"]

                    if (
                        FIRST_AVAILABLE_ROUND
                        <= round_no
                        <= latest_round
                    ):
                        collected[round_no] = item

                except Exception as error:
                    round_no = (
                        row.get("ltEpsd")
                        if isinstance(row, dict)
                        else None
                    )

                    print(f"  개별 데이터 검증 실패: {error}")

                    if (
                        isinstance(round_no, int)
                        and not isinstance(round_no, bool)
                        and FIRST_AVAILABLE_ROUND
                        <= round_no
                        <= latest_round
                        and round_no not in collected
                    ):
                        collected[round_no] = (
                            make_unavailable_round(round_no)
                        )

        if index < total_requests:
            time.sleep(REQUEST_DELAY)

    # 누락 회차는 미확인으로 채우고 계속 진행
    expected_rounds = range(
        FIRST_AVAILABLE_ROUND,
        latest_round + 1,
    )

    for round_no in expected_rounds:
        if round_no not in collected:
            collected[round_no] = make_unavailable_round(
                round_no
            )

    rounds = [
        collected[round_no]
        for round_no in expected_rounds
    ]

    verified_rounds = [
        item for item in rounds
        if item["methodDataStatus"] == "verified"
    ]

    no_winner_rounds = [
        item for item in rounds
        if item["methodDataStatus"] == "no_first_prize_winner"
    ]

    unverified_rounds = [
        item for item in rounds
        if item["methodDataStatus"] == "unverified"
    ]

    unavailable_rounds = [
        item for item in rounds
        if item["methodDataStatus"] == "unavailable"
    ]

    totals = {
        "automatic": sum(
            item["purchaseMethod"]["automatic"]
            for item in verified_rounds
        ),
        "manual": sum(
            item["purchaseMethod"]["manual"]
            for item in verified_rounds
        ),
        "semiAutomatic": sum(
            item["purchaseMethod"]["semiAutomatic"]
            for item in verified_rounds
        ),
        "firstPrizeGames": sum(
            item["firstPrizeGames"]
            for item in verified_rounds
        ),
    }

    method_total = (
        totals["automatic"]
        + totals["manual"]
        + totals["semiAutomatic"]
    )

    if method_total != totals["firstPrizeGames"]:
        raise RuntimeError(
            "전체 구매 방식 합계 검증 실패"
        )

    output = {
        "source": "Donghaeng Lottery",
        "api": API_URL,
        "generatedAt": datetime.now(
            timezone.utc
        ).isoformat(),
        "latestRound": latest_round,
        "firstAvailableRound": FIRST_AVAILABLE_ROUND,
        "roundCount": len(rounds),
        "methodStatistics": {
            "verifiedRoundCount": len(verified_rounds),
            "noFirstPrizeWinnerRoundCount": len(no_winner_rounds),
            "unverifiedRoundCount": len(unverified_rounds),
            "unavailableRoundCount": len(unavailable_rounds),
            "unavailableRounds": [
                item["round"] for item in unavailable_rounds
            ],
            "verifiedTotals": totals,
            "coverageNote": (
                "구매 방식 합계는 검증 완료 회차만 집계합니다. "
                "API 응답 실패 또는 데이터 누락 회차는 "
                "null로 기록하고 집계에서 제외합니다."
            ),
        },
        "rounds": rounds,
    }

    # 임시 파일 저장 후 최종 파일로 교체
    temp_file = OUTPUT_FILE.with_suffix(".tmp")

    temp_file.write_text(
        json.dumps(
            output,
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    temp_file.replace(OUTPUT_FILE)

    print("\n========== 수집 결과 ==========")
    print(f"전체 회차 범위: {len(rounds)}")
    print(f"첫 회차: {rounds[0]['round']}")
    print(f"최신 회차: {rounds[-1]['round']}")
    print(f"구매 방식 검증 완료: {len(verified_rounds)}")
    print(f"1등 당첨 게임 없음: {len(no_winner_rounds)}")
    print(f"구매 방식 미확인: {len(unverified_rounds)}")
    print(f"응답 실패·데이터 누락: {len(unavailable_rounds)}")

    if unavailable_rounds:
        print(
            "미확인 회차 목록:",
            [item["round"] for item in unavailable_rounds],
        )

    print("\n검증 완료 회차의 구매 방식 합계:")
    print(json.dumps(totals, ensure_ascii=False, indent=2))
    print(f"\n저장 완료: {OUTPUT_FILE}")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print("\n수집 실패")
        print(f"{type(error).__name__}: {error}")
        print("최종 파일 저장 전 오류가 발생했습니다.")
