
import json
import time
import urllib.request
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path

API_URL = (
    "https://www.dhlottery.co.kr/"
    "lt645/selectPstLt645InfoNew.do"
)

OUTPUT_FILE = Path(__file__).with_name("method-stats.json")
LOTTO_FILE = Path(__file__).with_name("lotto.json")

FIRST_AVAILABLE_ROUND = 5
REQUEST_DELAY = 0.1
REQUEST_TIMEOUT = 5
REQUEST_RETRIES = 2
RETRY_BASE_DELAY = 1


def get_latest_round():
    if not LOTTO_FILE.exists():
        raise FileNotFoundError(f"파일 없음: {LOTTO_FILE}")

    data = json.loads(LOTTO_FILE.read_text(encoding="utf-8"))

    if not isinstance(data, list) or not data:
        raise ValueError("lotto.json 데이터가 비어 있습니다.")

    rounds = [
        item.get("round", item.get("draw_no"))
        for item in data
        if isinstance(item, dict)
    ]
    rounds = [
        r for r in rounds
        if isinstance(r, int) and not isinstance(r, bool) and r > 0
    ]

    if not rounds:
        raise ValueError("유효한 회차를 찾지 못했습니다.")

    return max(rounds)


def load_existing_rounds():
    if not OUTPUT_FILE.exists():
        print("기존 통계 파일이 없습니다. 전체 수집 모드로 진행합니다.")
        return {}

    try:
        data = json.loads(OUTPUT_FILE.read_text(encoding="utf-8"))
        result = {}

        for item in data.get("rounds", []):
            if (
                isinstance(item, dict)
                and isinstance(item.get("round"), int)
                and not isinstance(item.get("round"), bool)
            ):
                result[item["round"]] = item

        print(f"기존 통계 {len(result)}개 회차를 읽었습니다.")
        return result

    except Exception as error:
        # 손상된 기존 파일을 빈 데이터로 간주해 덮어쓰지 않습니다.
        raise RuntimeError(
            f"기존 통계 파일을 읽을 수 없습니다. "
            f"파일을 보존하고 원인을 확인하세요: {error}"
        ) from error


def record_quality(item):
    if not isinstance(item, dict):
        return 0

    status = item.get("methodDataStatus")

    if status in ("verified", "no_first_prize_winner"):
        return 3
    if status == "unverified":
        return 2
    if status == "unavailable":
        return 1

    return 0


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
        "methodDataNote": "API 응답 또는 회차 데이터 확인 실패",
        "winType0": None,
    }


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

    for attempt in range(1, REQUEST_RETRIES + 1):
        try:
            request = urllib.request.Request(url, headers=headers)

            with urllib.request.urlopen(
                request, timeout=REQUEST_TIMEOUT
            ) as response:
                body = response.read().decode("utf-8")

            result = json.loads(body)

            if not isinstance(result, dict):
                raise ValueError("API 응답 형식 오류")

            if result.get("resultCode") not in (None, "0000", "200"):
                raise ValueError(
                    f"API 오류: {result.get('resultMessage')}"
                )

            rows = (result.get("data") or {}).get("list")

            if not isinstance(rows, list) or not rows:
                raise ValueError("응답 데이터가 없습니다.")

                        returned_rounds = sorted({
                row.get("ltEpsd")
                for row in rows
                if isinstance(row, dict)
                and isinstance(row.get("ltEpsd"), int)
            })

            print(
                f"  성공: {center_round}회차 기준, "
                f"{len(rows)}건, {attempt}번째 시도"
            )
            print(
                f"  실제 반환 회차: "
                f"{returned_rounds if returned_rounds else '회차 확인 불가'}"
            )

            return rows

        except Exception as error:
            print(
                f"  요청 실패: {center_round}회차 기준 "
                f"({attempt}/{REQUEST_RETRIES}): {error}"
            )

            if attempt < REQUEST_RETRIES:
                wait = RETRY_BASE_DELAY * attempt
                print(f"  {wait}초 후 재시도")
                time.sleep(wait)

    print(f"  실패: {center_round}회차 기준")
    return None


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
        raise ValueError(f"잘못된 회차 번호: {round_no!r}")

    numbers = [row.get(f"tm{i}WnNo") for i in range(1, 7)]
    bonus = row.get("bnsWnNo")

    valid_numbers = (
        all(
            isinstance(n, int)
            and not isinstance(n, bool)
            and 1 <= n <= 45
            for n in numbers
        )
        and len(set(numbers)) == 6
        and isinstance(bonus, int)
        and not isinstance(bonus, bool)
        and 1 <= bonus <= 45
        and bonus not in numbers
    )

    if not valid_numbers:
        raise ValueError(f"{round_no}회차 당첨번호 검증 실패")

    first_prize_games = require_nonnegative_int(
        row, "rnk1WnNope", round_no
    )

    modes = [row.get("winType1"), row.get("winType2"), row.get("winType3")]

    modes_valid = all(
        isinstance(value, int)
        and not isinstance(value, bool)
        and value >= 0
        for value in modes
    )

    automatic = manual = semi_automatic = None
    status = "unverified"
    note = "구매 방식 데이터 미확인"

    if modes_valid:
        a, m, s = modes
        mode_sum = a + m + s

        if first_prize_games == 0 and mode_sum == 0:
            status = "no_first_prize_winner"
            note = "해당 회차의 1등 당첨 게임 수가 0입니다."
            automatic, manual, semi_automatic = 0, 0, 0

        elif mode_sum == first_prize_games:
            status = "verified"
            note = "구매 방식 합계 검증 완료"
            automatic, manual, semi_automatic = a, m, s

        else:
            note = "구매 방식 합계와 1등 당첨 게임 수가 불일치합니다."

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
        "methodDataStatus": status,
        "methodDataNote": note,
        "winType0": row.get("winType0"),
    }


def build_output(round_map, latest_round):
    expected = range(FIRST_AVAILABLE_ROUND, latest_round + 1)

    rounds = [
        round_map.get(r, make_unavailable_round(r))
        for r in expected
    ]

    verified = [
        item for item in rounds
        if item.get("methodDataStatus") == "verified"
    ]

    no_winner = [
        item for item in rounds
        if item.get("methodDataStatus") == "no_first_prize_winner"
    ]

    unverified = [
        item for item in rounds
        if item.get("methodDataStatus") == "unverified"
    ]

    unavailable = [
        item for item in rounds
        if item.get("methodDataStatus") == "unavailable"
    ]

    totals = {
        "automatic": sum(
            (item.get("purchaseMethod") or {}).get("automatic") or 0
            for item in verified
        ),
        "manual": sum(
            (item.get("purchaseMethod") or {}).get("manual") or 0
            for item in verified
        ),
        "semiAutomatic": sum(
            (item.get("purchaseMethod") or {}).get("semiAutomatic") or 0
            for item in verified
        ),
        "firstPrizeGames": sum(
            item.get("firstPrizeGames") or 0 for item in verified
        ),
    }

    method_sum = (
        totals["automatic"]
        + totals["manual"]
        + totals["semiAutomatic"]
    )

    if method_sum != totals["firstPrizeGames"]:
        raise RuntimeError("전체 구매 방식 합계 검증 실패")

    return {
        "source": "Donghaeng Lottery",
        "api": API_URL,
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "latestRound": latest_round,
        "firstAvailableRound": FIRST_AVAILABLE_ROUND,
        "roundCount": len(rounds),
        "methodStatistics": {
            "verifiedRoundCount": len(verified),
            "noFirstPrizeWinnerRoundCount": len(no_winner),
            "unverifiedRoundCount": len(unverified),
            "unavailableRoundCount": len(unavailable),
            "unavailableRounds": [item["round"] for item in unavailable],
            "verifiedTotals": totals,
            "coverageNote": (
                "구매 방식 합계는 검증 완료 회차만 집계합니다. "
                "응답 실패 또는 데이터 누락 회차는 집계에서 제외합니다."
            ),
        },
        "rounds": rounds,
    }


def save_output(round_map, latest_round):
    output = build_output(round_map, latest_round)
    temp_file = OUTPUT_FILE.with_suffix(".tmp")

    temp_file.write_text(
        json.dumps(output, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temp_file.replace(OUTPUT_FILE)

    stats = output["methodStatistics"]
    print(
        "  중간 저장 완료: "
        f"검증 {stats['verifiedRoundCount']}, "
        f"미확인 {stats['unverifiedRoundCount']}, "
        f"응답 실패 {stats['unavailableRoundCount']}"
    )


def get_request_centers(round_map, latest_round, full_scan):
    if full_scan:
        centers = list(range(10, latest_round + 1, 10))
        if not centers or centers[-1] != latest_round:
            centers.append(latest_round)
        return sorted(set(centers))

    # unavailable 회차가 포함된 10회차 단위 구간만 재조회합니다.
    centers = set()

    for round_no in range(FIRST_AVAILABLE_ROUND, latest_round + 1):
        item = round_map.get(round_no)

        if not item or item.get("methodDataStatus") == "unavailable":
            center = ((round_no + 5) // 10) * 10

            if center > latest_round:
                center = latest_round

            centers.add(center)

    return sorted(centers)


def main():
    latest_round = get_latest_round()
    round_map = load_existing_rounds()

    print(f"lotto.json 최신 회차: {latest_round}")

    full_scan = not round_map

    # 누락된 회차를 기본값으로 준비하되, 기존 데이터를 덮어쓰지 않습니다.
    for round_no in range(FIRST_AVAILABLE_ROUND, latest_round + 1):
        if round_no not in round_map:
            round_map[round_no] = make_unavailable_round(round_no)

    centers = get_request_centers(round_map, latest_round, full_scan)

    if full_scan:
        print(f"전체 수집 모드: {len(centers)}개 요청")
    else:
        print(f"실패 구간 복구 모드: {len(centers)}개 요청")

    if not centers:
        print("재조회할 실패 구간이 없습니다.")
        save_output(round_map, latest_round)
        return

    for index, center in enumerate(centers, start=1):
        print(f"\n[{index}/{len(centers)}] {center}회차 기준 요청")

        rows = fetch_round_data(center)

        if rows is not None:
            for row in rows:
                try:
                    item = parse_round(row)
                    round_no = item["round"]

                    if not FIRST_AVAILABLE_ROUND <= round_no <= latest_round:
                        continue

                    old_item = round_map.get(round_no)

                    # 기존 데이터보다 품질이 좋은 데이터만 반영합니다.
                    if record_quality(item) > record_quality(old_item):
                        round_map[round_no] = item
                        print(
                            f"  회차 {round_no} 갱신: "
                            f"{item['methodDataStatus']}"
                        )

                except Exception as error:
                    print(f"  개별 데이터 검증 실패: {error}")

            # 성공한 구간마다 저장해 실행 취소 시에도 복구 결과를 보존합니다.
            save_output(round_map, latest_round)

        time.sleep(REQUEST_DELAY)

    # 마지막에도 전체 통계를 다시 계산하고 저장합니다.
    save_output(round_map, latest_round)

    output = build_output(round_map, latest_round)
    stats = output["methodStatistics"]

    print("\n========== 최종 결과 ==========")
    print(f"전체 회차: {output['roundCount']}")
    print(f"최신 회차: {latest_round}")
    print(f"구매 방식 검증 완료: {stats['verifiedRoundCount']}")
    print(f"1등 당첨 게임 없음: {stats['noFirstPrizeWinnerRoundCount']}")
    print(f"구매 방식 미확인: {stats['unverifiedRoundCount']}")
    print(f"응답 실패·데이터 누락: {stats['unavailableRoundCount']}")
    print("구매 방식 합계:")
    print(json.dumps(stats["verifiedTotals"], ensure_ascii=False, indent=2))
    print(f"저장 완료: {OUTPUT_FILE}")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(f"\n실행 실패: {type(error).__name__}: {error}")
        raise
