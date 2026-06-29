# SPDX-License-Identifier: MIT
"""Integration tests — mock LLM server testing of the full generation pipeline."""

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from ultrawhale.scoring import reset_seen_hashes


class MockLLMHandler(BaseHTTPRequestHandler):
    """Mock LLM server returning canned responses.

    ``responses`` must be set by each test to an iterator of JSON strings
    before starting the server.  Defaults to a single canned response.
    """

    responses: iter = iter([json.dumps({"choices": [{"message": {"content": "Default."}}]})])

    def do_POST(self):
        content_len = int(self.headers.get("Content-Length", 0))
        _ = self.rfile.read(content_len)  # consume request body

        try:
            response = next(self.responses)
        except StopIteration:
            response = json.dumps({"choices": [{"message": {"content": "Default response."}}]})

        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(response.encode())

    def do_GET(self):
        if "/v1/models" in self.path:
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"data": [{"id": "qwen3.6-27b"}]}).encode())
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        pass  # Silence server logs


@pytest.fixture
def mock_llm_server():
    """Start a mock LLM server on a random port."""
    server = HTTPServer(("127.0.0.1", 0), MockLLMHandler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{port}"
    server.shutdown()


class TestGenerateIntegration:
    def test_generate_single_pair(self, mock_llm_server, tmp_path: Path):
        """Generate a single Q&A pair against mock LLM server."""
        from ultrawhale.generate import generate_dataset

        reset_seen_hashes()
        output_file = tmp_path / "test_output.jsonl"

        # Reset mock responses
        MockLLMHandler.responses = iter(
            [
                json.dumps(
                    {"choices": [{"message": {"content": "What is a binary search tree and how does it work?"}}]}
                ),
                json.dumps(
                    {
                        "choices": [
                            {
                                "message": {
                                    "content": (
                                        "A binary search tree is a data structure where each node "
                                        "has at most two children — smaller values left, larger right."
                                    )
                                }
                            }
                        ]
                    }
                ),
            ]
        )

        generate_dataset(
            model="qwen3.6-27b",
            num_pairs=1,
            output_file=str(output_file),
            llm_host=mock_llm_server,
            topic_category="cs",
            skip_curation=True,
        )

        assert output_file.exists()
        lines = output_file.read_text().strip().split("\n")
        assert len(lines) == 1
        pair = json.loads(lines[0])
        assert "user_message" in pair
        assert "free_response" in pair
        assert "quality_score" in pair
        assert pair["quality_score"] > 0


class TestGenerateMultiplePairs:
    def test_generate_five_pairs(self, mock_llm_server, tmp_path: Path):
        """Generate 5 pairs — tests the retry and scoring loop."""
        from ultrawhale.generate import generate_dataset

        reset_seen_hashes()
        output_file = tmp_path / "test_output_5.jsonl"

        # Build enough canned responses for 5 pairs (Q + A each = 10 responses)
        responses = []
        for i in range(10):
            responses.append(
                json.dumps(
                    {
                        "choices": [
                            {"message": {"content": f"Canned response number {i} for testing the generation loop."}}
                        ]
                    }
                )
            )
        MockLLMHandler.responses = iter(responses)

        generate_dataset(
            model="qwen3.6-27b",
            num_pairs=5,
            output_file=str(output_file),
            llm_host=mock_llm_server,
            topic_category="cs",
            skip_curation=True,
        )

        assert output_file.exists()
        lines = [line for line in output_file.read_text().strip().split("\n") if line]
        # Some pairs may be filtered by quality scoring — we should have at least 1
        assert len(lines) >= 1


class TestConfigIntegration:
    def test_config_from_env(self, monkeypatch):
        """Config should load from environment variables."""
        from ultrawhale.config import Config

        monkeypatch.setenv("MISTRALRS_HOST", "http://test:9999")
        monkeypatch.setenv("ULTRAWHALE_MAX_WORKERS", "4")
        monkeypatch.setenv("ULTRAWHALE_MIN_SCORE", "0.75")
        monkeypatch.setenv("HF_TOKEN", "hf_test_token_abc123")

        cfg = Config()
        assert cfg.llm_host == "http://test:9999"
        assert cfg.max_workers == 4
        assert cfg.min_quality_score == 0.75
        assert cfg.hf_token == "hf_test_token_abc123"
        assert cfg.mask_token() == "hf_t…c123"

    def test_config_defaults(self):
        """Config should have sensible defaults when no env vars are set."""
        from ultrawhale.config import Config

        cfg = Config()
        assert cfg.llm_host == "http://localhost:8080"
        assert cfg.max_workers == 8
        assert cfg.min_workers == 2
        assert cfg.min_quality_score == 0.65
