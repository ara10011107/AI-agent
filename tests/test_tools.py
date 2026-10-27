"""API 키 없이 돌아가는 테스트: 데이터 적재, 도구 실행, 에이전트 루프(가짜 클라이언트)."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

from agent.catalog import Catalog, read_table_file, to_table_name
from agent.core import DataAgent
from agent.report import build_html_report
from agent.tools import ToolExecutor

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


@pytest.fixture()
def catalog() -> Catalog:
    cat = Catalog()
    cat.add_frame("카드소비", pd.DataFrame({
        "읍면동": ["애월읍", "애월읍", "성산읍"],
        "이용자구분": ["도민", "내국인관광객", "내국인관광객"],
        "이용금액": [1000, 3000, 5000],
    }))
    return cat


def test_cp949_csv_is_read(tmp_path: Path) -> None:
    path = tmp_path / "한글.csv"
    pd.DataFrame({"지역": ["제주시"], "값": [1]}).to_csv(path, index=False, encoding="cp949")
    df, enc = read_table_file(path)
    assert enc == "cp949"
    assert df.loc[0, "지역"] == "제주시"


def test_table_names_are_sql_friendly() -> None:
    assert to_table_name("2025 제주-카드(월별)", set()) == "t_2025_제주_카드_월별"
    assert to_table_name("a", {"a"}) == "a_2"


def test_sample_data_loads() -> None:
    cat = Catalog.from_directory(DATA_DIR)
    assert cat.load_errors == {}
    assert any(ds.is_sample for ds in cat.datasets.values())


def test_run_sql_returns_table_artifact(catalog: Catalog) -> None:
    ex = ToolExecutor(catalog)
    out = ex.execute("run_sql", {"query": 'SELECT "읍면동", SUM("이용금액") AS 합계 FROM 카드소비 GROUP BY 1 ORDER BY 2 DESC',
                                 "title": "읍면동별 합계"})
    assert not out.is_error
    assert "성산읍" in out.text
    assert out.artifacts[0].kind == "table"


@pytest.mark.parametrize("query", [
    "DROP TABLE 카드소비",
    "COPY (SELECT 1) TO 'x.csv'",
    "SELECT * FROM read_csv('/etc/hostname')",
])
def test_unsafe_sql_is_rejected(catalog: Catalog, query: str) -> None:
    out = ToolExecutor(catalog).execute("run_sql", {"query": query, "title": "x"})
    assert out.is_error


def test_run_python_collects_charts_and_keeps_state(catalog: Catalog) -> None:
    ex = ToolExecutor(catalog)
    out = ex.execute("run_python", {"purpose": "차트", "code": (
        "df = sql('SELECT \"이용자구분\", SUM(\"이용금액\") AS 금액 FROM 카드소비 GROUP BY 1')\n"
        "show_chart(px.bar(df, x='이용자구분', y='금액'), '이용자별 금액')\n"
        "total = int(df['금액'].sum())\nprint(total)"
    )})
    assert not out.is_error, out.text
    assert "9000" in out.text
    assert [a.kind for a in out.artifacts] == ["code", "chart"]
    again = ex.execute("run_python", {"purpose": "상태 유지", "code": "print(total * 2)"})
    assert "18000" in again.text


def test_run_python_error_is_reported(catalog: Catalog) -> None:
    out = ToolExecutor(catalog).execute("run_python", {"purpose": "오류", "code": "1/0"})
    assert out.is_error and "ZeroDivisionError" in out.text


class FakeMessages:
    """도구 호출 1회 후 답변하는 가짜 Claude 응답."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        usage = SimpleNamespace(input_tokens=10, output_tokens=5, cache_read_input_tokens=0)
        if len(self.calls) == 1:
            block = SimpleNamespace(type="tool_use", id="tu_1", name="run_sql",
                                    input={"query": 'SELECT SUM("이용금액") AS 합계 FROM 카드소비', "title": "합계"})
            return SimpleNamespace(content=[block], stop_reason="tool_use", usage=usage, model="fake")
        text = SimpleNamespace(type="text", text="## 한눈에 보는 결론\n- 합계는 9,000원입니다.")
        return SimpleNamespace(content=[text], stop_reason="end_turn", usage=usage, model="fake")


def test_agent_loop_runs_tools_and_answers(catalog: Catalog) -> None:
    fake = FakeMessages()
    client = SimpleNamespace(beta=SimpleNamespace(messages=fake))
    agent = DataAgent(catalog, client=client, model="fake")
    answer = agent.ask("전체 합계는?")
    assert "9,000" in answer.text
    tool_result = agent.messages[2]["content"][0]  # user 질문, assistant 도구 호출, user 도구 결과 순
    assert tool_result["type"] == "tool_result" and "9,000" in tool_result["content"]
    assert fake.calls[0]["fallbacks"] == "default"
    html = build_html_report("전체 합계는?", answer)
    assert "부록: 재현용 SQL" in html
