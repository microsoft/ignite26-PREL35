"""Set up the investigator index, knowledge source and knowledge base using the deployment identity."""

import argparse
import hashlib
import json
import math
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from azure.identity import AzureCliCredential, get_bearer_token_provider
from openai import AzureOpenAI

ROOT = Path(__file__).resolve().parent.parent
MODEL = "text-embedding-3-small"
DIMENSIONS = 1536
KNOWLEDGE_API_VERSION = "2026-08-01-preview"
TEXT_FIELDS = ("title", "content", "source_title")
FILTER_FIELDS = ("scheme_type", "approved_topic_id", "content_version")
ARRAY_FIELDS = ("indicators", "benign_explanations", "analyst_questions")


def validate_endpoint(value, suffix):
    parsed = urllib.parse.urlsplit(value)
    if (
        parsed.scheme != "https"
        or not (parsed.hostname or "").endswith(suffix)
        or parsed.username
        or parsed.password
        or parsed.port not in (None, 443)
        or parsed.path not in ("", "/")
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError(f"Expected an HTTPS Azure endpoint ending in {suffix}.")
    return value.rstrip("/")


def load_documents(path):
    corpus = json.loads(path.read_text(encoding="utf-8"))
    documents = corpus.get("documents")
    if corpus.get("schema_version") != 1 or not isinstance(documents, list) or not 1 <= len(documents) <= 1000:
        raise ValueError("Expected a version 1 corpus containing 1 to 1000 documents.")
    ids = set()
    required = ("id", "source_url", *TEXT_FIELDS, *FILTER_FIELDS)
    for document in documents:
        if not isinstance(document, dict) or document.get("is_synthetic") is not True:
            raise ValueError("Every indexed note must be explicitly marked as synthetic.")
        if any(not isinstance(document.get(field), str) or not document[field].strip() for field in required):
            raise ValueError("A note is missing required text or source fields.")
        if not re.fullmatch(r"[A-Za-z0-9_-]+", document["id"]) or document["id"] in ids:
            raise ValueError("Note IDs must be unique Search-compatible slugs.")
        ids.add(document["id"])
        if any(
            not isinstance(document.get(field), list)
            or not document[field]
            or any(not isinstance(item, str) or not item.strip() for item in document[field])
            for field in ARRAY_FIELDS
        ):
            raise ValueError("Each note needs indicators, legitimate explanations and analyst questions.")
        if urllib.parse.urlsplit(document["source_url"]).scheme != "https":
            raise ValueError("Each note needs an HTTPS supporting reference.")
        if set(document) != set(required) | set(ARRAY_FIELDS) | {"is_synthetic"}:
            raise ValueError("Unexpected corpus fields; do not index case data or precomputed vectors.")
    return documents


def index_definition(name, embedding_endpoint, deployment):
    fields = [{"name": "id", "type": "Edm.String", "key": True, "filterable": True}]
    fields += [{"name": field, "type": "Edm.String", "searchable": True} for field in TEXT_FIELDS]
    fields += [{"name": field, "type": "Edm.String", "filterable": True} for field in FILTER_FIELDS]
    fields += [{"name": field, "type": "Collection(Edm.String)", "searchable": True} for field in ARRAY_FIELDS]
    fields += [
        {"name": "source_url", "type": "Edm.String"},
        {"name": "is_synthetic", "type": "Edm.Boolean", "filterable": True},
        {"name": "content_hash", "type": "Edm.String"},
        {
            "name": "content_vector",
            "type": "Collection(Edm.Single)",
            "searchable": True,
            "retrievable": False,
            "dimensions": DIMENSIONS,
            "vectorSearchProfile": "scheme-vectors",
        },
    ]
    return {
        "name": name,
        "fields": fields,
        "semantic": {
            "defaultConfiguration": "scheme-semantic",
            "configurations": [{
                "name": "scheme-semantic",
                "prioritizedFields": {
                    "titleField": {"fieldName": "title"},
                    "prioritizedContentFields": [
                        {"fieldName": field}
                        for field in ("content", *ARRAY_FIELDS)
                    ],
                },
            }],
        },
        "vectorSearch": {
            "algorithms": [{"name": "scheme-hnsw", "kind": "hnsw"}],
            "profiles": [{"name": "scheme-vectors", "algorithm": "scheme-hnsw", "vectorizer": "scheme-vectorizer"}],
            "vectorizers": [{
                "name": "scheme-vectorizer",
                "kind": "azureOpenAI",
                "azureOpenAIParameters": {
                    "resourceUri": embedding_endpoint,
                    "deploymentId": deployment,
                    "modelName": MODEL,
                },
            }],
        },
    }


class NoRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class SearchClient:
    def __init__(self, endpoint, credential, api_version="2025-09-01"):
        self.endpoint = validate_endpoint(endpoint, ".search.windows.net")
        self.credential = credential
        self.api_version = api_version
        self.opener = urllib.request.build_opener(NoRedirects())

    def request(self, method, path, body=None, *, allow_not_found=False):
        for attempt in range(10):
            token = self.credential.get_token("https://search.azure.com/.default").token
            request = urllib.request.Request(
                f"{self.endpoint}/{path}?api-version={self.api_version}",
                data=None if body is None else json.dumps(body, allow_nan=False).encode("utf-8"),
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                method=method,
            )
            try:
                with self.opener.open(request, timeout=60) as response:
                    payload = response.read()
                    return json.loads(payload) if payload else {}
            except urllib.error.HTTPError as error:
                if error.code == 404 and allow_not_found:
                    return None
                if error.code not in (403, 429, 500, 502, 503, 504) or attempt == 9:
                    detail = ""
                    if error.code == 400:
                        try:
                            message = json.load(error).get("error", {}).get("message", "")
                            detail = f" {str(message).replace(token, '[redacted]')[:1000]}"
                        except (ValueError, AttributeError):
                            pass
                    raise RuntimeError(
                        f"Search {method} {path} failed (HTTP {error.code}).{detail} "
                        "Check the configuration and role assignments."
                    ) from None
                print(f"Waiting for Search permissions or availability (HTTP {error.code})...", flush=True)
                time.sleep(min(2 ** (attempt + 1), 30))


def content_hash(document, deployment):
    value = json.dumps([MODEL, DIMENSIONS, deployment, document], sort_keys=True, ensure_ascii=True)
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def embed_documents(client, documents, deployment):
    response = client.embeddings.create(
        model=deployment,
        dimensions=DIMENSIONS,
        input=[f"{document['title']}\n\n{document['content']}" for document in documents],
    )
    vectors = {item.index: item.embedding for item in response.data}
    if len(response.data) != len(documents) or set(vectors) != set(range(len(documents))):
        raise RuntimeError("The embedding service did not return one vector per note.")
    result = []
    for index, document in enumerate(documents):
        vector = vectors[index]
        if len(vector) != DIMENSIONS or any(not math.isfinite(value) for value in vector):
            raise RuntimeError("The embedding service returned an invalid vector.")
        result.append({
            **document,
            "content_hash": content_hash(document, deployment),
            "content_vector": vector,
            "@search.action": "mergeOrUpload",
        })
    return result


def seed_index(search, embeddings, definition, documents, deployment):
    name = definition["name"]
    search.request("PUT", f"indexes/{name}", definition)
    current = search.request("POST", f"indexes/{name}/docs/search", {
        "search": "*", "select": "id,content_hash", "top": 1000,
    })
    existing = {item["id"]: item.get("content_hash") for item in current["value"]}
    pending = [document for document in documents if existing.get(document["id"]) != content_hash(document, deployment)]
    if not pending:
        print(f"Index {name}: all {len(documents)} synthetic notes are already up to date.")
        return
    vectors = embed_documents(embeddings, pending, deployment)
    response = search.request("POST", f"indexes/{name}/docs/index", {"value": vectors})
    statuses = response.get("value", [])
    expected = {document["id"] for document in pending}
    uploaded = {item.get("key") for item in statuses if item.get("status") is True}
    if len(statuses) != len(pending) or uploaded != expected:
        raise RuntimeError("Some notes were not indexed. Index setup is incomplete; rerun after resolving the upload error.")
    print(f"Index {name}: uploaded {len(pending)} synthetic notes; {len(documents) - len(pending)} unchanged.")


def setup_knowledge_base(search, index_name, name, model_endpoint, deployment):
    source_name = f"{name}-schemes"
    existing = search.request("GET", f"knowledgebases/{name}", allow_not_found=True)
    # Detach the retired built-in Bing source; keep Web IQ and any other added sources.
    sources = [source for source in existing["knowledgeSources"] if source["name"] != f"{name}-web"] if existing else []
    if not any(source["name"] == source_name for source in sources):
        sources.append({"name": source_name})
    search.request("PUT", f"knowledgesources/{source_name}", {
        "name": source_name,
        "kind": "searchIndex",
        "description": "Synthetic financial-crime typologies, benign explanations and analyst questions.",
        "searchIndexParameters": {
            "searchIndexName": index_name,
            "semanticConfigurationName": "scheme-semantic",
            "baseFilter": "is_synthetic eq true",
            "sourceDataFields": [{"name": field} for field in (
                "id", *TEXT_FIELDS, *FILTER_FIELDS, *ARRAY_FIELDS, "source_url", "is_synthetic",
            )],
        },
    })
    definition = {
        "name": name,
        "description": "Research library for the fictional financial-crime lab; not evidence about a customer.",
        "knowledgeSources": sources,
        "models": [{
            "kind": "azureOpenAI",
            "azureOpenAIParameters": {
                "resourceUri": model_endpoint,
                "deploymentId": deployment,
                "modelName": "gpt-5-mini",
            },
        }],
        "outputMode": "extractiveData",
        "retrievalReasoningEffort": {"kind": "low"},
        "retrievalInstructions": (
            "Retrieve general typologies, plausible benign explanations and analyst questions. "
            "Synthetic examples and historical public references are not evidence about any customer."
        ),
        "retrieveDefaults": {
            "maxRuntimeInSeconds": 45,
            "maxOutputDocuments": 4,
            "maxOutputSizeInTokens": 5000,
        },
    }
    if existing:
        definition.update({key: value for key, value in existing.items() if not key.startswith("@odata.")})
    definition["knowledgeSources"] = sources
    search.request("PUT", f"knowledgebases/{name}", definition)
    print(f"Knowledge base {name}: index source {source_name}.")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--endpoint", required=True)
    parser.add_argument("--index", required=True)
    parser.add_argument("--embedding-endpoint", required=True)
    parser.add_argument("--embedding-deployment", required=True)
    parser.add_argument("--knowledge-base", required=True)
    parser.add_argument("--planning-deployment", required=True)
    parser.add_argument("--data", type=Path, default=ROOT / "data/investigator/schemes.json")
    args = parser.parse_args(argv)
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,126}[a-z0-9]", args.index):
        raise ValueError("The Search index name must contain 2 to 128 lowercase letters, digits or hyphens.")
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,118}[a-z0-9]", args.knowledge_base):
        raise ValueError("The knowledge base name must contain 2 to 120 lowercase letters, digits or hyphens.")
    endpoint = validate_endpoint(args.embedding_endpoint, ".openai.azure.com")
    documents = load_documents(args.data)
    with AzureCliCredential() as credential:
        search = SearchClient(args.endpoint, credential)
        with AzureOpenAI(
            azure_endpoint=endpoint,
            api_version="2024-10-21",
            azure_ad_token_provider=get_bearer_token_provider(credential, "https://cognitiveservices.azure.com/.default"),
            timeout=60,
            max_retries=2,
        ) as embeddings:
            seed_index(search, embeddings, index_definition(args.index, endpoint, args.embedding_deployment),
                       documents, args.embedding_deployment)
        knowledge_search = SearchClient(args.endpoint, credential, api_version=KNOWLEDGE_API_VERSION)
        setup_knowledge_base(knowledge_search, args.index, args.knowledge_base, endpoint, args.planning_deployment)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(f"Search setup failed: {error}", file=sys.stderr)
        sys.exit(1)
