"""Decision-1 reviewer: a verdict and a confidence for a triage recommendation, with no issue list and no GPT."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import time
import urllib.error

from src.decision1_client import CRITERIA, DecisionClient

PROMPTS = Path(__file__).resolve().parent / "prompts"
RULES_START = "## Evidence boundaries"

VERDICTS = {
    "agree": "The triage rules select the recommended disposition, and nothing the recommendation states is "
             "contradicted by the case records.",
    "disagree": "The triage rules select a different disposition, or the recommendation depends on a fact the case "
                "records do not support.",
}


def reviewer_instructions():
    """The reviewer rubric followed by the triage rules, which stay defined once, in decision1.md."""
    triage = (PROMPTS / "decision1.md").read_text(encoding="utf-8")
    if RULES_START not in triage:
        raise ValueError(f"decision1.md must contain the section '{RULES_START}'.")
    review = (PROMPTS / "decision1_reviewer.md").read_text(encoding="utf-8")
    return review + triage[triage.index(RULES_START):]


class Decision1Reviewer:
    def __init__(self, endpoint, deployment, token, instructions=None, **client_options):
        self.instructions = instructions or reviewer_instructions()
        self.client = DecisionClient(endpoint, deployment, self.instructions, token, **client_options)

    def review(self, case, recommendation):
        """Returns the verdict and its confidence. A service failure has no verdict; it never defaults to agree."""
        alert_id = case["alert"]["id"]
        if recommendation.get("disposition") not in CRITERIA:
            raise ValueError("The recommendation needs a disposition from the triage labels.")
        # The triage confidence is withheld: the reviewer judges the case, not how sure triage was.
        state = {"case": case, "triage_recommendation": {
            key: recommendation[key] for key in ("disposition", "reasons") if key in recommendation}}
        begin = time.perf_counter()
        try:
            answer = self.client.choose(state, VERDICTS, name="verdict")
        except (urllib.error.URLError, TimeoutError, ValueError) as error:
            code = f"http_{error.code}" if isinstance(error, urllib.error.HTTPError) else type(error).__name__
            return {"alert_id": alert_id, "status": "unavailable", "verdict": None, "error": code,
                    "seconds": time.perf_counter() - begin}
        return {"alert_id": alert_id, "status": "ok", "verdict": answer["choice"],
                "confidence": answer["confidence"], "probabilities": answer["probabilities"],
                "usage": answer["usage"], "seconds": time.perf_counter() - begin}

    def review_many(self, items, *, workers=8):
        """Independent reviews of (case, recommendation) pairs, in the order given."""
        if not 1 <= workers <= 16:
            raise ValueError("workers must be 1..16.")
        with ThreadPoolExecutor(max_workers=min(workers, max(len(items), 1))) as pool:
            return list(pool.map(lambda item: self.review(*item), items))
