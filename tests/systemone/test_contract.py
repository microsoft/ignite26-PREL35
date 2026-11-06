"""Focused offline POC tests; no Azure calls or learner-record writes."""
import copy
import json
import unittest
from types import SimpleNamespace

from run_agent import approved_arguments, unpack, validate_explanation
from run_api import batches, case_hash, score_decisions, validate_assessments


class ContractTests(unittest.TestCase):
    def setUp(self):
        self.case = batches(1)[0]
        self.assessment = {
            "index": 0, "alert_id": self.case["alert"]["id"], "case_sha256": case_hash(self.case),
            "status": "ok", "disposition": "escalate", "model": "microsoft-decision-1",
            "confidence": .75, "probabilities": {"escalate": .8, "request_info": .1, "close_false_positive": .1},
            "usage": {"input_tokens": 12, "output_tokens": 1},
        }
        self.body = {"rules_version": "triage-systemone-v2", "model_deployment": "decision-1",
                     "count": 1, "assessments": [self.assessment]}
        self.explanation = {
            "disposition": "escalate",
            "reasons": [f"Observation [{self.case['transactions'][0]['id']}]"], "gaps": [], "conflicts": [],
        }

    def validate_text(self, explanation=None, assessment=None):
        return validate_explanation(json.dumps(explanation or self.explanation),
                                    self.case, assessment or self.assessment)

    def test_result_is_correlated_to_actual_case_content(self):
        validate_assessments(self.body, [self.case])
        changed = copy.deepcopy(self.case)
        changed["customer"]["name"] += " changed"
        with self.assertRaises(AssertionError):
            validate_assessments(self.body, [changed])

    def test_typed_probabilities_reject_boolean(self):
        self.assessment["probabilities"]["escalate"] = True
        with self.assertRaises(AssertionError):
            validate_assessments(self.body, [self.case])

    def test_unexpected_rules_or_deployment_are_rejected(self):
        for key in ("rules_version", "model_deployment"):
            with self.subTest(key=key), self.assertRaises(AssertionError):
                validate_assessments({**self.body, key: "unexpected"}, [self.case])

    def test_no_approval_for_modified_case_or_other_tool(self):
        item = SimpleNamespace(server_label="disposition", name="asses_next_step",
                               arguments=json.dumps({"cases": [self.case]}))
        self.assertEqual(approved_arguments(item, self.case), {"cases": [self.case]})
        with self.assertRaises(ValueError):
            approved_arguments(item, {**self.case, "customer": {**self.case["customer"], "name": "Changed"}})
        item.name = "other_tool"
        with self.assertRaises(ValueError):
            approved_arguments(item, self.case)

    def test_no_approval_for_multiple_cases(self):
        item = SimpleNamespace(server_label="disposition", name="asses_next_step",
                               arguments=json.dumps({"cases": [self.case, self.case]}))
        with self.assertRaises(ValueError):
            approved_arguments(item, self.case)

    def test_numeric_boolean_is_not_the_original_pep_flag(self):
        changed = copy.deepcopy(self.case)
        changed["customer"]["pep"] = 0
        item = SimpleNamespace(server_label="disposition", name="asses_next_step",
                               arguments=json.dumps({"cases": [changed]}))
        with self.assertRaises(ValueError):
            approved_arguments(item, self.case)

    def test_mcp_numeric_format_normalization_does_not_allow_changed_values(self):
        case = {**self.case, "transactions": [{"id": "T-00001", "amount": 14602.0}]}
        normalized = {**case, "transactions": [{"id": "T-00001", "amount": 14602}]}
        self.assessment["case_sha256"] = case_hash(normalized)
        validate_assessments(self.body, [case], mcp=True)
        with self.assertRaises(AssertionError):
            validate_assessments(self.body, [case])
        case["transactions"][0]["amount"] = 14603.0
        with self.assertRaises(AssertionError):
            validate_assessments(self.body, [case], mcp=True)

    def test_disposition_comes_only_from_the_service(self):
        self.assertEqual(self.validate_text()["disposition"], "escalate")
        self.explanation["disposition"] = "close_false_positive"
        with self.assertRaises(AssertionError):
            self.validate_text()

    def test_wrong_case_record_citation_is_rejected(self):
        self.explanation["reasons"] = ["Unsupported [T-99999]"]
        with self.assertRaises(AssertionError):
            self.validate_text()

    def test_generic_or_missing_profile_citations_are_rejected(self):
        for reason in ("Observation [transactions]", "Observation [customer.invented_field]"):
            with self.subTest(reason=reason), self.assertRaises(AssertionError):
                self.validate_text({**self.explanation, "reasons": [reason]})

    def test_valid_citation_does_not_hide_an_unknown_path(self):
        for citation in ("prior_cases.0.id", "screening_lists.high_risk_jurisdictions", "transactions"):
            with self.subTest(citation=citation), self.assertRaises(AssertionError):
                self.validate_text({**self.explanation, "reasons": [self.explanation["reasons"][0] + f" [{citation}]"]})

    def test_gaps_and_conflicts_cannot_cite_another_case(self):
        for key in ("gaps", "conflicts"):
            with self.subTest(key=key), self.assertRaises(AssertionError):
                self.validate_text({**self.explanation, key: ["Unsupported [T-99999]"]})

    def test_service_failure_is_not_a_request_info_disposition(self):
        failed = {**self.assessment, "status": "upstream_error", "disposition": None,
                  "error": {"code": "decision_http_429"}}
        explanation = {**self.explanation, "disposition": None,
                       "reasons": [], "gaps": ["The service was rate limited."]}
        result = self.validate_text(explanation, failed)
        self.assertIsNone(result["disposition"])
        self.assertEqual(result["assessment"]["error"]["code"], "decision_http_429")

    def test_entire_tool_error_is_not_an_assessment(self):
        with self.assertRaises(ValueError):
            unpack('{"error":{"code":"staged_batch_unavailable"}}')

    def test_synthetic_cases_always_apply_triage_data_policy(self):
        cases = batches(14)
        restricted = next(c for c in cases if c["alert"]["id"] == "ALT-1005")
        self.assertEqual(restricted["documents"], [{"id": "DOC-203", "withheld": "restricted"}])
        self.assertEqual(cases[0]["customer"]["account_number"], "[withheld: confidential]")

    def test_decision_only_eval_scores_actual_choices_and_unavailable_results(self):
        wrong = score_decisions([{"alert_id": "ALT-1001", "status": "ok", "disposition": "request_info"}])
        self.assertEqual(wrong["correct"], 0)
        self.assertEqual(wrong["escalation_recall"], 0)
        unavailable = score_decisions([{"alert_id": "ALT-1010", "status": "upstream_error", "disposition": None}])
        self.assertEqual(unavailable["correct"], 0)
        self.assertIsNone(unavailable["rows"][0]["disposition"])


if __name__ == "__main__":
    unittest.main()
