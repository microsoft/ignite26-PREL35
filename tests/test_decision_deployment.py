import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_decision_model_is_allowed_by_skillable_policy():
    template = json.loads((ROOT / "infra/main.json").read_text())
    models = template["parameters"]["models"]["defaultValue"]
    decision = next(model for model in models if model.get("deploymentName") == "decision-1")
    assert decision == {
        "name": "Microsoft-Decision-1.1",
        "deploymentName": "decision-1",
        "format": "Microsoft",
        "registry": "azureml-msr",
        "version": "1",
        "capacity": 50,
        "sku": "DataZoneStandard",
    }

    policy = json.loads((ROOT / "infra/skillable/acp-v6.json").read_text())
    allowed = policy["if"]["anyOf"][0]["not"]["anyOf"]
    rules = [item["allOf"] for item in allowed if "allOf" in item]
    decision_rule = next(
        rule for rule in rules
        if any(condition.get("field") == "Microsoft.CognitiveServices/accounts/deployments/model.name"
               and condition.get("equals") == decision["name"] for condition in rule)
    )
    assert {"field": "Microsoft.CognitiveServices/accounts/deployments/sku.name",
            "equals": decision["sku"]} in decision_rule
    assert {"field": "Microsoft.CognitiveServices/accounts/deployments/sku.capacity",
            "lessOrEquals": 50} in decision_rule
