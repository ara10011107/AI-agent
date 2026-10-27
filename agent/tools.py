"""에이전트가 호출하는 도구들의 정의(JSON 스키마)와 실행기.

도구 실행 결과 중 사람이 봐야 하는 것(표, 차트, 실행한 SQL·코드)은 `Artifact`로 모아
화면 표시와 보고서 생성에 사용합니다.
"""

from __future__ import annotations

import contextlib
import io
import re
import traceback
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go

from .catalog import Catalog

MAX_ROWS_TO_MODEL = 60
MAX_OUTPUT_CHARS = 8000
READ_ONLY_SQL = re.compile(r"^\s*(\(|SELECT|WITH|DESCRIBE|SHOW|SUMMARIZE|PIVOT|UNPIVOT|FROM)\b", re.IGNORECASE)

TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "name": "list_datasets",
        "description": "사용 가능한 데이터셋(테이블) 목록과 행·열 수, 설명을 보여줍니다. 분석을 시작할 때 먼저 호출하세요.",
        "input_schema": {"type": "object", "properties": {}, "required": [], "additionalProperties": False},
        "strict": True,
    },
    {
        "name": "describe_dataset",
        "description": (
            "한 테이블의 컬럼 이름, 타입, 결측치 수, 고유값 수, 대표값, 상위 몇 행을 보여줍니다. "
            "SQL이나 파이썬을 쓰기 전에 반드시 해당 테이블을 한 번은 확인해 실제 컬럼 이름과 값의 형태를 파악하세요."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"table": {"type": "string", "description": "list_datasets에 나온 테이블 이름"}},
            "required": ["table"],
            "additionalProperties": False,
        },
        "strict": True,
    },
    {
        "name": "run_sql",
        "description": (
            "DuckDB SQL(읽기 전용: SELECT/WITH 등)을 실행합니다. 집계·필터·조인에 가장 적합합니다. "
            "한글 컬럼·테이블 이름은 큰따옴표로 감싸세요(예: SELECT \"읍면동\", SUM(\"이용금액\") FROM 테이블 GROUP BY 1). "
            f"결과는 최대 {MAX_ROWS_TO_MODEL}행까지만 보이므로 집계해서 줄이세요. 결과표는 사용자 화면에도 표시됩니다."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "실행할 DuckDB SQL"},
                "title": {"type": "string", "description": "사용자에게 보여줄 결과표 제목(한국어)"},
            },
            "required": ["query", "title"],
            "additionalProperties": False,
        },
        "strict": True,
    },
    {
        "name": "run_python",
        "description": (
            "파이썬 코드를 실행합니다. 상관분석, 증감률, 회귀·예측 같은 통계 계산과 차트 작성에 사용하세요. "
            "미리 준비된 이름: pd, np, px(plotly.express), go(plotly.graph_objects), "
            "tables(테이블 이름 -> DataFrame 딕셔너리, 원본은 수정하지 말고 .copy()해서 쓰기), "
            "sql(query) -> DataFrame, show_chart(fig, title) 차트를 사용자 화면에 표시, "
            "show_table(df, title) 표를 사용자 화면에 표시. "
            "결과는 print()로 출력해야 보입니다. 변수는 다음 호출까지 유지됩니다. "
            "차트는 한국어 제목·축 이름을 붙이고, 이미지 파일 저장이나 네트워크 접근은 하지 마세요."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "실행할 파이썬 코드"},
                "purpose": {"type": "string", "description": "이 코드로 확인하려는 것(한 문장, 한국어)"},
            },
            "required": ["code", "purpose"],
            "additionalProperties": False,
        },
        "strict": True,
    },
]


@dataclass
class Artifact:
    """사용자 화면·보고서에 보여줄 결과물."""
    kind: str  # "table" | "chart" | "sql" | "code" | "text" | "error"
    title: str
    content: Any = None
    detail: str = ""


@dataclass
class ToolOutcome:
    text: str
    is_error: bool = False
    artifacts: list[Artifact] = field(default_factory=list)


def _truncate(text: str, limit: int = MAX_OUTPUT_CHARS) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n...(출력이 길어 {len(text) - limit:,}자 생략)"


def frame_to_text(df: pd.DataFrame, limit: int = MAX_ROWS_TO_MODEL) -> str:
    shown = df.head(limit)
    text = shown.to_markdown(index=False, floatfmt=",.4g") if len(shown.columns) else "(열 없음)"
    note = f"\n(전체 {len(df):,}행 중 {len(shown):,}행 표시)" if len(df) > limit else f"\n({len(df):,}행)"
    return text + note


class ToolExecutor:
    """도구 호출을 실제로 실행합니다. 파이썬 변수는 대화가 이어지는 동안 유지됩니다."""

    def __init__(self, catalog: Catalog, allow_python: bool = True) -> None:
        self.catalog = catalog
        self.allow_python = allow_python
        self._pending: list[Artifact] = []
        self.namespace: dict[str, Any] = {}
        self.reset_namespace()

    def reset_namespace(self) -> None:
        self.namespace = {
            "pd": pd, "np": np, "px": px, "go": go,
            "tables": {name: ds.frame for name, ds in self.catalog.datasets.items()},
            "sql": self._sql_frame,
            "show_chart": self._show_chart,
            "show_table": self._show_table,
        }

    def refresh_tables(self) -> None:
        self.namespace["tables"] = {name: ds.frame for name, ds in self.catalog.datasets.items()}

    # --- 파이썬 코드에서 쓰는 도우미 ---
    def _sql_frame(self, query: str) -> pd.DataFrame:
        self._check_sql(query)
        return self.catalog.con.execute(query).df()

    def _show_chart(self, fig, title: str = "차트") -> None:
        if not isinstance(fig, go.Figure):
            raise TypeError("show_chart에는 plotly Figure를 넘겨주세요 (px.bar(...) 등)")
        if not fig.layout.title.text:
            fig.update_layout(title=title)
        self._pending.append(Artifact("chart", title, fig))
        print(f"[차트 표시됨: {title}]")

    def _show_table(self, df, title: str = "표") -> None:
        frame = df if isinstance(df, pd.DataFrame) else pd.DataFrame(df)
        self._pending.append(Artifact("table", title, frame))
        print(f"[표 표시됨: {title}]")

    @staticmethod
    def _check_sql(query: str) -> None:
        if not READ_ONLY_SQL.match(query):
            raise ValueError("읽기 전용 SQL(SELECT, WITH, DESCRIBE, SUMMARIZE 등)만 실행할 수 있습니다.")
        if re.search(r"\b(COPY|ATTACH|INSTALL|LOAD|EXPORT|PRAGMA|SET)\b", query, re.IGNORECASE):
            raise ValueError("파일 쓰기·확장 설치·설정 변경 구문은 사용할 수 없습니다.")

    # --- 도구 실행 ---
    def execute(self, name: str, args: dict[str, Any]) -> ToolOutcome:
        handler = getattr(self, f"_tool_{name}", None)
        if handler is None:
            return ToolOutcome(f"알 수 없는 도구입니다: {name}", is_error=True)
        try:
            return handler(**args)
        except Exception as e:
            return ToolOutcome(f"{type(e).__name__}: {e}", is_error=True,
                               artifacts=[Artifact("error", f"{name} 실행 오류", str(e))])

    def _tool_list_datasets(self) -> ToolOutcome:
        lines = self.catalog.summary_lines()
        if not lines:
            return ToolOutcome("등록된 데이터셋이 없습니다. 사용자에게 data/ 폴더에 CSV·엑셀 파일을 넣거나 화면에서 업로드해 달라고 안내하세요.")
        text = "\n".join(lines)
        if self.catalog.load_errors:
            text += "\n\n읽지 못한 파일:\n" + "\n".join(f"- {k}: {v}" for k, v in self.catalog.load_errors.items())
        return ToolOutcome(text)

    def _tool_describe_dataset(self, table: str) -> ToolOutcome:
        if table not in self.catalog.datasets:
            names = ", ".join(self.catalog.datasets) or "(없음)"
            return ToolOutcome(f"'{table}' 테이블이 없습니다. 사용 가능한 테이블: {names}", is_error=True)
        return ToolOutcome(self.catalog.profile(table))

    def _tool_run_sql(self, query: str, title: str = "조회 결과") -> ToolOutcome:
        self._check_sql(query)
        df = self.catalog.con.execute(query).df()
        return ToolOutcome(frame_to_text(df), artifacts=[Artifact("table", title, df, detail=query)])

    def _tool_run_python(self, code: str, purpose: str = "") -> ToolOutcome:
        if not self.allow_python:
            return ToolOutcome("이 환경에서는 파이썬 실행이 꺼져 있습니다. run_sql을 사용하세요.", is_error=True)
        self._pending = []
        buf = io.StringIO()
        error = None
        with contextlib.redirect_stdout(buf):
            try:
                exec(compile(code, "<analysis>", "exec"), self.namespace)
            except Exception:
                error = traceback.format_exc(limit=-3)
        artifacts = [Artifact("code", purpose or "분석 코드", code)] + self._pending
        self._pending = []
        output = buf.getvalue().strip()
        if error:
            text = (output + "\n\n" if output else "") + "오류:\n" + error
            return ToolOutcome(_truncate(text), is_error=True,
                               artifacts=artifacts + [Artifact("error", "파이썬 실행 오류", error)])
        return ToolOutcome(_truncate(output or "(출력 없음 - 결과를 보려면 print()를 사용하세요)"), artifacts=artifacts)
