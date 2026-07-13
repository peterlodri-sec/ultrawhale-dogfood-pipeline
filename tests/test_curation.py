# SPDX-License-Identifier: MIT
"""Tests for curation engine."""

from unittest.mock import MagicMock, patch

from ultrawhale.curation import CurationEngine


class TestCurationEngine:
    def test_judge_pair_returns_float(self):
        """judge_pair should return a float between 1 and 5."""
        mock_client = MagicMock()
        mock_client.chat.return_value = "4.5"

        with patch("ultrawhale.curation.HFInferenceClient", return_value=mock_client):
            engine = CurationEngine("fake-token")
            engine.client = mock_client

            score = engine.judge_pair(
                {"user_message": "What is Python?", "free_response": "Python is a programming language."}
            )
            assert isinstance(score, float)
            assert 1.0 <= score <= 5.0

    def test_judge_pair_fallback_on_none(self):
        """When chat returns None, should default to 3.0."""
        mock_client = MagicMock()
        mock_client.chat.return_value = None

        engine = CurationEngine("fake-token")
        engine.client = mock_client
        score = engine.judge_pair({"user_message": "Q", "free_response": "A"})
        assert score == 3.0

    def test_judge_pair_fallback_on_invalid(self):
        """When chat returns non-numeric, should default to 3.0."""
        mock_client = MagicMock()
        mock_client.chat.return_value = "not a number"

        engine = CurationEngine("fake-token")
        engine.client = mock_client
        score = engine.judge_pair({"user_message": "Q", "free_response": "A"})
        assert score == 3.0

    def test_verify_code_always_true(self):
        """verify_code currently returns True (placeholder)."""
        engine = CurationEngine("fake-token")
        assert engine.verify_code({"free_response": "print('hello')"}) is True
        assert engine.verify_code({"free_response": "no code here"}) is True

    def test_curate_rejects_low_score(self):
        """curate should return None when score < 4.0."""
        mock_client = MagicMock()
        mock_client.chat.return_value = "3.5"

        engine = CurationEngine("fake-token")
        engine.client = mock_client

        result = engine.curate({"user_message": "What is X?", "free_response": "X is Y."})
        assert result is None

    def test_curate_accepts_high_score(self):
        """curate should return pair with curated_score when score ≥ 4.0."""
        mock_client = MagicMock()
        mock_client.chat.return_value = "4.7"

        engine = CurationEngine("fake-token")
        engine.client = mock_client

        pair = {
            "user_message": "What is a neural network?",
            "free_response": "A neural network is a computational model inspired by biological neural networks.",
        }
        result = engine.curate(pair)
        assert result is not None
        assert result["curated_score"] == 4.7

    def test_curate_exact_threshold(self):
        """Score exactly 4.0 should pass."""
        mock_client = MagicMock()
        mock_client.chat.return_value = "4.0"

        engine = CurationEngine("fake-token")
        engine.client = mock_client

        result = engine.curate({"user_message": "Q", "free_response": "A"})
        assert result is not None
