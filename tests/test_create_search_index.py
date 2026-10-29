import copy
import io
import json
import tempfile
import unittest
import urllib.error
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from scripts import create_search_index as index


class IndexSetupTests(unittest.TestCase):
    def setUp(self):
        self.documents = index.load_documents(index.ROOT / "data/investigator/schemes.json")[:2]

    def test_rejects_non_synthetic_notes_and_duplicate_ids(self):
        for change in ("synthetic", "duplicate"):
            with self.subTest(change=change), tempfile.TemporaryDirectory() as directory:
                documents = copy.deepcopy(self.documents)
                if change == "synthetic":
                    documents[0]["is_synthetic"] = False
                else:
                    documents[1]["id"] = documents[0]["id"]
                path = Path(directory) / "notes.json"
                path.write_text(json.dumps({"schema_version": 1, "documents": documents}))
                with self.assertRaises(ValueError):
                    index.load_documents(path)

    def test_rejects_endpoints_that_could_receive_credentials_outside_azure(self):
        for endpoint in ("http://test.search.windows.net", "https://test.search.windows.net.example.org",
                         "https://user:password@test.search.windows.net", "https://test.search.windows.net/path"):
            with self.subTest(endpoint=endpoint), self.assertRaises(ValueError):
                index.validate_endpoint(endpoint, ".search.windows.net")

    def test_embedding_results_follow_response_indexes_not_response_order(self):
        client = Mock()
        client.embeddings.create.return_value.data = [
            SimpleNamespace(index=1, embedding=[2.0] * index.DIMENSIONS),
            SimpleNamespace(index=0, embedding=[1.0] * index.DIMENSIONS),
        ]
        result = index.embed_documents(client, self.documents, index.MODEL)
        self.assertEqual([document["content_vector"][0] for document in result], [1.0, 2.0])
        client.embeddings.create.return_value.data[0].embedding = [2.0]
        with self.assertRaisesRegex(RuntimeError, "invalid vector"):
            index.embed_documents(client, self.documents, index.MODEL)

    def test_index_creation_accepts_empty_success_responses(self):
        for payload, expected in ((b"", {}), (b'{"name":"schemes"}', {"name": "schemes"})):
            with self.subTest(payload=payload):
                search = index.SearchClient("https://example.search.windows.net", Mock())
                search.opener = Mock()
                search.opener.open.return_value = io.BytesIO(payload)
                self.assertEqual(search.request("PUT", "indexes/schemes", {}), expected)

    def test_unchanged_notes_do_not_regenerate_embeddings_or_upload(self):
        search, embeddings = Mock(), Mock()
        search.request.side_effect = [{}, {"value": [
            {"id": document["id"], "content_hash": index.content_hash(document, index.MODEL)}
            for document in self.documents
        ]}]
        index.seed_index(search, embeddings, {"name": "schemes"}, self.documents, index.MODEL)
        embeddings.embeddings.create.assert_not_called()
        self.assertEqual(search.request.call_count, 2)

    def test_partial_upload_is_reported_as_incomplete(self):
        search, embeddings = Mock(), Mock()
        search.request.side_effect = [{}, {"value": []}, {"value": [
            {"key": self.documents[0]["id"], "status": True},
            {"key": self.documents[1]["id"], "status": False},
        ]}]
        embeddings.embeddings.create.return_value.data = [
            SimpleNamespace(index=i, embedding=[1.0] * index.DIMENSIONS) for i in range(len(self.documents))
        ]
        with self.assertRaisesRegex(RuntimeError, "incomplete"):
            index.seed_index(search, embeddings, {"name": "schemes"}, self.documents, index.MODEL)

    def test_missing_knowledge_base_can_be_created_but_other_errors_still_fail(self):
        search = index.SearchClient("https://example.search.windows.net", Mock())
        search.opener = Mock()
        search.opener.open.side_effect = urllib.error.HTTPError("https://example", 404, "Not Found", {}, None)
        self.assertIsNone(search.request("GET", "knowledgebases/research", allow_not_found=True))
        with self.assertRaisesRegex(RuntimeError, "HTTP 404"):
            search.request("PUT", "knowledgesources/research-schemes", {})

    def test_service_validation_message_redacts_the_access_token(self):
        credential = Mock()
        credential.get_token.return_value.token = "test-access-token"
        search = index.SearchClient("https://example.search.windows.net", credential)
        search.opener = Mock()
        body = io.BytesIO(json.dumps({
            "error": {"message": "Configuration rejected: test-access-token"},
        }).encode())
        search.opener.open.side_effect = urllib.error.HTTPError("https://example", 400, "Bad Request", {}, body)
        with self.assertRaisesRegex(RuntimeError, "Configuration rejected") as raised:
            search.request("PUT", "knowledgebases/research", {})
        self.assertNotIn("test-access-token", str(raised.exception))

    def test_knowledge_base_setup_connects_the_index_and_preserves_added_sources(self):
        for existing in (
            None,
            {"knowledgeSources": [{"name": "future-source"}]},
            {"knowledgeSources": [{"name": "future-source"}, {"name": "research-schemes"}]},
        ):
            with self.subTest(existing=existing):
                search = Mock()
                search.request.return_value = existing
                index.setup_knowledge_base(
                    search, "schemes", "research", "https://example.openai.azure.com", "gpt-5-mini",
                )
                calls = search.request.call_args_list
                self.assertEqual(calls[0].args, ("GET", "knowledgebases/research"))
                source = calls[1].args[2]
                self.assertEqual(source["searchIndexParameters"]["searchIndexName"], "schemes")
                self.assertEqual(source["searchIndexParameters"]["baseFilter"], "is_synthetic eq true")
                self.assertNotIn("content_vector", [
                    field["name"] for field in source["searchIndexParameters"]["sourceDataFields"]
                ])
                knowledge_base = calls[2].args[2]
                sources = [source["name"] for source in knowledge_base["knowledgeSources"]]
                self.assertEqual(sources.count("research-schemes"), 1)
                self.assertEqual("future-source" in sources, existing is not None)
                parameters = knowledge_base["models"][0]["azureOpenAIParameters"]
                self.assertEqual(parameters["deploymentId"], "gpt-5-mini")
                self.assertNotIn("apiKey", parameters)

    def test_failed_knowledge_source_creation_stops_knowledge_base_creation(self):
        search = Mock()
        search.request.side_effect = [None, RuntimeError("Source creation failed")]
        with self.assertRaisesRegex(RuntimeError, "Source creation failed"):
            index.setup_knowledge_base(
                search, "schemes", "research", "https://example.openai.azure.com", "gpt-5-mini",
            )
        self.assertEqual(search.request.call_count, 2)


if __name__ == "__main__":
    unittest.main()
