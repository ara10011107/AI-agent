"""데이터 카탈로그: data/ 폴더의 CSV·엑셀 파일을 읽어 DuckDB 테이블로 등록합니다.

공공데이터 CSV는 CP949(EUC-KR)로 배포되는 경우가 많아 인코딩을 순서대로 시도합니다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import duckdb
import pandas as pd
import yaml

SUPPORTED_SUFFIXES = {".csv", ".txt", ".xlsx", ".xls"}
ENCODINGS = ("utf-8-sig", "cp949", "euc-kr", "utf-16")
DESCRIPTION_FILE = "catalog.yaml"


@dataclass
class Dataset:
    table: str
    path: Path
    frame: pd.DataFrame
    title: str
    description: str = ""
    source: str = ""
    encoding: str = ""
    column_notes: dict[str, str] = field(default_factory=dict)

    @property
    def is_sample(self) -> bool:
        return "sample" in self.path.parts


def read_table_file(path: Path) -> tuple[pd.DataFrame, str]:
    """파일을 DataFrame으로 읽고 (데이터, 사용한 인코딩)을 돌려줍니다."""
    suffix = path.suffix.lower()
    if suffix in (".xlsx", ".xls"):
        return pd.read_excel(path), "excel"
    last_error: Exception | None = None
    for enc in ENCODINGS:
        try:
            return pd.read_csv(path, encoding=enc, sep=None, engine="python"), enc
        except (UnicodeDecodeError, UnicodeError) as e:
            last_error = e
    raise ValueError(f"{path.name}: 지원하는 인코딩({', '.join(ENCODINGS)})으로 읽을 수 없습니다") from last_error


def to_table_name(stem: str, taken: set[str]) -> str:
    """파일 이름을 SQL에서 쓰기 쉬운 테이블 이름으로 바꿉니다 (한글 허용, 공백·기호 제거)."""
    name = re.sub(r"[^0-9A-Za-z가-힣_]+", "_", stem).strip("_") or "dataset"
    if name[0].isdigit():
        name = f"t_{name}"
    base, i = name, 2
    while name in taken:
        name = f"{base}_{i}"
        i += 1
    return name


def _load_descriptions(data_dir: Path) -> dict:
    path = data_dir / DESCRIPTION_FILE
    if not path.exists():
        return {}
    with path.open(encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


class Catalog:
    """여러 데이터셋을 하나의 DuckDB 연결에 등록해 SQL과 pandas 양쪽에서 쓰게 합니다."""

    def __init__(self) -> None:
        self.con = duckdb.connect(database=":memory:")
        # SQL로 임의 파일을 읽거나 쓰지 못하게 막고, 설정도 잠급니다 (등록한 DataFrame만 조회 가능)
        self.con.execute("SET enable_external_access = false")
        self.con.execute("SET lock_configuration = true")
        self.datasets: dict[str, Dataset] = {}
        self.load_errors: dict[str, str] = {}

    @classmethod
    def from_directory(cls, data_dir: Path, include_samples: bool = True) -> "Catalog":
        catalog = cls()
        descriptions = _load_descriptions(data_dir)
        for path in sorted(data_dir.rglob("*")):
            if path.suffix.lower() not in SUPPORTED_SUFFIXES or not path.is_file():
                continue
            if not include_samples and "sample" in path.relative_to(data_dir).parts:
                continue
            meta = descriptions.get(path.name, {})
            try:
                catalog.add_file(path, meta)
            except Exception as e:  # 한 파일이 깨져도 나머지는 계속 사용
                catalog.load_errors[path.name] = str(e)
        return catalog

    def add_file(self, path: Path, meta: dict | None = None) -> Dataset:
        meta = meta or {}
        frame, encoding = read_table_file(path)
        frame.columns = [str(c).strip() for c in frame.columns]
        table = to_table_name(meta.get("table") or path.stem, set(self.datasets))
        return self.add_frame(table, frame, path=path, encoding=encoding, meta=meta)

    def add_frame(self, table: str, frame: pd.DataFrame, path: Path | None = None,
                  encoding: str = "", meta: dict | None = None) -> Dataset:
        meta = meta or {}
        self.con.register(table, frame)
        ds = Dataset(
            table=table,
            path=path or Path(table),
            frame=frame,
            title=meta.get("title", table),
            description=meta.get("description", ""),
            source=meta.get("source", ""),
            encoding=encoding,
            column_notes=meta.get("columns", {}) or {},
        )
        self.datasets[table] = ds
        return ds

    def summary_lines(self) -> list[str]:
        lines = []
        for ds in self.datasets.values():
            tag = " [가상 샘플]" if ds.is_sample else ""
            desc = f" - {ds.description}" if ds.description else ""
            lines.append(f"- `{ds.table}`{tag}: {ds.title} ({len(ds.frame):,}행 × {ds.frame.shape[1]}열){desc}")
        return lines

    def profile(self, table: str, sample_rows: int = 5) -> str:
        """컬럼 타입, 결측, 고유값, 대표값을 마크다운으로 요약합니다."""
        ds = self.datasets[table]
        df = ds.frame
        out = [f"## {ds.title} (`{table}`)",
               f"- 파일: {ds.path.name} / 인코딩: {ds.encoding or '-'} / {len(df):,}행 × {df.shape[1]}열"]
        if ds.is_sample:
            out.append("- 주의: 데모용 가상(합성) 데이터입니다. 실제 통계로 해석하면 안 됩니다.")
        if ds.source:
            out.append(f"- 출처: {ds.source}")
        if ds.description:
            out.append(f"- 설명: {ds.description}")
        out.append("\n| 컬럼 | 타입 | 결측 | 고유값 | 예시/범위 | 메모 |\n|---|---|---|---|---|---|")
        for col in df.columns:
            s = df[col]
            if pd.api.types.is_numeric_dtype(s):
                example = f"{s.min():,} ~ {s.max():,} (평균 {s.mean():,.2f})" if s.notna().any() else "-"
            else:
                top = s.dropna().astype(str).value_counts().head(4).index.tolist()
                example = ", ".join(top)
            note = ds.column_notes.get(col, "")
            out.append(f"| {col} | {s.dtype} | {int(s.isna().sum()):,} | {s.nunique(dropna=True):,} | {example} | {note} |")
        out.append(f"\n상위 {sample_rows}행:\n")
        out.append(df.head(sample_rows).to_markdown(index=False))
        return "\n".join(out)
