import json
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

DATA_URL = "https://smok95.github.io/lotto/results/all.json"
OUTPUT_FILE = Path("lotto.json")


def fetch_lotto_data():
    request = urllib.request.Request(
        DATA_URL,
        headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"},
    )

    with urllib.request.urlopen(request, timeout=30) as response:
        data = json.loads(response.read().decode("utf-8"))

    if not isinstance(data, list) or not data:
        raise RuntimeError("가져온 데이터가 비어 있거나 목록 형식이 아닙니다.")

    results = []

    for item in data:
        if not isinstance(item, dict):
            continue

        round_no = item.get("draw_no", item.get("round"))
        numbers = item.get(
            "numbers",
            item.get("winning_numbers", item.get("drwtNo")),
        )
        bonus = item.get(
            "bonus_no",
            item.get("bonus_number", item.get("bnusNo")),
        )
        draw_date = item.get("date", item.get("draw_date", ""))

        try:
            round_no = int(round_no)
            numbers = [int(n) for n in numbers]
            bonus = int(bonus)
        except (TypeError, ValueError):
            continue

        if (
            round_no < 1
            or len(numbers) != 6
            or len(set(numbers)) != 6
            or any(n < 1 or n > 45 for n in numbers)
            or bonus < 1
            or bonus > 45
            or bonus in numbers
        ):
            continue

        results.append({
            "round": round_no,
            "draw_no": round_no,
            "numbers": sorted(numbers),
            "winning_numbers": sorted(numbers),
            "bonus": bonus,
            "bonus_no": bonus,
            "date": draw_date,
        })

    # 회차 중복 제거 후 오름차순 정렬
    unique = {item["round"]: item for item in results}
    results = [unique[r] for r in sorted(unique)]

    if not results:
        raise RuntimeError(
            "유효한 당첨번호가 없습니다. 데이터 출처의 응답 형식을 확인하세요."
        )

    return results


def main():
    results = fetch_lotto_data()

    OUTPUT_FILE.write_text(
        json.dumps(results, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(f"저장 완료: {OUTPUT_FILE}")
    print(f"수집 회차 수: {len(results)}")
    print(f"첫 회차: {results[0]['round']}회")
    print(f"마지막 회차: {results[-1]['round']}회")
    print(f"갱신 시간(UTC): {datetime.now(timezone.utc).isoformat()}")


if __name__ == "__main__":
    main()
