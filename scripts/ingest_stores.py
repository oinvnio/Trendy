#!/usr/bin/env python3
"""소상공인시장진흥공단 상가(상권)정보 CSV를 부산 가게 데이터로 적재한다.

    python3 scripts/ingest_stores.py <상가정보.csv> [--peek]

--peek 을 주면 적재하지 않고 컬럼 구성과 표본 몇 줄만 출력한다(스키마 확인용).

### 하는 일

1. 부산(시도명/시도코드 26) 행만 남긴다 — 전국 파일을 그대로 넣어도 된다
2. 상권업종 대/중분류를 트렌디 카테고리 5종으로 접는다
3. 좌표로 행정동을 공간 조인한다 (CSV의 행정동명은 교차 검증에만 쓴다)
4. 두 가지를 출력한다
   - data/busan/store-counts.json   행정동 × 카테고리 업소 수
   - data/busan/stores-sample.json  그룹당 최대 N곳 (드릴다운 목록용)

### 왜 전량을 싣지 않나

부산 상가업소는 수십만 건이라 그대로 지도에 얹을 수 없고, 얹을 이유도 없다.
상가정보는 **영업 중인 업소 목록이지 트렌드가 아니다.** 무엇이 뜨는지는 언급량과
burst가 정하고, 이 파일은 후보 풀과 좌표를 댈 뿐이다(docs/mvp-busan.md 4.0절).

다만 **업소 수 자체는 상권의 두께**를 보여주는 신호라, 트렌드 점수가 붙기 전까지
대표 라벨 크기의 임시 대용으로 쓸 수 있다.
"""
import csv
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

from shapely.geometry import shape, Point
from shapely.strtree import STRtree

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "data" / "busan"
PER_GROUP = 12  # 드릴다운 목록에 실을 그룹당 최대 업소 수

# 컬럼명은 배포 회차마다 조금씩 달라져 후보를 여럿 둔다
COLS = {
    "name": ("상호명", "사업장명", "상호"),
    "branch": ("지점명",),
    "big": ("상권업종대분류명", "상권업종대분류", "표준산업분류명"),
    "mid": ("상권업종중분류명", "상권업종중분류"),
    "small": ("상권업종소분류명", "상권업종소분류"),
    "sido": ("시도명", "시도"),
    "sidocd": ("시도코드",),
    "sgg": ("시군구명", "시군구"),
    "dong": ("행정동명", "행정동"),
    "lon": ("경도", "lon", "x", "좌표정보(x)", "위치좌표x"),
    "lat": ("위도", "lat", "y", "좌표정보(y)", "위치좌표y"),
}

# 대분류/중분류 → 카테고리. 위에서부터 먼저 맞는 규칙을 쓴다.
# 상가정보는 '상가'라서 자연·명소가 거의 없다 — view/culture는 TourAPI 몫이고
# 이 파일이 실제로 채우는 것은 food·cafe·life다.
RULES = [
    ("cafe", ("커피", "카페", "제과", "제빵", "디저트", "아이스크림", "빙수")),
    ("food", ("음식", "식당", "주점", "restaurant")),
    ("culture", ("문화", "박물관", "미술", "영화", "공연", "전시", "도서")),
    ("view", ("관광", "명소", "공원", "유원지", "자연")),
]
DEFAULT_CAT = "life"


def resolve(header):
    """실제 CSV 헤더에서 우리가 쓸 컬럼 이름을 찾아 매핑을 만든다."""
    norm = {h.strip().lower().replace(" ", ""): h for h in header if h}
    found = {}
    for key, cands in COLS.items():
        for c in cands:
            hit = norm.get(c.lower().replace(" ", ""))
            if hit:
                found[key] = hit
                break
    return found


def categorize(big, mid, small):
    blob = " ".join(x for x in (mid, small, big) if x)
    for cat, needles in RULES:
        if any(n in blob for n in needles):
            return cat
    return DEFAULT_CAT


def peek(path):
    with open(path, encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        print("컬럼:", ", ".join(reader.fieldnames or []))
        print("\n인식된 매핑:")
        for k, v in resolve(reader.fieldnames or []).items():
            print(f"  {k:8s} → {v}")
        print("\n표본:")
        for i, row in enumerate(reader):
            if i >= 3:
                break
            print("  " + json.dumps(row, ensure_ascii=False)[:400])


def main(path):
    dong_fc = json.loads((OUT_DIR / "dong.geojson").read_text(encoding="utf-8"))
    polys = [shape(f["geometry"]).buffer(0) for f in dong_fc["features"]]
    props = [f["properties"] for f in dong_fc["features"]]
    tree = STRtree(polys)

    with open(path, encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        cols = resolve(reader.fieldnames or [])
        for need in ("name", "lon", "lat"):
            if need not in cols:
                sys.exit(f"필수 컬럼을 찾지 못했습니다: {need}\n--peek 으로 헤더를 확인하세요.")

        counts = Counter()
        groups = defaultdict(list)
        total = kept = outside = mismatch = 0

        for row in reader:
            total += 1
            sido = (row.get(cols.get("sido", ""), "") or "")
            sidocd = (row.get(cols.get("sidocd", ""), "") or "")
            if sido and "부산" not in sido:
                continue
            if not sido and sidocd and not sidocd.startswith("26"):
                continue
            try:
                pt = Point(float(row[cols["lon"]]), float(row[cols["lat"]]))
            except (ValueError, TypeError, KeyError):
                continue

            hit = None
            for i in tree.query(pt):
                if polys[i].contains(pt):
                    hit = props[i]
                    break
            if hit is None:
                outside += 1
                continue

            csv_dong = (row.get(cols.get("dong", ""), "") or "").strip()
            if csv_dong and csv_dong != hit["dong_nm"]:
                mismatch += 1  # 경계 갱신 시차 — 공간 조인 결과를 신뢰한다

            cat = categorize(row.get(cols.get("big", ""), ""),
                             row.get(cols.get("mid", ""), ""),
                             row.get(cols.get("small", ""), ""))
            key = (hit["code"], cat)
            counts[key] += 1
            if len(groups[key]) < PER_GROUP:
                name = (row[cols["name"]] or "").strip()
                branch = (row.get(cols.get("branch", ""), "") or "").strip()
                groups[key].append({
                    "n": f"{name} {branch}".strip(),
                    "lon": round(pt.x, 5), "lat": round(pt.y, 5),
                })
            kept += 1

    counts_out = defaultdict(dict)
    for (code, cat), n in counts.items():
        counts_out[code][cat] = n
    (OUT_DIR / "store-counts.json").write_text(
        json.dumps({"note": "행정동 × 카테고리 업소 수. 상권의 두께 지표.",
                    "source": "소상공인시장진흥공단 상가(상권)정보",
                    "counts": counts_out}, ensure_ascii=False),
        encoding="utf-8")
    (OUT_DIR / "stores-sample.json").write_text(
        json.dumps({"note": f"드릴다운 목록용 표본. 행정동×카테고리 그룹당 최대 {PER_GROUP}곳. "
                            "트렌드 점수가 아니라 파일 등장 순서다.",
                    "source": "소상공인시장진흥공단 상가(상권)정보",
                    "per_group": PER_GROUP,
                    "groups": {f"{c}|{k}": v for (c, k), v in groups.items()}},
                   ensure_ascii=False),
        encoding="utf-8")

    print(f"전체 {total:,}행 → 부산 {kept:,}건 적재")
    print(f"  경계 밖/좌표 오류로 제외: {outside:,}건")
    print(f"  CSV 행정동명과 공간 조인 결과 불일치: {mismatch:,}건 (공간 조인을 신뢰)")
    print("  카테고리:", dict(Counter(cat for _, cat in counts.elements()).most_common()))
    for f in ("store-counts.json", "stores-sample.json"):
        print(f"  data/busan/{f}  ({(OUT_DIR / f).stat().st_size/1024:,.0f} KB)")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    if "--peek" in sys.argv:
        peek(sys.argv[1])
    else:
        main(sys.argv[1])
