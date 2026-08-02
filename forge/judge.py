"""
Process Reward Model (PRM) Judge — OpenClaw-RL (arXiv:2603.10165).

Scores each completed step on a 0-10 scale using a fast, cheap model.
Runs asynchronously in a background thread so it doesn't block execution.
Judge scores feed into TrustLedger and Vault for richer calibration.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass
from queue import Queue, Empty

from forge.config import JUDGE_MODEL, JUDGE_TIMEOUT_SECONDS, XAI_API_KEY
from forge.models import PlanStep, StepResult
from forge.providers import detect_provider, calculate_cost

log = logging.getLogger("forge.judge")

JUDGE_PROMPT_TEMPLATE = """You are a Process Reward Model (PRM) judge evaluating an AI agent's work.

Task goal: {task_goal}
Step {step_number}: {step_title}
Description: {step_description}

Actual result:
- Status: {status}
- Output (truncated): {output}
- Tools used: {tools_used}
- Latency: {latency}s
- Errors: {error}

Score this step 0-10 where:
  0-2 = Failed, wrong approach
  3-4 = Partially complete, significant issues
  5-6 = Adequate, minor issues
  7-8 = Good, meets expectations
  9-10 = Excellent, efficient and thorough

Respond with JSON only: {{"score": N, "rationale": "one sentence"}}"""


@dataclass
class JudgeScore:
    """Result of a PRM judge evaluation."""
    step_number: int
    score: float          # 0.0 to 10.0
    rationale: str = ""
    judge_model: str = ""
    latency_seconds: float = 0.0
    cost_usd: float = 0.0


class StepJudge:
    """Synchronous single-step scorer using a fast LLM."""

    def __init__(self, model: str = "", timeout: float = 0.0):
        self.model = model or JUDGE_MODEL
        self.timeout = timeout or JUDGE_TIMEOUT_SECONDS

    def judge_step(self, step: PlanStep, result: StepResult, task_goal: str) -> JudgeScore:
        """Score a completed step. Returns neutral 5.0 on any failure."""
        start = time.time()
        try:
            prompt = JUDGE_PROMPT_TEMPLATE.format(
                task_goal=task_goal[:300],
                step_number=step.step_number,
                step_title=step.title,
                step_description=step.description[:300],
                status=result.status,
                output=result.output[:500],
                tools_used=", ".join(result.tools_used) or "none",
                latency=f"{result.latency_seconds:.1f}",
                error=result.error or "none",
            )

            response_text = self._call_llm(prompt)
            score_data = self._parse_response(response_text)
            latency = time.time() - start

            return JudgeScore(
                step_number=step.step_number,
                score=score_data["score"],
                rationale=score_data["rationale"],
                judge_model=self.model,
                latency_seconds=round(latency, 2),
            )

        except Exception as e:
            log.warning("Judge failed for step %d: %s", step.step_number, e)
            return JudgeScore(
                step_number=step.step_number,
                score=5.0,
                rationale=f"Judge error: {e}",
                judge_model=self.model,
                latency_seconds=round(time.time() - start, 2),
            )

    def _call_llm(self, prompt: str) -> str:
        """Call the judge model and return the response text."""
        provider = detect_provider(self.model)

        if provider == "xai":
            from xai_sdk import Client
            from xai_sdk.chat import user
            client = Client(api_key=XAI_API_KEY)
            chat = client.chat.create(model=self.model)
            chat.append(user(prompt))
            response_text = ""
            for _resp, chunk in chat.stream():
                if chunk.content:
                    response_text += chunk.content
            return response_text

        elif provider == "anthropic":
            from forge.config import ANTHROPIC_API_KEY
            import anthropic
            client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
            msg = client.messages.create(
                model=self.model,
                max_tokens=100,
                messages=[{"role": "user", "content": prompt}],
            )
            from forge.providers import anthropic_message_text
            return anthropic_message_text(msg.content)

        elif provider == "openai":
            from forge.config import OPENAI_API_KEY
            from openai import OpenAI
            client = OpenAI(api_key=OPENAI_API_KEY)
            resp = client.chat.completions.create(
                model=self.model,
                max_tokens=100,
                messages=[{"role": "user", "content": prompt}],
            )
            return resp.choices[0].message.content or "" if resp.choices else ""

        else:
            raise ValueError(f"Unsupported provider for judge: {provider}")

    @staticmethod
    def _parse_response(text: str) -> dict:
        """Parse JSON score from judge response."""
        # Try direct JSON parse
        text = text.strip()
        # Handle markdown code blocks
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
            text = text.strip()

        data = json.loads(text)
        score = float(data.get("score", 5.0))
        score = max(0.0, min(10.0, score))
        rationale = str(data.get("rationale", ""))[:200]
        return {"score": score, "rationale": rationale}


class BackgroundJudge:
    """Async wrapper — judges steps in a background daemon thread."""

    def __init__(self, model: str = ""):
        self._judge = StepJudge(model=model)
        self._work_queue: Queue = Queue()
        self._results_queue: Queue = Queue()
        self._stop_event = threading.Event()
        self._worker = threading.Thread(target=self._worker_loop, daemon=True)
        self._worker.start()
        log.info("Background judge started (model=%s)", self._judge.model)

    def _worker_loop(self):
        """Consume work items and produce judge scores."""
        while not self._stop_event.is_set():
            try:
                item = self._work_queue.get(timeout=0.5)
            except Empty:
                continue

            if item is None:  # shutdown sentinel
                self._work_queue.task_done()
                break

            step, result, task_goal = item
            score = self._judge.judge_step(step, result, task_goal)
            self._results_queue.put(score)
            self._work_queue.task_done()
            log.info("Judged step %d: score=%.1f (%s)",
                     score.step_number, score.score, score.rationale[:60])

    def submit(self, step: PlanStep, result: StepResult, task_goal: str):
        """Non-blocking: enqueue a step for background judging."""
        self._work_queue.put((step, result, task_goal))

    def collect(self) -> list[JudgeScore]:
        """Drain all available results. Waits up to 5s for pending work."""
        # join() blocks until task_done() has been called for every enqueued item,
        # meaning the worker has finished scoring AND published the result.
        done = threading.Event()

        def _wait_for_join():
            self._work_queue.join()
            done.set()

        waiter = threading.Thread(target=_wait_for_join, daemon=True)
        waiter.start()
        waiter.join(timeout=5.0)

        scores = []
        while True:
            try:
                scores.append(self._results_queue.get_nowait())
            except Empty:
                break
        return scores

    def shutdown(self):
        """Signal worker to stop and join thread."""
        self._stop_event.set()
        self._work_queue.put(None)  # sentinel
        self._worker.join(timeout=5.0)
        log.info("Background judge shut down")
