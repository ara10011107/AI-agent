"""탐라 데이터 비서 - 웹 화면 (Streamlit)

실행: streamlit run app.py
"""

from __future__ import annotations

import os
from pathlib import Path

import anthropic
import streamlit as st
from dotenv import load_dotenv

from agent.catalog import Catalog
from agent.core import DataAgent, StepEvent
from agent.report import build_html_report, save_run

load_dotenv()

DATA_DIR = Path(__file__).resolve().parent / "data"
UPLOAD_DIR = DATA_DIR / "user"
TOOL_LABELS = {
    "list_datasets": "데이터 목록 확인",
    "describe_dataset": "데이터 구조 살펴보기",
    "run_sql": "데이터 집계(SQL)",
    "run_python": "통계 계산·차트 작성",
}
EXAMPLE_QUESTIONS = [
    "관광객 소비가 가장 많은 읍면동 5곳과 주로 어떤 업종에서 쓰는지 알려줘",
    "애월읍에서 카페를 하려는데, 요일별·이용자별로 매출이 어떻게 달라?",
    "외국인 관광객 소비가 특히 늘어나는 달과 업종은?",
    "비 오는 날 입도객 수가 줄어드는지 확인해줘",
]

st.set_page_config(page_title="탐라 데이터 비서", layout="wide")


def render_artifacts(artifacts, key_prefix: str) -> None:
    for i, a in enumerate(artifacts):
        if a.kind == "chart":
            st.plotly_chart(a.content, width="stretch", key=f"{key_prefix}-chart-{i}")
        elif a.kind == "table":
            st.markdown(f"**{a.title}**")
            st.dataframe(a.content, width="stretch", hide_index=True)
    code = [a for a in artifacts if a.kind == "code" or (a.kind == "table" and a.detail)]
    if code:
        with st.expander("실행한 SQL·코드 보기 (재현용)"):
            for a in code:
                st.caption(a.title)
                st.code(a.detail if a.kind == "table" else a.content,
                        language="sql" if a.kind == "table" else "python")


def describe_step(event: StepEvent) -> str:
    label = TOOL_LABELS.get(event.tool, event.tool)
    if event.tool == "describe_dataset":
        return f"{label}: `{event.args.get('table', '')}`"
    if event.tool == "run_sql":
        return f"{label}: {event.args.get('title', '')}"
    if event.tool == "run_python":
        return f"{label}: {event.args.get('purpose', '')}"
    return label


def get_catalog(include_samples: bool) -> Catalog:
    key = ("catalog", include_samples)
    if st.session_state.get("catalog_key") != key:
        st.session_state.catalog = Catalog.from_directory(DATA_DIR, include_samples=include_samples)
        st.session_state.catalog_key = key
        st.session_state.pop("agent", None)
        st.session_state.history = []
    return st.session_state.catalog


def get_agent(catalog: Catalog, api_key: str) -> DataAgent | None:
    if not api_key and not os.getenv("ANTHROPIC_API_KEY"):
        return None
    if "agent" not in st.session_state:
        client = anthropic.Anthropic(api_key=api_key) if api_key else anthropic.Anthropic()
        st.session_state.agent = DataAgent(catalog, client=client)
    return st.session_state.agent


# ---------------- 사이드바 ----------------
with st.sidebar:
    st.header("설정")
    api_key = ""
    if not os.getenv("ANTHROPIC_API_KEY"):
        api_key = st.text_input("Claude API 키", type="password",
                                help="console.anthropic.com에서 발급받은 키. 이 컴퓨터 밖으로 저장되지 않습니다.")
    include_samples = st.toggle("데모용 가상 샘플 데이터 포함", value=True,
                                help="실제 제주 데이터를 넣었다면 끄세요.")
    catalog = get_catalog(include_samples)

    st.subheader("데이터 추가")
    uploads = st.file_uploader("제주빅데이터센터·제주데이터허브에서 받은 CSV/엑셀",
                               type=["csv", "xlsx", "xls", "txt"], accept_multiple_files=True)
    for up in uploads or []:
        if up.name in st.session_state.setdefault("uploaded", set()):
            continue
        UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
        path = UPLOAD_DIR / up.name
        path.write_bytes(up.getvalue())
        try:
            ds = catalog.add_file(path)
            st.success(f"`{ds.table}` 추가 ({len(ds.frame):,}행)")
        except Exception as e:
            st.error(f"{up.name}을(를) 읽지 못했습니다: {e}")
        st.session_state.uploaded.add(up.name)

    st.subheader("사용 중인 데이터")
    if not catalog.datasets:
        st.info("데이터가 없습니다. 파일을 업로드하거나 data/ 폴더에 넣어 주세요.")
    for name, ds in catalog.datasets.items():
        with st.expander(("[샘플] " if ds.is_sample else "") + ds.title):
            st.caption(f"테이블 `{name}` · {len(ds.frame):,}행 × {ds.frame.shape[1]}열")
            st.dataframe(ds.frame.head(20), width="stretch", hide_index=True)
    for fname, err in catalog.load_errors.items():
        st.warning(f"{fname}: {err}")

    if st.button("새 대화 시작", width="stretch"):
        st.session_state.history = []
        if "agent" in st.session_state:
            st.session_state.agent.reset()
        st.rerun()

# ---------------- 본문 ----------------
st.title("탐라 데이터 비서")
st.caption("제주 공공데이터에 대해 평소 말투로 물어보세요. 데이터를 찾아 계산하고, 표·차트와 함께 쉽게 설명해 드립니다.")

agent = get_agent(catalog, api_key)
history = st.session_state.setdefault("history", [])

if not history:
    st.markdown("**이런 질문을 해보세요**")
    cols = st.columns(2)
    for i, q in enumerate(EXAMPLE_QUESTIONS):
        if cols[i % 2].button(q, key=f"ex-{i}", width="stretch"):
            st.session_state.pending_question = q
            st.rerun()

for idx, turn in enumerate(history):
    with st.chat_message(turn["role"]):
        st.markdown(turn["text"])
        if turn["role"] == "assistant":
            render_artifacts(turn["artifacts"], key_prefix=f"h{idx}")
            st.download_button("보고서 내려받기 (HTML)", turn["report_html"],
                               file_name=f"탐라데이터비서_보고서_{idx}.html", mime="text/html",
                               key=f"dl-{idx}")

question = st.chat_input("예: 서귀포시에서 주말 관광객 소비가 많은 업종은?")
question = question or st.session_state.pop("pending_question", None)

if question:
    if agent is None:
        st.error("왼쪽 설정에 Claude API 키를 입력해 주세요.")
        st.stop()
    history.append({"role": "user", "text": question})
    with st.chat_message("user"):
        st.markdown(question)
    with st.chat_message("assistant"):
        with st.status("데이터를 살펴보는 중...", expanded=True) as status:
            def on_event(event: StepEvent) -> None:
                if event.kind == "tool_call":
                    status.write(describe_step(event))
                elif event.kind == "tool_result" and event.is_error:
                    status.write("　↳ 오류가 나서 방법을 바꿔 다시 시도합니다")
                elif event.kind == "notice":
                    status.write(event.text)
            try:
                answer = agent.ask(question, on_event=on_event)
            except anthropic.AuthenticationError:
                status.update(label="API 키가 올바르지 않습니다", state="error")
                st.stop()
            except anthropic.RateLimitError:
                status.update(label="요청이 많아 잠시 제한되었습니다. 1분 뒤 다시 시도해 주세요.", state="error")
                st.stop()
            except anthropic.APIConnectionError:
                status.update(label="인터넷 연결을 확인해 주세요.", state="error")
                st.stop()
            except anthropic.APIStatusError as e:
                status.update(label=f"API 오류 ({e.status_code}): {e.message}", state="error")
                st.stop()
            status.update(label=f"분석 완료 · 단계 {sum(s.kind == 'tool_call' for s in answer.steps)}개",
                          state="complete", expanded=False)
        st.markdown(answer.text)
        render_artifacts(answer.artifacts, key_prefix="new")
        report_html = build_html_report(question, answer)
        run_dir = save_run(question, answer)
        st.download_button("보고서 내려받기 (HTML)", report_html,
                           file_name="탐라데이터비서_보고서.html", mime="text/html", key="dl-new")
        st.caption(f"실행 기록 저장: {run_dir.relative_to(Path(__file__).resolve().parent)}")
    history.append({"role": "assistant", "text": answer.text, "artifacts": answer.artifacts,
                    "report_html": report_html})
