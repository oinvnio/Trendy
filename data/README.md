# data/ — 부산 MVP 기초 데이터

`scripts/`의 빌드 스크립트가 생성한 파일이다. 손으로 고치지 말고 스크립트를 다시 돌린다.

## 파일

| 파일 | 내용 | 용도 |
| --- | --- | --- |
| `busan/silhouette.geojson` | 부산 전체 외곽 (MultiPolygon 4조각) | 줌 L1 워드클라우드 마스크 |
| `busan/sgg.geojson` | 구·군 16개 경계 + 라벨 대표점 | 줌 L2 |
| `busan/dong.geojson` | 행정동 206개 경계 + 라벨 대표점 | 줌 L3 |
| `busan/gazetteer.json` | 지명 사전 223건 (시 1 / 구·군 16 / 행정동 206) | 폴백 라벨 + 검색어 조합 |
| `busan/preview-fallback-labels.svg` | 폴백 라벨만으로 실루엣을 채운 미리보기 | 공백 육안 확인 |

라벨 대표점은 면적 중심점(centroid)이 아니라 `representative_point`다. 부산은 영도·가덕도 등
섬과 오목한 해안선이 많아 중심점을 쓰면 라벨이 바다에 떨어지는 구역이 생긴다.
멀티폴리곤은 가장 큰 조각을 기준으로 잡는다. (206개 전부 자기 폴리곤 내부에 있음을 검증)

## 재생성

```bash
# 1) 전국 행정동 경계 원본 확보 (약 34MB)
git clone --depth 1 --filter=blob:none --no-checkout https://github.com/vuski/admdongkor /tmp/admdongkor
git -C /tmp/admdongkor checkout HEAD -- ver20260701/HangJeongDong_ver20260701.geojson

# 2) 부산 추출 + 병합 + 단순화 + 지명 사전 생성
pip install shapely topojson
python3 scripts/build_geo.py /tmp/admdongkor/ver20260701/HangJeongDong_ver20260701.geojson

# 3) (선택) 폴백 라벨 미리보기 갱신
python3 scripts/preview_labels.py
```

`topojson`이 설치되어 있으면 위상을 보존하며 단순화한다(인접 구·군 경계 사이에 틈이 생기지 않음).
없으면 개별 단순화로 폴백하므로 경계선을 렌더링할 계획이라면 설치하는 편이 낫다.

## 역명 추가

역 좌표 원본은 이 저장소에 포함하지 않았다(재배포 조건 미확인). 공공데이터포털에서
역사 정보 데이터셋을 받아 CSV로 저장한 뒤:

```bash
python3 scripts/merge_stations.py stations.csv
```

각 역을 공간 조인으로 행정동에 붙이고 `gazetteer.json`에 `type: "station"`으로 병합한다.
부산 경계 밖 좌표는 자동으로 제외한다.

## 출처 및 라이선스

경계 데이터의 원자료는 **통계청 통계지리정보서비스(SGIS)** 행정동 경계이며
**공공누리 제1유형(출처표시)** 으로 개방되어 있다. 가공물은
[vuski/admdongkor](https://github.com/vuski/admdongkor)가 **CC BY 4.0** 으로 배포한다.

출처표시 의무는 가공 여부와 무관하게 유지되므로, 이 데이터를 서비스에 노출할 때
**SGIS 출처표시를 반드시 보존**해야 한다. 각 GeoJSON의 `source` 필드에 출처 문자열을 넣어 두었다.

기준 시점: 행정동 경계 `ver20260701` (2026-07-01).
