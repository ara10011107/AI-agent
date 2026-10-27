"""에이전트 루프: Claude가 도구를 호출하면 실행해 결과를 돌려주고, 답이 나올 때까지 반복합니다."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any, Callable

import anthropic

from .catalog import Catalog
from .prompts import SYSTEM_PROMPT
from .tools import TOOL_DEFINITIONS, Artifact, ToolExecutor

DEFAULT_MODEL = "claude-opus-5-5"
DEFAULT_EFFORT = "medium"
MAX_TOOL_ROUNDS = 20
FALLBACK_BETA = "server-side-fallback-2026-07-01"


@dataclass
class StepEvent:
    """화면에 진행 상황을 보여주기 위한 이벤트."""
    kind: str  # "tool_call" | "tool_result" | "notice"
    tool: str = ""
    args: dict[str, Any] = field(default_factory=dict)
    text: str = ""
    is_error: bool = False
    artifacts: list[Artifact] = field(default_factory=list)


@dataclass
class AgentAnswer:
    text: str
    artifacts: list[Artifact]
    steps: list[StepEvent]
    stop_reason: str
    usage: dict[str, int]
    model: str


class DataAgent:
    """대화 기록을 유지하는 데이터 분석 에이전트. 후속 질문도 같은 맥락에서 이어집니다."""

    def __init__(self, catalog: Catalog, client: anthropic.Anthropic | None = None,
                 model: str | None = None, effort: str | None = None,
                 allow_python: bool = True) -> None:
        self.catalog = catalog
        self.client = client or anthropic.Anthropic()
        self.model = model or os.getenv("AGENT_MODEL", DEFAULT_MODEL)
        self.effort = effort or os.getenv("AGENT_EFFORT", DEFAULT_EFFORT)
        self.executor = ToolExecutor(catalog, allow_python=allow_python)
        self.messages: list[dict[str, Any]] = []
        self._known_tables = set(catalog.datasets)

    def reset(self) -> None:
        self.messages = []
        self.executor.reset_namespace()
        self._known_tables = set(self.catalog.datasets)

    def _catalog_change_note(self) -> str:
        """대화 도중 데이터가 추가되면 시스템 프롬프트를 바꾸는 대신 다음 질문에 알림을 덧붙입니다."""
        current = set(self.catalog.datasets)
        added = sorted(current - self._known_tables)
        self._known_tables = current
        if not added or not self.messages:
            return ""
        self.executor.refresh_tables()
        return "\n\n[알림: 새 데이터셋이 추가되었습니다: " + ", ".join(added) + "]"

    def _create(self) -> Any:
        return self.client.beta.messages.create(
            model=self.model,
            max_tokens=16000,
            system=SYSTEM_PROMPT,
            tools=TOOL_DEFINITIONS,
            messages=self.messages,
            thinking={"type": "adaptive"},
            output_config={"effort": self.effort},
            cache_control={"type": "ephemeral"},
            # 안전 분류기가 요청을 거절하면 서버가 권장 모델로 자동 재시도합니다
            betas=[FALLBACK_BETA],
            fallbacks="default",
        )

    def ask(self, question: str, on_event: Callable[[StepEvent], None] | None = None) -> AgentAnswer:
        emit = on_event or (lambda e: None)
        note = self._catalog_change_note()
        if self.messages and self.messages[-1]["role"] == "user":
            # 이전 질문이 도구 결과에서 중단된 경우: 같은 user 차례에 질문을 덧붙입니다
            self.messages[-1]["content"].append({"type": "text", "text": question + note})
        else:
            self.messages.append({"role": "user", "content": question + note})

        artifacts: list[Artifact] = []
        steps: list[StepEvent] = []
        usage = {"input_tokens": 0, "output_tokens": 0, "cache_read_input_tokens": 0}
        response = None

        for _ in range(MAX_TOOL_ROUNDS):
            response = self._create()
            for key in usage:
                usage[key] += getattr(response.usage, key, 0) or 0
            self.messages.append({"role": "assistant", "content": response.content})

            if response.stop_reason == "pause_turn":
                continue
            if response.stop_reason != "tool_use":
                break

            results = []
            for block in response.content:
                if block.type != "tool_use":
                    continue
                args = block.input if isinstance(block.input, dict) else json.loads(block.input)
                call = StepEvent("tool_call", tool=block.name, args=args)
                steps.append(call)
                emit(call)
                outcome = self.executor.execute(block.name, args)
                done = StepEvent("tool_result", tool=block.name, text=outcome.text,
                                 is_error=outcome.is_error, artifacts=outcome.artifacts)
                steps.append(done)
                emit(done)
                artifacts.extend(outcome.artifacts)
                results.append({"type": "tool_result", "tool_use_id": block.id,
                                "content": outcome.text, "is_error": outcome.is_error})
            # 한 번에 요청된 도구 결과는 하나의 user 메시지로 함께 돌려줍니다
            self.messages.append({"role": "user", "content": results})
        else:
            notice = StepEvent("notice", text=f"도구 호출이 {MAX_TOOL_ROUNDS}회를 넘어 중단했습니다.")
            steps.append(notice)
            emit(notice)

        text = self._final_text(response)
        return AgentAnswer(text=text, artifacts=artifacts, steps=steps,
                           stop_reason=response.stop_reason if response else "none",
                           usage=usage, model=response.model if response else self.model)

    def _final_text(self, response: Any) -> str:
        if response is None:
            return "응답을 받지 못했습니다."
        if response.stop_reason == "refusal":
            return "이 질문에는 답변할 수 없습니다. 질문을 바꾸어 다시 시도해 주세요."
        text = "\n".join(b.text for b in response.content if b.type == "text").strip()
        if response.stop_reason == "max_tokens":
            text += "\n\n(답변이 너무 길어 중간에 끊겼습니다. '이어서 설명해줘'라고 요청해 보세요.)"
        if response.stop_reason == "tool_use":
            text += "\n\n(분석 단계가 너무 많아 중간에 멈췄습니다. 질문 범위를 좁혀 다시 요청해 주세요.)"
        return text or "분석을 마쳤지만 요약 문장을 받지 못했습니다. 위의 표와 차트를 참고해 주세요."
