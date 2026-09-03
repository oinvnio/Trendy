#!/usr/bin/env python3
"""부산 행정 경계 3종(행정동 / 구·군 / 시 실루엣)과 지명 사전을 생성한다.

입력: 전국 행정동 경계 GeoJSON (vuski/admdongkor)
출력: data/busan/{dong,sgg,silhouette}.geojson, data/busan/gazetteer.json

사용법:
    python3 scripts/build_geo.py <HangJeongDong_verYYYYMMDD.geojson>

출처 표시 의무: 원자료는 통계청 SGIS 행정동 경계(공공누리 제1유형)이며,
가공물은 CC BY 4.0(vuski/admdongkor)이다. data/README.md 참고.
"""
import json
import sys
from pathlib import Path

from shapely.geometry import mapping, shape
from shapely.ops import unary_union

try:
    import topojson as tp
except ImportError:  # 위상 보존 단순화가 없으면 개별 단순화로 폴백
    tp = None

SIDO_CODE = "26"  # 부산광역시
ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "data" / "busan"

# 단순화 강도(도 단위). 0.0001도 ≈ 11m
TOLERANCE = {"dong": 0.00015, "sgg": 0.00025, "silhouette": 0.0004}


def label_point(geom):
    """라벨을 놓을 대표점. 멀티폴리곤은 가장 큰 조각을 기준으로 삼는다.

    부산은 영도·가덕도 등 섬이 많아 면적 중심점(centroid)을 쓰면
    라벨이 바다 위에 떨어질 수 있어 representative_point를 쓴다.
    """
    if geom.geom_type == "MultiPolygon":
        geom = max(geom.geoms, key=lambda g: g.area)
    pt = geom.representative_point()
    return [round(pt.x, 6), round(pt.y, 6)]


def simplify(geoms, tolerance):
    """가능하면 위상을 보존하며 단순화한다(인접 경계 사이 틈 방지)."""
    if tp is not None:
        topo = tp.Topology(list(geoms), prequantize=False, toposimplify=tolerance)
        fc = json.loads(topo.to_geojson())
        return [shape(f["geometry"]) for f in fc["features"]]
    return [g.simplify(tolerance, preserve_topology=True) for g in geoms]


def write_geojson(path, features, note):
    fc = {
        "type": "FeatureCollection",
        "name": path.stem,
        "note": note,
        "source": "통계청 SGIS 행정동 경계(공공누리 제1유형) / vuski·admdongkor (CC BY 4.0)",
        "features": features,
    }
    path.write_text(json.dumps(fc, ensure_ascii=False), encoding="utf-8")
    size = path.stat().st_size / 1024
    print(f"  {path.relative_to(ROOT)}  ({len(features)}건, {size:,.0f} KB)")


def main(src):
    print(f"입력: {src}")
    data = json.loads(Path(src).read_text(encoding="utf-8"))
    feats = [f for f in data["features"] if f["properties"]["sido"] == SIDO_CODE]
    if not feats:
        sys.exit("부산(sido=26) 피처를 찾지 못했습니다. 입력 파일을 확인하세요.")
    print(f"부산 행정동 {len(feats)}건 추출")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    gazetteer = []

    # 1) 행정동 -------------------------------------------------------------
    dong_geoms = simplify([shape(f["geometry"]) for f in feats], TOLERANCE["dong"])
    dong_features = []
    for f, geom in zip(feats, dong_geoms):
        p = f["properties"]
        # adm_nm은 "부산광역시 중구 중앙동" 형태라 마지막 토큰이 동 이름
        dong_nm = p["adm_nm"].split()[-1]
        lp = label_point(geom)
        dong_features.append({
            "type": "Feature",
            "properties": {
                "code": p["adm_cd2"],
                "sgg_code": p["sgg"],
                "sgg_nm": p["sggnm"],
                "dong_nm": dong_nm,
                "full_nm": p["adm_nm"],
                "label_lon": lp[0],
                "label_lat": lp[1],
            },
            "geometry": mapping(geom),
        })
        gazetteer.append({
            "type": "dong",
            "name": dong_nm,
            "full_name": p["adm_nm"],
            "code": p["adm_cd2"],
            "parent": p["sggnm"],
            "lon": lp[0],
            "lat": lp[1],
        })
    write_geojson(OUT_DIR / "dong.geojson", dong_features, "부산 행정동 경계 + 라벨 대표점 (줌 L3)")

    # 2) 구·군 (행정동을 sgg 코드로 병합) -----------------------------------
    by_sgg = {}
    for f in feats:
        p = f["properties"]
        by_sgg.setdefault((p["sgg"], p["sggnm"]), []).append(shape(f["geometry"]))
    keys = list(by_sgg)
    sgg_geoms = simplify([unary_union(by_sgg[k]) for k in keys], TOLERANCE["sgg"])
    sgg_features = []
    for (code, name), geom in zip(keys, sgg_geoms):
        lp = label_point(geom)
        sgg_features.append({
            "type": "Feature",
            "properties": {
                "code": code, "sgg_nm": name,
                "dong_count": len(by_sgg[(code, name)]),
                "label_lon": lp[0], "label_lat": lp[1],
            },
            "geometry": mapping(geom),
        })
        gazetteer.append({
            "type": "sgg", "name": name, "full_name": f"부산광역시 {name}",
            "code": code, "parent": "부산광역시", "lon": lp[0], "lat": lp[1],
        })
    write_geojson(OUT_DIR / "sgg.geojson", sgg_features, "부산 구·군 경계 + 라벨 대표점 (줌 L2)")

    # 3) 시 실루엣 (전체 병합 후 단순화) ------------------------------------
    whole = unary_union([shape(f["geometry"]) for f in feats])
    whole = simplify([whole], TOLERANCE["silhouette"])[0]
    lp = label_point(whole)
    write_geojson(
        OUT_DIR / "silhouette.geojson",
        [{
            "type": "Feature",
            "properties": {"name": "부산광역시", "code": SIDO_CODE,
                           "label_lon": lp[0], "label_lat": lp[1],
                           "bbox": [round(v, 6) for v in whole.bounds]},
            "geometry": mapping(whole),
        }],
        "부산 전체 실루엣 — 워드클라우드 라벨 배치 마스크 (줌 L1)",
    )
    gazetteer.append({
        "type": "sido", "name": "부산광역시", "full_name": "부산광역시",
        "code": SIDO_CODE, "parent": None, "lon": lp[0], "lat": lp[1],
    })

    # 4) 지명 사전 ----------------------------------------------------------
    gaz = {
        "note": "폴백 라벨(트렌드 부재 구역)과 검색어 조합(지명+키워드)에 함께 쓰인다.",
        "source": "통계청 SGIS 행정동 경계(공공누리 제1유형) / vuski·admdongkor (CC BY 4.0)",
        "counts": {t: sum(1 for e in gazetteer if e["type"] == t) for t in ("sido", "sgg", "dong")},
        "places": sorted(gazetteer, key=lambda e: (e["type"], e["code"])),
    }
    path = OUT_DIR / "gazetteer.json"
    path.write_text(json.dumps(gaz, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"  {path.relative_to(ROOT)}  ({len(gazetteer)}건, {path.stat().st_size/1024:,.0f} KB)")
    print("완료. 역명은 scripts/merge_stations.py 로 추가한다.")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    main(sys.argv[1])
