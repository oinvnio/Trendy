# data/raw/ — 원본 추출본을 잠시 두는 곳

전국 상가(상권)정보 원본은 수백 MB라 그대로 주고받기 어렵다.
`scripts/slim_csv.py`로 **부산 행 + 필요한 9개 컬럼만** 뽑아 gzip으로 줄인 파일을
여기에 두면 `scripts/ingest_stores.py`가 이어받는다.

```bash
python3 scripts/slim_csv.py <원본.csv> [원본2.csv ...] -o data/raw/busan-stores.csv.gz
python3 scripts/ingest_stores.py add data/raw/busan-stores.csv.gz
python3 scripts/ingest_stores.py build
```

집계 결과(`data/busan/store-counts.json`, `stores-sample.json`)가 나오면
이 폴더의 추출본은 지워도 된다. 원본은 저장소에 커밋하지 않는다.

출처: 소상공인시장진흥공단 상가(상권)정보 (공공데이터포털)
