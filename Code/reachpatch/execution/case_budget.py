"""One operational budget for compiler, recovery, generation and execution."""
from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass, field
from functools import wraps
import hashlib
import json
import time
from typing import Any


class CaseBudgetExhausted(RuntimeError):
    """A global resource bound, not a target failure."""


@dataclass
class CaseBudget:
    wall_seconds: float
    max_model_calls: int = 160
    max_tokens: int = 1_000_000
    execution_seconds: float = 3600.0
    final_validation_reserve: float = 30.0
    started: float = field(default_factory=time.monotonic)
    model_calls: int = 0
    tokens: int = 0
    execution_used: float = 0.0
    events: list[dict[str, Any]] = field(default_factory=list)
    execution_cache: dict[str, Any] = field(default_factory=dict, repr=False)
    model_request_counts: dict[str, int] = field(default_factory=dict, repr=False)
    model_action_counts: dict[str, int] = field(default_factory=dict, repr=False)
    state: Any = field(default=None, repr=False)

    @property
    def remaining_wall(self) -> float:
        return max(0.0, self.wall_seconds - (time.monotonic() - self.started))

    def execution_allowance(self, requested: float) -> float:
        allowed = min(requested, self.remaining_wall, max(0.0, self.execution_seconds - self.execution_used))
        if allowed <= 0:
            raise CaseBudgetExhausted("EXECUTION_OR_WALL_BUDGET")
        return allowed

    def summary(self) -> dict[str, Any]:
        return {"model_calls": self.model_calls, "tokens": self.tokens,
                "usage": {key: sum(event.get("usage", {}).get(key, 0) or 0 for event in self.events)
                          for key in ("prompt_tokens", "completion_tokens", "total_tokens", "prompt_cache_hit_tokens", "prompt_cache_miss_tokens")},
                "execution_seconds": self.execution_used, "remaining_wall_seconds": self.remaining_wall,
                "max_model_calls": self.max_model_calls, "max_tokens": self.max_tokens,
                "events": self.events}


active_case_budget: ContextVar[CaseBudget | None] = ContextVar("reachpatch_case_budget", default=None)


def charged_execution(function: Any) -> Any:
    @wraps(function)
    def execute(*args: Any, **kwargs: Any) -> Any:
        budget = active_case_budget.get()
        if budget is None:
            return function(*args, **kwargs)
        kwargs["timeout_seconds"] = budget.execution_allowance(float(kwargs.get("timeout_seconds", 60.0)))
        started = time.monotonic()
        try:
            return function(*args, **kwargs)
        finally:
            elapsed = time.monotonic() - started
            budget.execution_used += elapsed
            budget.events.append({"kind": "EXECUTION", "seconds": elapsed})
    return execute


class BudgetedTransport:
    def __init__(self, transport: Any, budget: CaseBudget):
        self.transport, self.budget = transport, budget

    def complete(self, messages: Any, **kwargs: Any) -> Any:
        budget = self.budget
        if budget.model_calls >= budget.max_model_calls or budget.remaining_wall <= budget.final_validation_reserve:
            raise CaseBudgetExhausted("MODEL_CALL_OR_VALIDATION_RESERVE")
        # UTF-8 byte count is a conservative input reservation, not a claim of
        # exact tokenization. Include tool schemas in the reservation.
        input_bound = len(json.dumps((messages, kwargs.get("tools", ())), ensure_ascii=False).encode("utf-8"))
        stage = str(getattr(budget.state, "phase", "EVIDENCE_RECOVERY"))
        request_payload = json.dumps(
            (stage, messages, kwargs.get("tools", ())),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
        request_fingerprint = hashlib.sha256(request_payload).hexdigest()
        prior_request_count = budget.model_request_counts.get(request_fingerprint, 0)
        budget.model_request_counts[request_fingerprint] = prior_request_count + 1
        # Reserve the last 20% for post-generation evidence and repair. This
        # is a request admission bound, not a measurement of consumed tokens.
        reserve = int(budget.max_tokens * .2) if stage == "INITIAL_GENERATION" else 0
        available = budget.max_tokens - budget.tokens - reserve
        output_limit = min(int(kwargs.get("max_tokens", 4096)), available - input_bound)
        if output_limit <= 0:
            budget.events.append({"kind": "MODEL_REJECTED", "stage": stage,
                "reason": "TOKEN_BUDGET", "input_reservation": input_bound,
                "remaining_tokens": budget.max_tokens - budget.tokens,
                "protected_tokens": reserve})
            raise CaseBudgetExhausted("TOKEN_BUDGET")
        kwargs["max_tokens"] = output_limit
        kwargs["timeout_seconds"] = min(float(kwargs.get("timeout_seconds", 120.0)),
                                         budget.remaining_wall - budget.final_validation_reserve)
        budget.model_calls += 1
        reserved = input_bound + output_limit
        budget.tokens += reserved
        started = time.monotonic()
        try:
            response = self.transport.complete(messages, **kwargs)
        except BaseException:
            budget.events.append({"kind": "MODEL_ERROR", "stage": stage,
                                  "charged_tokens": reserved, "usage_reported": False})
            raise
        usage = response.get("_usage", response.get("usage", {})) or {}
        actual = usage.get("total_tokens")
        charged = int(actual) if isinstance(actual, (int, float)) and actual >= 0 else reserved
        budget.tokens += charged - reserved
        tool_names: list[str] = []
        novel_tool_actions = 0
        duplicate_tool_actions = 0
        for call in response.get("tool_calls", ()) or ():
            function = call.get("function", {}) if isinstance(call, dict) else {}
            name = str(function.get("name", ""))
            arguments = function.get("arguments", "")
            tool_names.append(name)
            action_payload = json.dumps(
                (stage, name, arguments), ensure_ascii=False, sort_keys=True,
                separators=(",", ":"), default=str,
            ).encode("utf-8")
            action_fingerprint = hashlib.sha256(action_payload).hexdigest()
            prior_action_count = budget.model_action_counts.get(action_fingerprint, 0)
            budget.model_action_counts[action_fingerprint] = prior_action_count + 1
            if prior_action_count:
                duplicate_tool_actions += 1
            else:
                novel_tool_actions += 1
        content = str(response.get("content", "") or "").strip()
        budget.events.append({
            "kind": "MODEL",
            "charged_tokens": charged,
            "stage": stage,
            "usage": usage,
            "input_reservation": input_bound,
            "usage_reported": actual is not None,
            "seconds": time.monotonic() - started,
            # Store only hashes and tool names: enough to audit repeated work
            # without copying prompts, source, issue text, or tool arguments.
            "request_fingerprint": request_fingerprint,
            "exact_request_duplicate": bool(prior_request_count),
            "tool_call_count": len(tool_names),
            "tool_names": tool_names,
            "novel_tool_action_count": novel_tool_actions,
            "duplicate_tool_action_count": duplicate_tool_actions,
            "has_text_response": bool(content),
            "response_content_hash": (
                hashlib.sha256(content.encode("utf-8")).hexdigest()
                if content else None
            ),
        })
        return response
