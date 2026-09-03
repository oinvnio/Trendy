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
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data" / "busan"
SRC = ROOT / "prototype" / "src"
OUT = ROOT / "prototype"

R = 5  # 좌표 반올림 자리수 (약 1m)


def rings(geometry):
    """폴리곤/멀티폴리곤을 [[ [lon,lat], ... ], ...] 링 목록으로 편다."""
    polys = geometry["coordinates"] if geometry["type"] == "MultiPolygon" else [geometry["coordinates"]]
    out = []
    for poly in polys:
        for ring in poly:
            out.append([[round(x, R), round(y, R)] for x, y in ring])
    return out


def main():
    sil = json.loads((DATA / "silhouette.geojson").read_text(encoding="utf-8"))["features"][0]
    sgg = json.loads((DATA / "sgg.geojson").read_text(encoding="utf-8"))["features"]
    dong = json.loads((DATA / "dong.geojson").read_text(encoding="utf-8"))["features"]
    gaz = json.loads((DATA / "gazetteer.json").read_text(encoding="utf-8"))
    kw = json.loads((OUT / "keywords.sample.json").read_text(encoding="utf-8"))

    # 지명 사전 조회용 색인 — 샘플 키워드의 앵커가 실제 행정동인지 검증한다
    index = {(p["parent"], p["name"]): p for p in gaz["places"] if p["type"] == "dong"}
    keywords, missing = [], []
    for k in kw["keywords"]:
        place = index.get((k["sgg"], k["dong"]))
        if place is None:
            missing.append(f'{k["text"]} → {k["sgg"]} {k["dong"]}')
            continue
        keywords.append({
            "t": k["text"], "c": k["category"], "tr": k["tier"], "s": k["score"],
            "lon": place["lon"], "lat": place["lat"], "d": f'{k["sgg"]} {k["dong"]}',
        })
    if missing:
        raise SystemExit("앵커가 지명 사전에 없습니다:\n  " + "\n  ".join(missing))

    places = [
        {"n": p["name"], "k": p["type"], "lon": p["lon"], "lat": p["lat"],
         "d": p["parent"] or "부산광역시"}
        for p in gaz["places"] if p["type"] in ("sgg", "dong")
    ]

    bundle = {
        "bbox": sil["properties"]["bbox"],
        "silhouette": rings(sil["geometry"]),
        "sgg": [rings(f["geometry"]) for f in sgg],
        "dong": [rings(f["geometry"]) for f in dong],
        "places": places,
        "keywords": keywords,
        "categories": kw["categories"],
        "counts": {"dong": len(dong), "sgg": len(sgg), "keywords": len(keywords)},
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
