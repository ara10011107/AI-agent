"""탐라 데이터 비서 - 터미널 실행

예시:
  python cli.py "관광객 소비가 가장 많은 읍면동은?"
  python cli.py            # 대화 모드 (빈 줄 또는 'exit' 입력 시 종료)
  python cli.py --no-samples --data-dir 내데이터폴더 "질문"
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from dotenv import load_dotenv

from agent.catalog import Catalog
from agent.core import DataAgent, StepEvent
from agent.report import save_run


def print_event(event: StepEvent) -> None:
    if event.kind == "tool_call":
        detail = event.args.get("title") or event.args.get("purpose") or event.args.get("table") or ""
        print(f"  - {event.tool} {detail}", file=sys.stderr)
    elif event.kind == "tool_result" and event.is_error:
        print(f"    (오류: {event.text.splitlines()[-1] if event.text else ''})", file=sys.stderr)
    elif event.kind == "notice":
        print(f"  ! {event.text}", file=sys.stderr)


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(description="제주 공공데이터 분석 AI 에이전트")
    parser.add_argument("question", nargs="?", help="질문 (생략하면 대화 모드)")
    parser.add_argument("--data-dir", type=Path, default=Path(__file__).resolve().parent / "data")
    parser.add_argument("--no-samples", action="store_true", help="가상 샘플 데이터 제외")
    parser.add_argument("--effort", choices=["low", "medium", "high", "xhigh", "max"], help="분석 깊이")
    args = parser.parse_args()

    catalog = Catalog.from_directory(args.data_dir, include_samples=not args.no_samples)
    print("사용 데이터:\n" + ("\n".join(catalog.summary_lines()) or "(없음)"), file=sys.stderr)
    agent = DataAgent(catalog, effort=args.effort)

    def ask(q: str) -> None:
        answer = agent.ask(q, on_event=print_event)
        print("\n" + answer.text)
        run_dir = save_run(q, answer)
        print(f"\n[보고서: {run_dir / 'report.html'}]", file=sys.stderr)

    if args.question:
        ask(args.question)
        return
    while (q := input("\n질문> ").strip()) and q.lower() not in ("exit", "quit", "종료"):
        ask(q)


if __name__ == "__main__":
    main()
