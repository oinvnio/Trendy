#!/usr/bin/env python3
"""한국관광공사 TourAPI에서 부산 관광·문화 POI를 받아 CSV로 저장한다.

상가(상권)정보에는 공연장·전시장·해수욕장·공원·사찰이 없어 문화·행사가 0건,
뷰·자연이 79건에 그친다(docs/mvp-busan.md 11.2절). 그 두 카테고리를 세우는 것이
이 스크립트의 목적이다.

    python3 scripts/fetch_tourapi.py --key "발급받은키" -o tourapi-busan.csv
    python3 scripts/fetch_tourapi.py --key "..." --probe        연결·사양 확인만

키는 공공데이터포털 "한국관광공사_국문 관광정보 서비스"에서 활용신청하면 받는다.
--key 대신 환경변수 TOURAPI_KEY 로 넘겨도 된다.

출력 CSV는 상가정보와 같은 컬럼 이름을 쓰므로 그대로 이어붙일 수 있다.

    python3 scripts/ingest_stores.py add tourapi-busan.csv
    python3 scripts/ingest_stores.py build

표준 라이브러리만 쓴다.
"""
import argparse
import csv
import json
import os
import sys
import time
import urllib.parse
import urllib.request

AREA_BUSAN = 6

# 받아올 콘텐츠 타입. 숙박·쇼핑·음식점(32/38/39)은 상가정보가 이미 덮으므로 뺀다.
CONTENT_TYPES = {
    12: "관광지",
    14: "문화시설",
    15: "축제공연행사",
    28: "레포츠",
}

# 서비스 경로는 개편을 거쳐 왔다. 앞에서부터 시도하고 되는 것을 쓴다.
ENDPOINTS = [
    ("KorService2", "areaBasedList2"),
    ("KorService1", "areaBasedList1"),
    ("KorService", "areaBasedList"),
]
BASE = "https://apis.data.go.kr/B551011/{svc}/{op}"


def call(key, svc, op, **params):
    q = {
        "serviceKey": key, "MobileOS": "ETC", "MobileApp": "trendy",
        "_type": "json", "numOfRows": 100, "pageNo": 1, "arrange": "A",
        **params,
    }
    url = BASE.format(svc=svc, op=op) + "?" + urllib.parse.urlencode(q, safe="%")
    with urllib.request.urlopen(url, timeout=30) as r:
        raw = r.read().decode("utf-8", errors="replace")
    if not raw.lstrip().startswith("{"):
        raise RuntimeError(f"JSON이 아닌 응답: {raw[:200]}")
    body = json.loads(raw)
    head = body.get("response", {}).get("header", {})
    code = str(head.get("resultCode", ""))
    if code not in ("0000", "00", "0"):
        raise RuntimeError(f"API 오류 {code}: {head.get('resultMsg')}")
    return body["response"]["body"]


def items_of(body):
    items = (body.get("items") or {}).get("item") or []
    return items if isinstance(items, list) else [items]


def probe(key):
    for svc, op in ENDPOINTS:
        try:
            body = call(key, svc, op, areaCode=AREA_BUSAN, contentTypeId=12, numOfRows=1)
            print(f"✅ {svc}/{op} 정상 — 부산 관광지 총 {body.get('totalCount', 0):,}건")
            return svc, op
        except Exception as e:  # noqa: BLE001 — 어떤 실패든 다음 후보로 넘어간다
            print(f"❌ {svc}/{op}: {e}")
    sys.exit("사용 가능한 엔드포인트가 없습니다. 키 승인 상태를 확인하세요"
             " (활용신청 직후에는 1시간 정도 걸릴 수 있습니다).")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--key", default=os.environ.get("TOURAPI_KEY"))
    ap.add_argument("-o", "--out", default="tourapi-busan.csv")
    ap.add_argument("--probe", action="store_true")
    args = ap.parse_args()
    if not args.key:
        sys.exit("--key 또는 환경변수 TOURAPI_KEY 가 필요합니다.")

    svc, op = probe(args.key)
    if args.probe:
        return

    rows, seen = [], set()
    for ctid, label in CONTENT_TYPES.items():
        page, total = 1, None
        while True:
            for attempt in range(3):
                try:
                    body = call(args.key, svc, op, areaCode=AREA_BUSAN,
                                contentTypeId=ctid, pageNo=page)
                    break
                except Exception as e:  # noqa: BLE001
                    if attempt == 2:
                        sys.exit(f"{label} {page}쪽에서 실패: {e}")
                    time.sleep(2 * (attempt + 1))
            if total is None:
                total = int(body.get("totalCount") or 0)
                print(f"{label}: {total:,}건")
            got = items_of(body)
            if not got:
                break
            for it in got:
                cid = str(it.get("contentid") or "")
                if cid in seen:
                    continue
                x, y = it.get("mapx"), it.get("mapy")
                if not x or not y or float(x) == 0:
                    continue
                seen.add(cid)
                rows.append({
                    "상가업소번호": cid,
                    "상호명": (it.get("title") or "").strip(),
                    "지점명": "",
                    "상권업종대분류명": "관광",          # TourAPI 출처 표시
                    "상권업종중분류명": it.get("cat1") or "",   # A01 자연 / A02 인문 / A03 레포츠
                    "상권업종소분류명": label,
                    "시도명": "부산광역시",
                    "행정동명": "",
                    "경도": x, "위도": y,
                })
            if page * 100 >= total:
                break
            page += 1
            time.sleep(0.2)

    cols = ["상가업소번호", "상호명", "지점명", "상권업종대분류명", "상권업종중분류명",
            "상권업종소분류명", "시도명", "행정동명", "경도", "위도"]
    with open(args.out, "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)
    print(f"\n총 {len(rows):,}건 저장 → {args.out}")
    print("다음: python3 scripts/ingest_stores.py add " + args.out)


if __name__ == "__main__":
    main()
