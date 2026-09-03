#!/usr/bin/env python3
"""상가(상권)정보 원본 CSV에서 부산 행과 필요한 컬럼만 뽑아 gzip으로 줄인다.

원본은 전국 수백 MB라 그대로 주고받기 어렵다. 이 스크립트는 **표준 라이브러리만**
쓰므로 파이썬만 있으면 아무 설치 없이 돌아간다.

    python3 scripts/slim_csv.py <원본.csv> [원본2.csv ...] -o busan-stores.csv.gz

부산은 전국의 약 7%이고 컬럼도 9개만 남기므로, 보통 원본의 2~3% 크기로 줄어든다.
결과 파일을 저장소에 올리면(또는 그대로 전달하면) 다음 단계를 이어갈 수 있다.

    python3 scripts/ingest_stores.py add busan-stores.csv.gz
    python3 scripts/ingest_stores.py build
"""
import csv
import gzip
import json
import sys
from collections import Counter
from pathlib import Path

KEEP = ["상가업소번호", "상호명", "지점명", "상권업종대분류명", "상권업종중분류명",
        "상권업종소분류명", "행정동명", "경도", "위도"]

ALIASES = {
    "상가업소번호": ("상가업소번호", "상가업소_번호"),
    "상호명": ("상호명", "사업장명", "상호"),
    "지점명": ("지점명",),
    "상권업종대분류명": ("상권업종대분류명", "상권업종대분류", "표준산업분류명"),
    "상권업종중분류명": ("상권업종중분류명", "상권업종중분류"),
    "상권업종소분류명": ("상권업종소분류명", "상권업종소분류"),
    "행정동명": ("행정동명", "행정동"),
    "경도": ("경도", "lon", "x", "좌표정보(x)", "위치좌표x"),
    "위도": ("위도", "lat", "y", "좌표정보(y)", "위치좌표y"),
}
SIDO = ("시도명", "시도")
SIDOCD = ("시도코드",)


def norm(s):
    return (s or "").strip().lower().replace(" ", "").replace("_", "")


def find(header, cands):
    idx = {norm(h): h for h in header if h}
    for c in cands:
        if norm(c) in idx:
            return idx[norm(c)]
    return None


def open_maybe_gz(path):
    """CSV를 연다. gzip·zip 압축과 UTF-8/CP949 인코딩을 모두 받아준다.

    공공데이터포털 파일은 CP949(euc-kr)로 배포되는 경우가 흔해서 인코딩을
    먼저 시험해 보고 고른다. zip이면 안에 든 첫 CSV를 읽는다.
    """
    import gzip as _gzip, io as _io, zipfile as _zip

    def sniff(raw):
        for enc in ("utf-8-sig", "cp949"):
            try:
                raw.decode(enc)
                return enc
            except UnicodeDecodeError:
                continue
        return "utf-8"  # 마지막 수단 — 아래에서 errors="replace"로 읽는다

    p = str(path)
    if p.endswith(".zip"):
        zf = _zip.ZipFile(p)
        inner = next((n for n in zf.namelist() if n.lower().endswith(".csv")), None)
        if inner is None:
            raise SystemExit(f"{p}: zip 안에 csv가 없습니다 — {zf.namelist()[:5]}")
        data = zf.read(inner)
    elif p.endswith(".gz"):
        data = None
        with _gzip.open(p, "rb") as fh:
            head = fh.read(1 << 16)
        enc = sniff(head)
        return _gzip.open(p, "rt", encoding=enc, errors="replace", newline="")
    else:
        with open(p, "rb") as fh:
            head = fh.read(1 << 16)
        enc = sniff(head)
        return open(p, encoding=enc, errors="replace", newline="")

    enc = sniff(data[: 1 << 16])
    return _io.StringIO(data.decode(enc, errors="replace"))


def main(paths, out_path):
    total = kept = 0
    with gzip.open(out_path, "wt", encoding="utf-8", newline="") as out:
        writer = csv.DictWriter(out, fieldnames=KEEP)
        writer.writeheader()
        for path in paths:
            with open_maybe_gz(path) as fh:
                reader = csv.DictReader(fh)
                header = reader.fieldnames or []
                cols = {k: find(header, v) for k, v in ALIASES.items()}
                sido_col = find(header, SIDO)
                sidocd_col = find(header, SIDOCD)
                if not cols["상호명"] or not cols["경도"] or not cols["위도"]:
                    sys.exit(f"{path}: 상호명·경도·위도 컬럼을 찾지 못했습니다.\n"
                             f"헤더: {', '.join(header[:25])}")
                n = 0
                drop_sido = drop_coord = 0
                sido_seen = Counter()
                sample = None
                for row in reader:
                    total += 1
                    if sample is None:
                        sample = row
                    if sido_col or sidocd_col:
                        sido_seen[(row.get(sido_col) or row.get(sidocd_col) or "").strip()] += 1
                    if sido_col:
                        if "부산" not in (row.get(sido_col) or ""):
                            drop_sido += 1
                            continue
                    elif sidocd_col and not str(row.get(sidocd_col) or "").startswith("26"):
                        drop_sido += 1
                        continue
                    if not (row.get(cols["경도"]) and row.get(cols["위도"])):
                        drop_coord += 1
                        continue
                    writer.writerow({k: (row.get(c) or "").strip() if c else ""
                                     for k, c in cols.items()})
                    n += 1
                kept += n
                print(f"  {Path(path).name}: {n:,}건 추출 "
                      f"(시도 불일치 {drop_sido:,} / 좌표 없음 {drop_coord:,})")
                if n == 0:
                    print("  ── 0건이라 진단을 출력합니다 ──")
                    print(f"  읽은 행 수: {total:,}")
                    print(f"  인식된 컬럼: " + ", ".join(f"{k}→{v}" for k, v in cols.items() if v))
                    miss = [k for k, v in cols.items() if not v]
                    if miss:
                        print(f"  못 찾은 컬럼: {', '.join(miss)}")
                    print(f"  시도 컬럼: {sido_col or sidocd_col or '없음'}")
                    if sido_seen:
                        print("  시도 값 상위: " +
                              ", ".join(f"{v!r}×{c:,}" for v, c in sido_seen.most_common(5)))
                    if sample:
                        print("  첫 행: " + json.dumps(sample, ensure_ascii=False)[:500])
                    print("  ── 이 내용을 그대로 알려주시면 매핑을 고칩니다 ──")

    src = sum(Path(p).stat().st_size for p in paths)
    dst = Path(out_path).stat().st_size
    print(f"\n전체 {total:,}행 → 부산 {kept:,}건")
    print(f"{src/1024/1024:,.1f} MB → {dst/1024/1024:,.2f} MB "
          f"({dst/src*100:.1f}%)  {out_path}")


if __name__ == "__main__":
    args = sys.argv[1:]
    if "-o" not in args or len(args) < 3:
        sys.exit(__doc__)
    i = args.index("-o")
    main(args[:i], args[i + 1])
