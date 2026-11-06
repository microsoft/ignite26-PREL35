"""Typed Decision-1 inference against the existing Foundry account; no GPT or provisioning."""
import json
import math
import random
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

CRITERIA = {
    "escalate": "Actual evidence establishes a defined financial-crime red flag. Missing information alone is insufficient.",
    "close_false_positive": "No established red flag; the profile or permitted independent evidence explains the activity, or screening is a false positive.",
    "request_info": "No established red flag, but the activity is unexplained or necessary KYC/evidence is incomplete.",
}


def read_answer(answer, labels=CRITERIA):
    if not isinstance(answer, dict) or answer.get("type") != "choice" or answer.get("choice") not in labels:
        raise ValueError("Invalid Decision-1 choice.")
    probabilities = answer.get("probabilities")
    if not isinstance(probabilities, dict) or set(probabilities) != set(labels):
        raise ValueError("Invalid choice probabilities.")
    values = [answer.get("confidence"), *probabilities.values()]
    if not all(type(v) in (int, float) and math.isfinite(v) and 0 <= v <= 1 for v in values):
        raise ValueError("Invalid numerical decision values.")
    if abs(sum(probabilities.values()) - 1) > .01:
        raise ValueError("Choice probabilities do not sum to one.")
    return {"disposition": answer["choice"], "confidence": answer["confidence"], "probabilities": probabilities}


class DecisionClient:
    def __init__(self, endpoint, deployment, instructions, token, timeout=30, retries=8):
        self.endpoint = endpoint.rstrip("/") + "/providers/microsoft/v1/systemone"
        self.deployment, self.instructions, self.token, self.timeout = deployment, instructions, token, timeout
        self.retries = retries

    def question(self, selector=None):
        instructions = self.instructions
        if selector is not None:
            instructions += f"\n\nAssess ONLY state.cases[{selector}]. Other cases are not evidence for this answer."
        return {"type": "choice", "instructions": instructions, "criteria": CRITERIA}

    def _post(self, body):
        request = urllib.request.Request(
            self.endpoint, json.dumps(body, ensure_ascii=False).encode("utf-8"),
            {"Authorization": "Bearer " + self.token, "Content-Type": "application/json"},
        )
        for attempt in range(self.retries + 1):
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    payload = json.loads(response.read())
                break
            except urllib.error.HTTPError as error:
                if error.code != 429 or attempt == self.retries:
                    raise
                # Rate limiting is bursty; jittered backoff lets a parallel batch drain.
                time.sleep(min(0.25 * 2 ** attempt, 4) * (0.5 + random.random()))
        if not isinstance(payload, dict) or payload.get("model") != "microsoft-decision-1":
            raise ValueError("Unexpected decision model.")
        return payload

    def choose(self, state, criteria, name="choice"):
        """One typed choice over any state: the instructions, the labels and their probabilities."""
        question = {"type": "choice", "instructions": self.instructions, "criteria": criteria}
        payload = self._post({"model": self.deployment, "state": state, "questions": {name: question}})
        if not isinstance(payload.get("answers"), dict) or set(payload["answers"]) != {name}:
            raise ValueError("Returned question names differ from the request.")
        parsed = read_answer(payload["answers"][name], criteria)
        return {"choice": parsed.pop("disposition"), **parsed, "usage": payload.get("usage")}

    def classify(self, cases, *, workers=8, shared=False):
        if not cases or len({c["alert"]["id"] for c in cases}) != len(cases):
            raise ValueError("Supply cases with unique alert IDs.")
        if not 1 <= workers <= 16:
            raise ValueError("workers must be 1..16.")
        started = time.perf_counter()

        def request_group(group):
            indices, state, questions = group
            begin = time.perf_counter()
            try:
                payload = self._post({"model": self.deployment, "state": state, "questions": questions})
                if not isinstance(payload.get("answers"), dict) or set(payload["answers"]) != set(questions):
                    raise ValueError("Returned question names differ from the request.")
                rows = [{"alert_id": cases[i]["alert"]["id"], "status": "ok",
                         **read_answer(payload["answers"][name])} for i, name in indices]
                request_info = {"usage": payload.get("usage")}
            except (urllib.error.URLError, TimeoutError, ValueError) as error:
                code = f"http_{error.code}" if isinstance(error, urllib.error.HTTPError) else type(error).__name__
                rows = [{"alert_id": cases[i]["alert"]["id"], "status": "unavailable",
                         "disposition": None, "error": code} for i, _ in indices]
                request_info = {"error": code}
            return rows, {**request_info, "seconds": time.perf_counter() - begin}

        if shared:
            names = [c["alert"]["id"] for c in cases]
            groups = [(list(enumerate(names)), {"cases": cases},
                       {name: self.question(i) for i, name in enumerate(names)})]
        else:
            groups = [([(i, "disposition")], case, {"disposition": self.question()}) for i, case in enumerate(cases)]
        with ThreadPoolExecutor(max_workers=min(workers, len(groups))) as pool:
            responses = list(pool.map(request_group, groups))
        return {
            "mode": "shared" if shared else "isolated",
            "seconds": time.perf_counter() - started,
            "rows": [row for rows, _ in responses for row in rows],
            "requests": [request_info for _, request_info in responses],
        }
