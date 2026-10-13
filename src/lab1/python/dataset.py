"""The reviewed evaluation dataset, the case files, and recorded answers for replay. Mirrors Dataset.cs.

The dataset's expected answers are only used for scoring; they're never sent to the agents or the judge.
"""
import json

from checks import DISPOSITIONS, text, valid_ids


def load_json(path):
    """Reads a JSON file, with or without a byte-order mark."""
    with open(path, encoding="utf-8-sig") as file:
        return json.load(file)


# Item IDs name each agent answer in the Evidence Pack, in replay files, and in telemetry.
def triage_item(alert_id):
    return f"triage/{alert_id}"


def workflow_review_item(alert_id):
    return f"reviewer/workflow-{alert_id}"


def challenge_item(challenge_id):
    return f"reviewer/{challenge_id}"


def item_ids(dataset):
    """Every item ID, in the order the runner evaluates them."""
    for case in dataset["cases"]:
        yield triage_item(case["alert_id"])
        yield workflow_review_item(case["alert_id"])
    for challenge in dataset["reviewer_challenges"]:
        yield challenge_item(challenge["id"])


def load_cases(path):
    """Case files by alert ID. Each includes the bank's screening lists, as the agents receive it."""
    data = load_json(path)
    return {case["alert"]["id"]: {**case, "screening_lists": data["screening_lists"]} for case in data["cases"]}


def validate(dataset, case_files):
    """Rejects a dataset that couldn't produce a meaningful score."""
    require(isinstance(dataset, dict), "The dataset must be a JSON object.")
    cases, challenges = dataset.get("cases"), dataset.get("reviewer_challenges")
    require(text(dataset.get("version")) and dataset.get("local_gate") == "all_checks_pass"
            and isinstance(cases, list) and cases and isinstance(challenges, list) and challenges,
            "The dataset needs a version, local_gate=all_checks_pass, and at least one case and reviewer challenge.")

    alert_ids = [text(case.get("alert_id")) for case in cases]
    require(len(set(alert_ids)) == len(alert_ids), "Each alert can appear only once in the dataset.")
    for case in cases:
        alert_id = text(case.get("alert_id"))
        require(alert_id in case_files, f"Unknown alert {alert_id}.")
        dispositions = case.get("accepted_dispositions")
        require(isinstance(dispositions, list) and dispositions and all(d in DISPOSITIONS for d in dispositions),
                f"{alert_id} needs at least one supported disposition.")
        valid = valid_ids(case_files[alert_id])
        groups = case.get("required_citation_groups")
        require(isinstance(groups, list) and groups and all(isinstance(g, list) and g and set(g) <= valid for g in groups),
                f"Required evidence for {alert_id} must reference IDs in its supplied case or screening lists.")

    challenge_ids = [text(challenge.get("id")) for challenge in challenges]
    require(len(set(challenge_ids)) == len(challenge_ids), "Each reviewer challenge needs a unique ID.")
    for challenge in challenges:
        name = text(challenge.get("id"))
        require(name and text(challenge.get("alert_id")) in case_files and isinstance(challenge.get("recommendation"), dict),
                f"Reviewer challenge {name} needs a known alert and a recommendation.")
        require(challenge.get("expected_verdict") in ("agree", "disagree"),
                f"Reviewer challenge {name} needs an expected verdict of agree or disagree.")


def load_replay(path, dataset):
    """Recorded answers by item ID. The file must have a nonempty answer for every item in the dataset."""
    recorded = load_json(path)
    require(isinstance(recorded, dict), f"{path} must contain a JSON object.")
    answers = {}
    for item_id in item_ids(dataset):
        answers[item_id] = text(recorded.get(item_id))
        require(answers[item_id].strip(), f"The replay file needs a nonempty answer for {item_id}.")
    return answers


def require(condition, message):
    if not condition:
        raise ValueError(message)
