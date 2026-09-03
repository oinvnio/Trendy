#!/usr/bin/env python3
"""폴백 라벨만으로 부산 실루엣이 얼마나 채워지는지 SVG로 확인한다.

트렌드 데이터가 아직 없으므로 지명(구·군명 + 행정동명)만 배치한다.
"트렌드가 없는 구역은 지명·역명으로 채운다"는 규칙이 실제로 실루엣의
여백을 메우는지, 어디가 여전히 비는지를 눈으로 보기 위한 검증용 도구다.

    python3 scripts/preview_labels.py [출력경로]
"""
import json
import math
import sys
from pathlib import Path

from shapely.geometry import shape

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "data" / "busan"
WIDTH = 1100
MARGIN = 24
# 카테고리 색 대신, 지금은 라벨 종류로만 구분한다
STYLE = {"sgg": (19, "#1f4e79", 0.95), "dong": (11, "#6b7280", 0.85)}


def main(out_path):
    sil = json.loads((OUT_DIR / "silhouette.geojson").read_text(encoding="utf-8"))
    gaz = json.loads((OUT_DIR / "gazetteer.json").read_text(encoding="utf-8"))
    feat = sil["features"][0]
    minx, miny, maxx, maxy = feat["properties"]["bbox"]

    # 위도 35도 부근의 경도 압축을 반영한 단순 정거원통도법
    kx = math.cos(math.radians((miny + maxy) / 2))
    sx = (WIDTH - 2 * MARGIN) / ((maxx - minx) * kx)
    height = int((maxy - miny) * sx + 2 * MARGIN)

    def project(lon, lat):
        return (MARGIN + (lon - minx) * kx * sx, MARGIN + (maxy - lat) * sx)

    # 실루엣 경로
    paths = []
    geom = shape(feat["geometry"])
    for poly in getattr(geom, "geoms", [geom]):
        for ring in [poly.exterior, *poly.interiors]:
            pts = " ".join("%.1f,%.1f" % project(x, y) for x, y in ring.coords)
            paths.append(f'<polygon points="{pts}" />')

    # 라벨 배치: 구·군 먼저, 그다음 행정동. 겹치면 뒤에 오는 쪽을 버린다
    placed, dropped = [], 0
    boxes = []
    order = [p for p in gaz["places"] if p["type"] == "sgg"] + \
            [p for p in gaz["places"] if p["type"] == "dong"]
    for p in order:
        size, color, opacity = STYLE[p["type"]]
        x, y = project(p["lon"], p["lat"])
        w, h = len(p["name"]) * size * 0.62, size
        box = (x - w / 2, y - h / 2, x + w / 2, y + h / 2)
        if any(not (box[2] < b[0] or box[0] > b[2] or box[3] < b[1] or box[1] > b[3])
               for b in boxes):
            dropped += 1
            continue
        boxes.append(box)
        placed.append(f'<text x="{x:.1f}" y="{y:.1f}" font-size="{size}" fill="{color}" '
                      f'fill-opacity="{opacity}">{p["name"]}</text>')

    svg = f"""<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" height="{height}" viewBox="0 0 {WIDTH} {height}">
<style>
 text {{ font-family: "Noto Sans KR", "Apple SD Gothic Neo", "Malgun Gothic", sans-serif;
        text-anchor: middle; dominant-baseline: middle; }}
 polygon {{ fill: #eef2f6; stroke: #c3ccd6; stroke-width: 1; }}
</style>
<rect width="100%" height="100%" fill="#ffffff"/>
<g>{''.join(paths)}</g>
<g>{''.join(placed)}</g>
<text x="{MARGIN}" y="{height - MARGIN}" font-size="12" fill="#9aa4b0" text-anchor="start">
부산 폴백 라벨 미리보기 — 지명 {len(placed)}개 배치 / {dropped}개 겹침 제외 (트렌드 데이터 이전 상태)</text>
</svg>"""
    Path(out_path).write_text(svg, encoding="utf-8")
    print(f"{out_path}  배치 {len(placed)} / 겹침 제외 {dropped} / 전체 {len(order)}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else OUT_DIR / "preview-fallback-labels.svg")
