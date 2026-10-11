
import json
import os
import urllib.request
from pathlib import Path

API = (
    "https://www.dhlottery.co.kr/"
    "lt645/selectPstLt645InfoNew.do"
    "?srchDir=center&srchLtEpsd={}"
)

def fetch_round(round_no):
    url = API.format(round_no)
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "Mozilla/5.0"}
    )

    with urllib.request.urlopen(req, timeout=20) as response:
        data = json.loads(response.read().decode("utf-8"))

    # API 응답 형식이 예상과 다르면 저장하지 않습니다.
    if isinstance(data, dict):
        rows = data.get("data") or data.get("list") or []
    elif isinstance(data, list):
        rows = data
    else:
        rows = []

    for row in rows:
        if not isinstance(row, dict):
            continue

        nums = row.get("winning_numbers")
        if not nums:
            nums = [
                row.get("tm1WnNo"), row.get("tm2WnNo"),
                row.get("tm3WnNo"), row.get("tm4WnNo"),
                row.get("tm5WnNo"), row.get("tm6WnNo")
            ]

        try:
            nums = [int(n) for n in nums]
            actual_round = int(
                row.get("draw_no") or row.get("ltEpsd")
                or row.get("drwNo") or row.get("round")
            )
        except (TypeError, ValueError):
            continue

        if (
            actual_round == round_no
            and len(nums) == 6
            and len(set(nums)) == 6
            and all(1 <= n <= 45 for n in nums)
        ):
            bonus = row.get("bonus_number") or row.get("bnusNo")
            return {
                "round": actual_round,
                "numbers": sorted(nums),
                "bonus": int(bonus) if bonus else None
            }

    return None

def main():
    path = Path("lotto.json")
    existing = []

    if path.exists():
        existing = json.loads(path.read_text(encoding="utf-8"))

    by_round = {
        item["round"]: item
        for item in existing
        if isinstance(item, dict) and "round" in item
    }

    # 시작 회차를 지정하고 이후에는 최신 회차를 확인합니다.
    # 공식 API 응답 구조를 먼저 검증해야 합니다.
    start_round = int(os.getenv("START_ROUND", "1"))
    latest_known = max(by_round.keys(), default=start_round - 1)

    # 최신 회차를 무작정 추정하지 않고, 저장된 마지막 회차부터
    # 최대 10회차까지만 확인합니다. 첫 실행은 별도 초기화가 필요합니다.
    first = max(start_round, latest_known + 1)
    last = first + 9

    for r in range(first, last + 1):
        try:
            item = fetch_round(r)
            if item:
                by_round[r] = item
            elif r > latest_known:
                # 아직 추첨되지 않은 회차 또는 응답 형식 불일치
                break
        except Exception as exc:
            print(f"회차 {r} 조회 실패: {exc}")
            break

    result = [by_round[r] for r in sorted(by_round)]
    if not result:
        raise RuntimeError("유효한 당첨번호 데이터가 없습니다.")

    path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )
    print(f"저장 완료: {len(result)}개 회차")

if __name__ == "__main__":
    main()
