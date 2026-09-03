#!/usr/bin/env python3
"""소상공인시장진흥공단 상가(상권)정보를 부산 가게 데이터로 적재한다.

원본이 커서 여러 조각으로 나눠 받는 상황을 전제로, **누적 → 집계** 두 단계로 나눴다.

    python3 scripts/ingest_stores.py peek  <조각.csv>          헤더·표본만 확인
    python3 scripts/ingest_stores.py add   <조각.csv> [...]    부산 행만 걸러 누적
    python3 scripts/ingest_stores.py build                     누적분을 집계해 출력
    python3 scripts/ingest_stores.py reset                     누적분 삭제

조각이 올 때마다 add 하고, 다 모이면 build 한다. add는 상가업소번호로 중복을
걸러내므로 같은 조각을 두 번 넣어도, 조각끼리 겹쳐도 건수가 부풀지 않는다.

헤더가 없는 조각(줄 단위로 자른 경우)은 앞서 add한 조각의 헤더를 재사용한다.

### 출력

    data/busan/store-counts.json   행정동 × 카테고리 업소 수 (상권 두께 지표)
    data/busan/stores-sample.json  그룹당 최대 12곳 (드릴다운 목록용)

전량을 싣지 않는 이유: 상가정보는 **영업 중인 업소 목록이지 트렌드가 아니다.**
무엇이 뜨는지는 언급량과 burst가 정하고, 이 파일은 후보 풀과 좌표를 댄다
(docs/mvp-busan.md 4.0절). 다만 업소 수 자체는 상권의 두께를 보여주는 신호다.
"""
import csv
import gzip
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

from shapely.geometry import shape, Point
from shapely.strtree import STRtree

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "data" / "busan"
STAGE = OUT_DIR / ".stores-busan.csv"   # 누적 중간 파일 (git 제외)
META = OUT_DIR / ".stores-meta.json"    # 재사용할 헤더 정보
PER_GROUP = 12

STAGE_COLS = ["id", "name", "branch", "big", "mid", "small", "dong_csv", "lon", "lat"]

COLS = {
    "id": ("상가업소번호", "상가업소_번호", "id"),
    "name": ("상호명", "사업장명", "상호"),
    "branch": ("지점명",),
    "big": ("상권업종대분류명", "상권업종대분류", "표준산업분류명"),
    "mid": ("상권업종중분류명", "상권업종중분류"),
    "small": ("상권업종소분류명", "상권업종소분류"),
    "sido": ("시도명", "시도"),
    "sidocd": ("시도코드",),
    "dong_csv": ("행정동명", "행정동"),
    "lon": ("경도", "lon", "x", "좌표정보(x)", "위치좌표x"),
    "lat": ("위도", "lat", "y", "좌표정보(y)", "위치좌표y"),
}

# 위에서부터 먼저 맞는 규칙을 쓴다. 상가 데이터라 자연·명소는 거의 없다 —
# 이 파일이 실제로 채우는 것은 food·cafe·life이고 view는 TourAPI 몫이다.
RULES = [
    ("cafe", ("커피", "카페", "제과", "제빵", "디저트", "아이스크림", "빙수")),
    ("food", ("음식", "식당", "주점", "restaurant")),
    ("culture", ("문화", "박물관", "미술", "영화", "공연", "전시", "도서")),
    ("view", ("관광", "명소", "공원", "유원지", "자연")),
]
DEFAULT_CAT = "life"


def open_csv(path):
    """일반 CSV와 gzip 압축본을 같은 방식으로 연다."""
    if str(path).endswith(".gz"):
        return gzip.open(path, "rt", encoding="utf-8-sig", newline="")
    return open(path, encoding="utf-8-sig", newline="")


def norm(h):
    return (h or "").strip().lower().replace(" ", "").replace("_", "")


def resolve(header):
    idx = {norm(h): h for h in header if h}
    found = {}
    for key, cands in COLS.items():
        for c in cands:
            if norm(c) in idx:
                found[key] = idx[norm(c)]
                break
    return found


def looks_like_header(row):
    return any(norm(c) in {norm(x) for cands in COLS.values() for x in cands} for c in row)


def read_rows(path):
    """조각을 (컬럼매핑, dict행 이터레이터)로 연다. 헤더가 없으면 직전 헤더를 쓴다."""
    fh = open_csv(path)
    first = next(csv.reader([fh.readline()]), [])
    meta = json.loads(META.read_text(encoding="utf-8")) if META.exists() else {}

    if looks_like_header(first):
        header = first
        META.write_text(json.dumps({"header": header}, ensure_ascii=False), encoding="utf-8")
    else:
        header = meta.get("header")
        if not header:
            fh.close()
            sys.exit(f"{path}: 헤더가 없고 재사용할 헤더도 없습니다. "
                     "헤더가 있는 조각을 먼저 add 하세요.")
        fh.seek(0)  # 첫 줄도 데이터다
    return resolve(header), csv.DictReader(fh, fieldnames=header), fh


def cmd_peek(paths):
    for path in paths:
        with open_csv(path) as fh:
            reader = csv.reader(fh)
            first = next(reader, [])
            print(f"\n── {path}")
            if looks_like_header(first):
                print("헤더:", ", ".join(first))
                print("인식된 매핑:")
                for k, v in resolve(first).items():
                    print(f"  {k:8s} → {v}")
            else:
                print("헤더가 없는 조각으로 보입니다. 첫 줄:", first[:8])
            for i, row in enumerate(reader):
                if i >= 2:
                    break
                print("  표본:", row[:10])


def cmd_add(paths):
    seen = set()
    if STAGE.exists():
        with open(STAGE, encoding="utf-8", newline="") as fh:
            for r in csv.DictReader(fh):
                seen.add(r["id"] or f'{r["name"]}|{r["lon"]},{r["lat"]}')

    new = STAGE.exists()
    out = open(STAGE, "a" if new else "w", encoding="utf-8", newline="")
    writer = csv.DictWriter(out, fieldnames=STAGE_COLS)
    if not new:
        writer.writeheader()

    for path in paths:
        cols, reader, fh = read_rows(path)
        for need in ("name", "lon", "lat"):
            if need not in cols:
                fh.close()
                sys.exit(f"{path}: 필수 컬럼 '{need}'을 찾지 못했습니다. peek으로 헤더를 확인하세요.")
        total = kept = dup = 0
        for row in reader:
            total += 1
            sido = (row.get(cols.get("sido", ""), "") or "")
            sidocd = (row.get(cols.get("sidocd", ""), "") or "")
            if sido and "부산" not in sido:
                continue
            if not sido and sidocd and not str(sidocd).startswith("26"):
                continue
            lon, lat = row.get(cols["lon"]), row.get(cols["lat"])
            try:
                lon, lat = float(lon), float(lat)
            except (TypeError, ValueError):
                continue
            rid = (row.get(cols.get("id", ""), "") or "").strip()
            key = rid or f'{row[cols["name"]]}|{lon},{lat}'
            if key in seen:
                dup += 1
                continue
            seen.add(key)
            writer.writerow({
                "id": rid,
                "name": (row.get(cols["name"]) or "").strip(),
                "branch": (row.get(cols.get("branch", ""), "") or "").strip(),
                "big": (row.get(cols.get("big", ""), "") or "").strip(),
                "mid": (row.get(cols.get("mid", ""), "") or "").strip(),
                "small": (row.get(cols.get("small", ""), "") or "").strip(),
                "dong_csv": (row.get(cols.get("dong_csv", ""), "") or "").strip(),
                "lon": round(lon, 6), "lat": round(lat, 6),
            })
            kept += 1
        fh.close()
        print(f"{Path(path).name}: {total:,}행 읽음 → 부산 {kept:,}건 누적, 중복 {dup:,}건 제외")
    out.close()
    print(f"누적 총계: {len(seen):,}건  ({STAGE.relative_to(ROOT)})")


def categorize(big, mid, small):
    blob = " ".join(x for x in (mid, small, big) if x)
    for cat, needles in RULES:
        if any(n in blob for n in needles):
            return cat
    return DEFAULT_CAT


def cmd_build():
    if not STAGE.exists():
        sys.exit("누적된 데이터가 없습니다. 먼저 add 하세요.")
    fc = json.loads((OUT_DIR / "dong.geojson").read_text(encoding="utf-8"))
    polys = [shape(f["geometry"]).buffer(0) for f in fc["features"]]
    props = [f["properties"] for f in fc["features"]]
    tree = STRtree(polys)

    counts, groups = Counter(), defaultdict(list)
    total = outside = mismatch = 0
    with open(STAGE, encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            total += 1
            pt = Point(float(row["lon"]), float(row["lat"]))
            hit = None
            for i in tree.query(pt):
                if polys[i].contains(pt):
                    hit = props[i]
                    break
            if hit is None:
                outside += 1
                continue
            if row["dong_csv"] and row["dong_csv"] != hit["dong_nm"]:
                mismatch += 1  # 경계 갱신 시차 — 공간 조인 결과를 신뢰한다
            cat = categorize(row["big"], row["mid"], row["small"])
            key = (hit["code"], cat)
            counts[key] += 1
            if len(groups[key]) < PER_GROUP:
                groups[key].append({
                    "n": f'{row["name"]} {row["branch"]}'.strip(),
                    "lon": round(float(row["lon"]), 5), "lat": round(float(row["lat"]), 5),
                })

    by_dong = defaultdict(dict)
    for (code, cat), n in counts.items():
        by_dong[code][cat] = n
    (OUT_DIR / "store-counts.json").write_text(json.dumps({
        "note": "행정동 × 카테고리 업소 수. 상권의 두께 지표이며 트렌드 점수가 아니다.",
        "source": "소상공인시장진흥공단 상가(상권)정보",
        "total": total - outside, "counts": by_dong}, ensure_ascii=False), encoding="utf-8")
    (OUT_DIR / "stores-sample.json").write_text(json.dumps({
        "note": f"드릴다운 목록용 표본. 행정동×카테고리 그룹당 최대 {PER_GROUP}곳. "
                "정렬 기준은 트렌드가 아니라 파일 등장 순서다.",
        "source": "소상공인시장진흥공단 상가(상권)정보", "per_group": PER_GROUP,
        "groups": {f"{c}|{k}": v for (c, k), v in groups.items()}},
        ensure_ascii=False), encoding="utf-8")

    print(f"누적 {total:,}건 집계 → 부산 경계 안 {total - outside:,}건")
    print(f"  경계 밖으로 제외: {outside:,}건")
    print(f"  CSV 행정동명 불일치: {mismatch:,}건 (공간 조인을 신뢰)")
    print("  카테고리:", dict(Counter(c for _, c in counts.elements()).most_common()))
    print(f"  행정동 커버리지: {len(by_dong)}/{len(props)}")
    for f in ("store-counts.json", "stores-sample.json"):
        print(f"  data/busan/{f}  ({(OUT_DIR / f).stat().st_size/1024:,.0f} KB)")


def cmd_reset():
    for p in (STAGE, META):
        if p.exists():
            p.unlink()
            print("삭제:", p.relative_to(ROOT))


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    cmd, args = sys.argv[1], sys.argv[2:]
    if cmd == "peek" and args:
        cmd_peek(args)
    elif cmd == "add" and args:
        cmd_add(args)
    elif cmd == "build":
        cmd_build()
    elif cmd == "reset":
        cmd_reset()
    else:
        sys.exit(__doc__)
