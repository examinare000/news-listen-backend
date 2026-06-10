import pytest
from unittest.mock import MagicMock, patch


def test_generate_text_returns_string():
    with patch("shared.gemini_client.genai.Client") as mock_client_class:
        mock_client = MagicMock()
        mock_client_class.return_value = mock_client

        mock_response = MagicMock()
        mock_response.text = "Generated text response"
        mock_client.models.generate_content.return_value = mock_response

        from shared.gemini_client import GeminiClient
        client = GeminiClient(api_key="test-key")
        result = client.generate_text("Hello, generate something")

        assert result == "Generated text response"
        mock_client.models.generate_content.assert_called_once()


def test_generate_tts_returns_bytes():
    with patch("shared.gemini_client.genai.Client") as mock_client_class:
        mock_client = MagicMock()
        mock_client_class.return_value = mock_client

        mock_part = MagicMock()
        mock_part.inline_data.data = b"fake-audio-bytes"
        mock_response = MagicMock()
        mock_response.candidates[0].content.parts = [mock_part]
        mock_client.models.generate_content.return_value = mock_response

        from shared.gemini_client import GeminiClient
        client = GeminiClient(api_key="test-key")
        result = client.generate_tts("Hello world", voice="Kore")

        assert result == b"fake-audio-bytes"


def test_generate_tts_raises_value_error_on_empty_candidates():
    """Gemini TTS が candidates を返さない場合、明確な ValueError を送出する"""
    with patch("shared.gemini_client.genai.Client") as mock_client_class:
        mock_client = MagicMock()
        mock_client_class.return_value = mock_client

        mock_response = MagicMock()
        mock_response.candidates = []  # 空の candidates
        mock_client.models.generate_content.return_value = mock_response

        from shared.gemini_client import GeminiClient
        client = GeminiClient(api_key="test-key")

        with pytest.raises(ValueError, match="candidates"):
            client.generate_tts("Hello world", voice="Kore")


def test_generate_tts_raises_value_error_on_empty_parts():
    """Gemini TTS が音声 parts を返さない場合、明確な ValueError を送出する"""
    with patch("shared.gemini_client.genai.Client") as mock_client_class:
        mock_client = MagicMock()
        mock_client_class.return_value = mock_client

        mock_candidate = MagicMock()
        mock_candidate.content.parts = []  # 空の parts
        mock_response = MagicMock()
        mock_response.candidates = [mock_candidate]
        mock_client.models.generate_content.return_value = mock_response

        from shared.gemini_client import GeminiClient
        client = GeminiClient(api_key="test-key")

        with pytest.raises(ValueError, match="audio parts"):
            client.generate_tts("Hello world", voice="Kore")


def test_generate_tts_raises_value_error_when_inline_data_missing():
    """inline_data が None の場合、明確な ValueError を送出する"""
    with patch("shared.gemini_client.genai.Client") as mock_client_class:
        mock_client = MagicMock()
        mock_client_class.return_value = mock_client

        mock_part = MagicMock()
        mock_part.inline_data = None
        mock_candidate = MagicMock()
        mock_candidate.content.parts = [mock_part]
        mock_response = MagicMock()
        mock_response.candidates = [mock_candidate]
        mock_client.models.generate_content.return_value = mock_response

        from shared.gemini_client import GeminiClient
        client = GeminiClient(api_key="test-key")

        with pytest.raises(ValueError, match="inline audio data"):
            client.generate_tts("Hello world", voice="Kore")
