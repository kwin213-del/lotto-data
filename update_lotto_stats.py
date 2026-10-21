
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

# 당첨번호 파일에서 최신 회차 자동 확인
LOTTO_FILE = Path(__file__).with_name("lotto.json")


def get_latest_round():
    if not LOTTO_FILE.exists():
        raise FileNotFoundError(
            f"당첨번호 파일을 찾을 수 없습니다: {LOTTO_FILE}"
        )

    data = json.loads(LOTTO_FILE.read_text(encoding="utf-8"))

    if not isinstance(data, list) or not data:
        raise ValueError("lotto.json 데이터가 비어 있거나 형식이 잘못되었습니다.")

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
        raise ValueError("lotto.json에서 유효한 회차를 찾지 못했습니다.")

    return max(rounds)

# 현재 API 응답에서 확인된 수집 범위
FIRST_AVAILABLE_ROUND = 5

# 요청 사이 대기 시간(초)
REQUEST_DELAY = 1.5

# 요청 실패 시 재시도 횟수
MAX_RETRIES = 5

# 요청당 최대 대기 시간(초)
REQUEST_TIMEOUT = 90

# ==========================================
# API 데이터 수집
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

    last_error = None

    for attempt in range(1, MAX_RETRIES + 1):
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
                content_type = response.headers.get(
                    "Content-Type",
                    "",
                )

            if (
                "json" not in content_type.lower()
                and not body.lstrip().startswith("{")
            ):
                raise ValueError(
                    "JSON이 아닌 응답을 받았습니다."
                )

            result = json.loads(body)

            if not isinstance(result, dict):
                raise ValueError("API 응답 형식이 올바르지 않습니다.")

            if result.get("resultCode") not in (
                None,
                "0000",
                "200",
            ):
                raise ValueError(
                    f"API 응답 오류: "
                    f"{result.get('resultMessage')}"
                )

            rows = (result.get("data") or {}).get("list")

            if not isinstance(rows, list) or not rows:
                raise ValueError(
                    "응답에 회차 데이터가 없습니다."
                )

            print(
                f"  {center_round}회차 기준 요청 성공 "
                f"(응답 {len(rows)}건)"
            )

            return rows

        except Exception as error:
            last_error = error

            if attempt < MAX_RETRIES:
                # 5초 → 10초 → 20초 → 30초 대기
                wait_seconds = min(
                    5 * (2 ** (attempt - 1)),
                    30,
                )

                print(
                    f"재시도 {attempt}/{MAX_RETRIES - 1}: "
                    f"{center_round}회차 기준 요청 실패"
                )
                print(f"  원인: {error}")
                print(f"  {wait_seconds}초 후 다시 요청합니다.")

                time.sleep(wait_seconds)

    raise RuntimeError(
        f"{center_round}회차 기준 요청 실패: {last_error}"
    )



# ==========================================
# 데이터 검증
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
    ):
        raise ValueError(
            f"잘못된 회차 번호: {round_no!r}"
        )

    # 당첨번호 검증
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
        row,
        "rnk1WnNope",
        round_no,
    )

    # 기본적으로 구매 방식은 미확인으로 처리합니다.
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
            method_status = "unverified"
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
# 전체 회차 수집
# ==========================================

def main():
    collected = {}
    latest_round = get_latest_round()

    print(f"lotto.json 기준 최신 회차: {latest_round}")

    centers = list(
        range(
            10,
            latest_round + 1,
            10,
        )
    )

    if not centers or centers[-1] != latest_round:
        centers.append(latest_round)

    for index, center in enumerate(centers, start=1):
        print(
            f"[{index}/{len(centers)}] "
            f"{center}회차 기준 데이터 요청 중..."
        )

        rows = fetch_round_data(center)

        for row in rows:
            item = parse_round(row)
            round_no = item["round"]

            if (
                FIRST_AVAILABLE_ROUND
                <= round_no
                <= latest_round
            ):
                collected[round_no] = item

        if index < len(centers):
            time.sleep(REQUEST_DELAY)

    # 당첨번호 데이터 누락 여부 검사
    expected = set(
        range(
            FIRST_AVAILABLE_ROUND,
            latest_round + 1,
        )
    )

    actual = set(collected.keys())
    missing = sorted(expected - actual)

    if missing:
        raise RuntimeError(
            f"당첨번호 데이터 누락 {len(missing)}개: "
            f"{missing[:30]}. "
            "기존 method-stats.json은 유지합니다."
        )

    rounds = [
        collected[number]
        for number in range(
            FIRST_AVAILABLE_ROUND,
            latest_round + 1,
        )
    ]

    # 구매 방식 데이터가 검증된 회차만 집계
    verified_rounds = [
        item
        for item in rounds
        if item["methodDataStatus"] == "verified"
    ]

    no_winner_rounds = [
        item
        for item in rounds
        if item["methodDataStatus"] == "no_first_prize_winner"
    ]

    unverified_rounds = [
        item
        for item in rounds
        if item["methodDataStatus"] == "unverified"
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

    # 최종 집계도 다시 검증
    method_total = (
        totals["automatic"]
        + totals["manual"]
        + totals["semiAutomatic"]
    )

    if method_total != totals["firstPrizeGames"]:
        raise RuntimeError(
            "전체 구매 방식 합계 검증 실패. "
            "기존 파일은 유지합니다."
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
            "verifiedTotals": totals,
            "coverageNote": (
                "구매 방식 통계는 검증 완료 회차만 집계합니다. "
                "미확인 회차의 구매 방식 값은 null입니다."
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
    print(f"전체 수집 회차: {len(rounds)}")
    print(f"첫 회차: {rounds[0]['round']}")
    print(f"최신 회차: {rounds[-1]['round']}")
    print(f"구매 방식 검증 완료: {len(verified_rounds)}")
    print(f"1등 당첨 게임 없음: {len(no_winner_rounds)}")
    print(f"구매 방식 미확인: {len(unverified_rounds)}")

    print("\n검증 완료 회차의 구매 방식 합계:")
    print(
        json.dumps(
            totals,
            ensure_ascii=False,
            indent=2,
        )
    )

    print(f"\n저장 완료: {OUTPUT_FILE}")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print("\n수집 실패")
        print(f"{type(error).__name__}: {error}")
        print("검증 실패 시 기존 method-stats.json은 유지됩니다.")
