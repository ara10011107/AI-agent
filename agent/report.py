"""분석 결과를 HTML 보고서와 실행 기록(JSON)으로 저장합니다.

실행 기록에는 질문, 답변, 실행한 SQL·파이썬 코드가 모두 남아 같은 결과를 다시 계산(재현)할 수 있습니다.
"""

from __future__ import annotations

import html
import json
from datetime import datetime
from pathlib import Path

import markdown
import pandas as pd

from .core import AgentAnswer

RUNS_DIR = Path(__file__).resolve().parent.parent / "runs"

_STYLE = """
body{font-family:'Pretendard','Apple SD Gothic Neo','Malgun Gothic',sans-serif;max-width:960px;margin:32px auto;padding:0 16px;line-height:1.65;color:#1f2933}
h1{font-size:1.6rem;border-bottom:3px solid #f28c28;padding-bottom:8px}
h2{font-size:1.2rem;margin-top:1.6em}
table{border-collapse:collapse;margin:8px 0;font-size:.9rem}
th,td{border:1px solid #d5dbe1;padding:4px 8px;text-align:right}
th{background:#f4f6f8}
pre{background:#f4f6f8;padding:12px;overflow-x:auto;font-size:.85rem}
.meta{color:#66788a;font-size:.85rem}
.question{background:#fff5eb;border-left:4px solid #f28c28;padding:10px 14px}
"""


def _frame_html(df: pd.DataFrame, limit: int = 200) -> str:
    note = f"<p class='meta'>전체 {len(df):,}행 중 {limit:,}행 표시</p>" if len(df) > limit else ""
    return df.head(limit).to_html(index=False, float_format=lambda v: f"{v:,.4g}", border=0) + note


def build_html_report(question: str, answer: AgentAnswer) -> str:
    parts = [
        "<!doctype html><html lang='ko'><head><meta charset='utf-8'>",
        "<meta name='viewport' content='width=device-width,initial-scale=1'>",
        "<title>탐라 데이터 비서 분석 보고서</title>",
        f"<style>{_STYLE}</style></head><body>",
        "<h1>탐라 데이터 비서 분석 보고서</h1>",
        f"<p class='meta'>작성 {datetime.now():%Y-%m-%d %H:%M} · 모델 {html.escape(answer.model)}</p>",
        f"<div class='question'><b>질문</b><br>{html.escape(question)}</div>",
        markdown.markdown(answer.text, extensions=["tables"]),
    ]
    visuals = [a for a in answer.artifacts if a.kind in ("chart", "table")]
    if visuals:
        parts.append("<h2>분석 결과 표·차트</h2>")
    plotly_loaded = False
    for a in visuals:
        parts.append(f"<h3>{html.escape(a.title)}</h3>")
        if a.kind == "chart":
            # plotly.js는 첫 차트에서 한 번만 CDN으로 불러옵니다
            parts.append(a.content.to_html(full_html=False, include_plotlyjs=False if plotly_loaded else "cdn"))
            plotly_loaded = True
        else:
            parts.append(_frame_html(a.content))
    code = [a for a in answer.artifacts if a.kind == "code" or (a.kind == "table" and a.detail)]
    if code:
        parts.append("<h2>부록: 재현용 SQL·코드</h2>")
        for a in code:
            body = a.detail if a.kind == "table" else a.content
            lang = "SQL" if a.kind == "table" else "Python"
            parts.append(f"<p><b>{lang}</b> · {html.escape(a.title)}</p><pre>{html.escape(body)}</pre>")
    parts.append("</body></html>")
    return "\n".join(parts)


def save_run(question: str, answer: AgentAnswer, runs_dir: Path = RUNS_DIR) -> Path:
    """runs/<시각>/ 폴더에 report.html과 run.json을 저장하고 폴더 경로를 돌려줍니다."""
    out = runs_dir / datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    out.mkdir(parents=True, exist_ok=True)
    (out / "report.html").write_text(build_html_report(question, answer), encoding="utf-8")
    record = {
        "question": question,
        "answer": answer.text,
        "model": answer.model,
        "stop_reason": answer.stop_reason,
        "usage": answer.usage,
        "steps": [
            {"kind": s.kind, "tool": s.tool, "args": s.args, "text": s.text, "is_error": s.is_error}
            for s in answer.steps
        ],
    }
    (out / "run.json").write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    return out
