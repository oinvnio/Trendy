#!/usr/bin/env python3
"""부산 도시철도 역 정보를 지명 사전(gazetteer.json)에 병합한다.

역명은 폴백 라벨의 2순위다(행정동명 → 역명 → 상위 구·군). 트렌드가 없는
구역에서 "물만골역"처럼 사람이 위치를 즉시 알아보는 라벨을 만들기 위해 쓴다.

### 입력 CSV 구하기

이 저장소에는 역 좌표 원본을 포함하지 않는다(재배포 조건 확인 필요).
공공데이터포털에서 역사 정보 데이터셋(예: "부산교통공사_도시철도 역사 정보")을
내려받아 CSV로 저장한 뒤 아래처럼 실행한다.

    python3 scripts/merge_stations.py stations.csv

### CSV 컬럼

컬럼명은 아래 후보 중 아무거나로 인식한다(한글/영문 혼용 대응).

    역명   : 역명, 역이름, 지하철역, station, name
    위도   : 위도, lat, latitude, ycoord, y
    경도   : 경도, lon, lng, longitude, xcoord, x
    노선   : 노선, 호선, line          (선택)

좌표가 부산 경계 밖이면 건너뛰고, 각 역이 속한 행정동을 공간 조인으로 붙인다.
"""
import csv
import json
import sys
from pathlib import Path

from shapely.geometry import shape, Point

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "data" / "busan"

ALIASES = {
    "name": ("역명", "역이름", "지하철역", "station", "station_name", "name"),
    "lat": ("위도", "lat", "latitude", "ycoord", "y"),
    "lon": ("경도", "lon", "lng", "longitude", "xcoord", "x"),
    "line": ("노선", "호선", "노선명", "line", "line_name"),
}


def pick(row, key):
    for cand in ALIASES[key]:
        for col, val in row.items():
            if col and col.strip().lower().replace(" ", "") == cand.lower():
                return val
    return None


def main(csv_path):
    dong = json.loads((OUT_DIR / "dong.geojson").read_text(encoding="utf-8"))
    polys = [(shape(f["geometry"]).buffer(0), f["properties"]) for f in dong["features"]]

    gaz_path = OUT_DIR / "gazetteer.json"
    gaz = json.loads(gaz_path.read_text(encoding="utf-8"))
    places = [p for p in gaz["places"] if p["type"] != "station"]  # 재실행 시 갱신

    added, skipped = 0, 0
    with open(csv_path, encoding="utf-8-sig", newline="") as fh:
        for row in csv.DictReader(fh):
            name, lat, lon = pick(row, "name"), pick(row, "lat"), pick(row, "lon")
            if not (name and lat and lon):
                skipped += 1
                continue
            try:
                pt = Point(float(lon), float(lat))
            except ValueError:
                skipped += 1
                continue
            parent = next((p["full_nm"] for g, p in polys if g.contains(pt)), None)
            if parent is None:  # 부산 밖이거나 좌표 오류
                skipped += 1
                continue
            name = name.strip()
            places.append({
                "type": "station",
                "name": name if name.endswith("역") else f"{name}역",
                "full_name": f"{parent} {name}",
                "code": None,
                "parent": parent,
                "line": (pick(row, "line") or "").strip() or None,
                "lon": round(pt.x, 6),
                "lat": round(pt.y, 6),
            })
            added += 1

    gaz["places"] = sorted(places, key=lambda e: (e["type"], e["name"]))
    gaz["counts"] = {t: sum(1 for e in places if e["type"] == t)
                     for t in ("sido", "sgg", "dong", "station")}
    gaz_path.write_text(json.dumps(gaz, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"역 {added}건 병합, {skipped}건 제외 → {gaz_path.relative_to(ROOT)}")
    print("건수:", gaz["counts"])


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    main(sys.argv[1])
