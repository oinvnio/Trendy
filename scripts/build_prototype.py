#!/usr/bin/env python3
"""렌더링 프로토타입을 단일 HTML로 빌드한다.

data/busan/* 와 prototype/keywords.sample.json 을 하나의 JSON으로 묶어
prototype/src/{style.css,app.js} 와 함께 인라인한다. 외부 요청이 없으므로
파일 하나만 열면 동작한다.

출력
  prototype/index.html     로컬에서 바로 여는 완전한 문서
  prototype/artifact.html  Artifact 게시용 조각 (doctype/html/head/body 없음)

사용법: python3 scripts/build_prototype.py
"""
import json
import random
from pathlib import Path

from shapely.geometry import shape

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data" / "busan"
SRC = ROOT / "prototype" / "src"
OUT = ROOT / "prototype"

R = 5  # 좌표 반올림 자리수 (약 1m)
SPREAD_R = 0.013  # 같은 동 키워드를 흩뿌릴 최대 반경(도). 위도 기준 약 1.4km


def rings(geometry):
    """폴리곤/멀티폴리곤을 [[ [lon,lat], ... ], ...] 링 목록으로 편다."""
    polys = geometry["coordinates"] if geometry["type"] == "MultiPolygon" else [geometry["coordinates"]]
    out = []
    for poly in polys:
        for ring in poly:
            out.append([[round(x, R), round(y, R)] for x, y in ring])
    return out


def spread_within(poly, anchor, n, seed):
    """행정동 폴리곤 안에 n개 지점을 서로 떨어뜨려 고른다.

    같은 동에 묶인 키워드가 전부 대표점 한 곳에 쌓이면 줌인해도 라벨이
    겹쳐 사라진다. 가게별 실제 좌표(카카오 로컬·TourAPI)를 확보하기 전까지
    쓰는 임시 분산이며, 이렇게 나온 좌표는 approx로 표시한다.
    """
    if n <= 1:
        return [anchor]
    rng = random.Random(seed)
    # 분산 반경 상한 — 기장읍처럼 100km²가 넘는 폴리곤에서 폴리곤 전체에
    # 흩뿌리면 라벨이 실제 위치에서 수 km 떨어진다. 대표점 주변으로 제한한다.
    area = poly.intersection(shape({"type": "Point", "coordinates": anchor}).buffer(SPREAD_R))
    if area.area > 0:
        poly = area
    minx, miny, maxx, maxy = poly.bounds
    pool = []
    for _ in range(n * 260):
        pt = (rng.uniform(minx, maxx), rng.uniform(miny, maxy))
        if poly.contains(shape({"type": "Point", "coordinates": pt})):
            pool.append(pt)
        if len(pool) >= n * 26:
            break
    if len(pool) < n:
        return [anchor] * n

    # 대표점에서 시작해 가장 멀리 떨어진 후보를 차례로 고른다
    picked = [anchor]
    while len(picked) < n:
        best, best_d = None, -1
        for c in pool:
            d = min((c[0] - q[0]) ** 2 + (c[1] - q[1]) ** 2 for q in picked)
            if d > best_d:
                best, best_d = c, d
        picked.append(best)
        pool.remove(best)
    return picked


def main():
    sil = json.loads((DATA / "silhouette.geojson").read_text(encoding="utf-8"))["features"][0]
    sgg = json.loads((DATA / "sgg.geojson").read_text(encoding="utf-8"))["features"]
    dong = json.loads((DATA / "dong.geojson").read_text(encoding="utf-8"))["features"]
    gaz = json.loads((DATA / "gazetteer.json").read_text(encoding="utf-8"))
    kw = json.loads((OUT / "keywords.sample.json").read_text(encoding="utf-8"))

    # 지명 사전 조회용 색인 — 샘플 키워드의 앵커가 실제 행정동인지 검증한다
    index = {(p["parent"], p["name"]): p for p in gaz["places"] if p["type"] == "dong"}
    polys = {(f["properties"]["sgg_nm"], f["properties"]["dong_nm"]): shape(f["geometry"]).buffer(0)
             for f in dong}

    missing = [f'{k["text"]} → {k["sgg"]} {k["dong"]}'
               for k in kw["keywords"] if (k["sgg"], k["dong"]) not in index]
    if missing:
        raise SystemExit("앵커가 지명 사전에 없습니다:\n  " + "\n  ".join(missing))

    # 같은 행정동에 묶인 키워드는 동 안에서 서로 떨어뜨려 배치한다
    grouped = {}
    for k in kw["keywords"]:
        grouped.setdefault((k["sgg"], k["dong"]), []).append(k)

    keywords, approx_n = [], 0
    for key, group in grouped.items():
        place = index[key]
        anchor = (place["lon"], place["lat"])
        group.sort(key=lambda k: -k["score"])  # 점수 높은 쪽이 대표점을 갖는다
        pts = spread_within(polys[key], anchor, len(group), seed=key[0] + key[1])
        for k, (lon, lat) in zip(group, pts):
            is_approx = (lon, lat) != anchor
            approx_n += is_approx
            keywords.append({
                "t": k["text"], "c": k["category"], "tr": k["tier"], "s": k["score"],
                "k": k.get("kind", "spot"), "a": 1 if is_approx else 0,
                "lon": round(lon, R), "lat": round(lat, R),
                "d": f'{k["sgg"]} {k["dong"]}',
            })

    places = [
        {"n": p["name"], "k": p["type"], "lon": p["lon"], "lat": p["lat"],
         "d": p["parent"] or "부산광역시"}
        for p in gaz["places"] if p["type"] in ("sgg", "dong")
    ]

    # 상가(상권)정보 집계 — 있으면 드릴다운 목록과 라벨 크기에 쓴다
    stores = {"sgg": {}, "dong": {}}
    counts_p, sample_p = DATA / "store-counts.json", DATA / "stores-sample.json"
    if counts_p.exists() and sample_p.exists():
        counts = json.loads(counts_p.read_text(encoding="utf-8"))["counts"]
        sample = json.loads(sample_p.read_text(encoding="utf-8"))["groups"]
        # 행정동 코드 → (구·군, "구·군 행정동")
        unit_of = {f["properties"]["code"]:
                   (f["properties"]["sgg_nm"],
                    f'{f["properties"]["sgg_nm"]} {f["properties"]["dong_nm"]}')
                   for f in dong}
        for code, per_cat in counts.items():
            if code not in unit_of:
                continue
            sgg_nm, dong_nm = unit_of[code]
            for cat, n in per_cat.items():
                lst = sample.get(f"{code}|{cat}", [])
                for unit, level, cap in ((dong_nm, "dong", 8), (sgg_nm, "sgg", 12)):
                    slot = stores[level].setdefault(f"{unit}|{cat}", {"n": 0, "list": []})
                    slot["n"] += n
                    for it in lst:
                        if len(slot["list"]) < cap:
                            slot["list"].append(it)
        print(f"  상가정보 연결: 구·군 {len(stores['sgg'])}조합 / 행정동 {len(stores['dong'])}조합")

    bundle = {
        "bbox": sil["properties"]["bbox"],
        "stores": stores,
        "silhouette": rings(sil["geometry"]),
        "sgg": [rings(f["geometry"]) for f in sgg],
        "dong": [rings(f["geometry"]) for f in dong],
        "places": places,
        "keywords": keywords,
        "categories": kw["categories"],
        "counts": {
            "dong": len(dong), "sgg": len(sgg), "keywords": len(keywords),
            "stores": sum(1 for k in keywords if k["k"] == "store"),
            "approx": approx_n,
        },
    }

    data = json.dumps(bundle, ensure_ascii=False, separators=(",", ":"))
    css = (SRC / "style.css").read_text(encoding="utf-8")
    js = (SRC / "app.js").read_text(encoding="utf-8")
    body = (SRC / "body.html").read_text(encoding="utf-8")

    head = (
        '<title>부산 트렌드 워드맵</title>\n'
        '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>\n'
        '<link rel="stylesheet" href="https://fonts.googleapis.com/css2'
        '?family=IBM+Plex+Sans+KR:wght@400;500;600'
        '&family=IBM+Plex+Mono:wght@400;500'
        '&family=Noto+Serif+KR:wght@600&display=swap">\n'
        f"<style>\n{css}\n</style>"
    )
    tail = (
        f'<script type="application/json" id="trendy-data">{data}</script>\n'
        f"<script>\n{js}\n</script>"
    )

    fragment = f"{head}\n{body}\n{tail}\n"
    (OUT / "artifact.html").write_text(fragment, encoding="utf-8")
    (OUT / "index.html").write_text(
        f'<!doctype html>\n<html lang="ko">\n<head>\n<meta charset="utf-8">\n'
        f'<meta name="viewport" content="width=device-width,initial-scale=1">\n'
        f'{head}\n</head>\n<body>\n{body}\n{tail}\n</body>\n</html>\n',
        encoding="utf-8",
    )
    for p in (OUT / "index.html", OUT / "artifact.html"):
        print(f"  {p.relative_to(ROOT)}  ({p.stat().st_size/1024:,.0f} KB)")
    print("  데이터:", bundle["counts"])


if __name__ == "__main__":
    main()
