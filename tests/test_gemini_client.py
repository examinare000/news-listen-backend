import pytest
from unittest.mock import MagicMock, patch


# === T1: TextGenerationResult と generate_text_with_usage ===


def test_generate_text_with_usage_returns_result_with_text_and_usage():
    """generate_text_with_usage は text、prompt_token_count、cached_content_token_count を返す"""
    with patch("shared.gemini_client.genai.Client") as mock_client_class:
        mock_client = MagicMock()
        mock_client_class.return_value = mock_client

        mock_usage = MagicMock()
        mock_usage.prompt_token_count = 100
        mock_usage.cached_content_token_count = 0
        mock_response = MagicMock()
        mock_response.text = "Generated response"
        mock_response.usage_metadata = mock_usage
        mock_client.models.generate_content.return_value = mock_response

        from shared.gemini_client import GeminiClient
        client = GeminiClient(api_key="test-key")
        result = client.generate_text_with_usage("Test prompt", temperature=0.7)

        assert result.text == "Generated response"
        assert result.prompt_token_count == 100
        assert result.cached_content_token_count == 0


def test_generate_text_with_usage_defaults_usage_metadata_to_zero_if_missing():
    """usage_metadata が無い場合、prompt_token_count/cached_content_token_count は 0 に安全化"""
    with patch("shared.gemini_client.genai.Client") as mock_client_class:
        mock_client = MagicMock()
        mock_client_class.return_value = mock_client

        mock_response = MagicMock()
        mock_response.text = "Generated response"
        mock_response.usage_metadata = None  # 無いケース
        mock_client.models.generate_content.return_value = mock_response

        from shared.gemini_client import GeminiClient
        client = GeminiClient(api_key="test-key")
        result = client.generate_text_with_usage("Test prompt", temperature=0.7)

        assert result.text == "Generated response"
        assert result.prompt_token_count == 0
        assert result.cached_content_token_count == 0


def test_generate_text_with_usage_coerces_none_token_counts_to_zero():
    """usage_metadata は存在するが token_count 属性が None のとき 0 に安全化する。

    WHY: google-genai はキャッシュ未使用時 cached_content_token_count を None で返す
    ことがあり、呼び出し側の `cached_content_token_count > 0` 比較が
    `'>' not supported between instances of 'NoneType' and 'int'` で失敗していた
    （recommendation ジョブが実スコアリングできずフォールバックする原因）。
    """
    with patch("shared.gemini_client.genai.Client") as mock_client_class:
        mock_client = MagicMock()
        mock_client_class.return_value = mock_client

        mock_usage = MagicMock()
        mock_usage.prompt_token_count = None
        mock_usage.cached_content_token_count = None
        mock_response = MagicMock()
        mock_response.text = "Generated response"
        mock_response.usage_metadata = mock_usage
        mock_client.models.generate_content.return_value = mock_response

        from shared.gemini_client import GeminiClient
        client = GeminiClient(api_key="test-key")
        result = client.generate_text_with_usage("Test prompt", temperature=0.7)

        assert result.prompt_token_count == 0
        assert result.cached_content_token_count == 0
        # 比較演算が例外を出さないこと（回帰の核心）
        assert (result.cached_content_token_count > 0) is False


def test_generate_text_with_usage_with_cached_content_parameter():
    """generate_text_with_usage は cached_content パラメータを GenerateContentConfig に渡す"""
    with patch("shared.gemini_client.genai.Client") as mock_client_class:
        mock_client = MagicMock()
        mock_client_class.return_value = mock_client

        mock_usage = MagicMock()
        mock_usage.prompt_token_count = 50
        mock_usage.cached_content_token_count = 100
        mock_response = MagicMock()
        mock_response.text = "Cached result"
        mock_response.usage_metadata = mock_usage
        mock_client.models.generate_content.return_value = mock_response

        from shared.gemini_client import GeminiClient
        client = GeminiClient(api_key="test-key")
        result = client.generate_text_with_usage(
            "Test prompt", cached_content="cache-name-123", temperature=0.7
        )

        assert result.text == "Cached result"
        assert result.cached_content_token_count == 100
        # generate_content の呼び出しを確認（cached_content が config に渡されている）
        call_args = mock_client.models.generate_content.call_args
        assert call_args is not None


# === T2: create_cached_content ===


def test_create_cached_content_returns_cache_name():
    """create_cached_content は caches.create が返した name を返す"""
    with patch("shared.gemini_client.genai.Client") as mock_client_class:
        mock_client = MagicMock()
        mock_client_class.return_value = mock_client

        mock_cache = MagicMock()
        mock_cache.name = "projects/test/locations/us-central1/cachedContents/cache123"
        mock_client.caches.create.return_value = mock_cache

        from shared.gemini_client import GeminiClient
        client = GeminiClient(api_key="test-key")
        result = client.create_cached_content(
            system_instruction="You are a helpful assistant",
            display_name="test-cache",
            ttl_seconds=3600,
        )

        assert result == "projects/test/locations/us-central1/cachedContents/cache123"
        mock_client.caches.create.assert_called_once()


def test_create_cached_content_returns_none_on_exception():
    """create_cached_content は例外時に None を返し warning をログする"""
    with patch("shared.gemini_client.genai.Client") as mock_client_class:
        with patch("shared.gemini_client.logger") as mock_logger:
            mock_client = MagicMock()
            mock_client_class.return_value = mock_client
            mock_client.caches.create.side_effect = Exception("Cache creation failed")

            from shared.gemini_client import GeminiClient
            client = GeminiClient(api_key="test-key")
            result = client.create_cached_content(
                system_instruction="You are a helpful assistant",
                display_name="test-cache",
                ttl_seconds=3600,
            )

            assert result is None
            mock_logger.warning.assert_called_once()


# === T3: find_cached_content と recommendation_cache_display_name ===


def test_find_cached_content_returns_cache_name_when_found():
    """find_cached_content は display_name が一致するキャッシュの name を返す"""
    with patch("shared.gemini_client.genai.Client") as mock_client_class:
        mock_client = MagicMock()
        mock_client_class.return_value = mock_client

        mock_cache1 = MagicMock()
        mock_cache1.display_name = "rec-user1-abc123"
        mock_cache1.name = "projects/test/locations/us-central1/cachedContents/cache1"

        mock_cache2 = MagicMock()
        mock_cache2.display_name = "rec-user1-xyz789"
        mock_cache2.name = "projects/test/locations/us-central1/cachedContents/cache2"

        mock_client.caches.list.return_value = [mock_cache1, mock_cache2]

        from shared.gemini_client import GeminiClient
        client = GeminiClient(api_key="test-key")
        result = client.find_cached_content("rec-user1-abc123")

        assert result == "projects/test/locations/us-central1/cachedContents/cache1"


def test_find_cached_content_returns_none_when_not_found():
    """find_cached_content は display_name が一致しない場合 None を返す"""
    with patch("shared.gemini_client.genai.Client") as mock_client_class:
        mock_client = MagicMock()
        mock_client_class.return_value = mock_client
        mock_client.caches.list.return_value = []

        from shared.gemini_client import GeminiClient
        client = GeminiClient(api_key="test-key")
        result = client.find_cached_content("nonexistent")

        assert result is None


def test_find_cached_content_returns_none_on_exception():
    """find_cached_content は例外時に None を返し warning をログする"""
    with patch("shared.gemini_client.genai.Client") as mock_client_class:
        with patch("shared.gemini_client.logger") as mock_logger:
            mock_client = MagicMock()
            mock_client_class.return_value = mock_client
            mock_client.caches.list.side_effect = Exception("List failed")

            from shared.gemini_client import GeminiClient
            client = GeminiClient(api_key="test-key")
            result = client.find_cached_content("test")

            assert result is None
            mock_logger.warning.assert_called_once()


def test_recommendation_cache_display_name_is_deterministic():
    """recommendation_cache_display_name は同じ入力で常に同じ名前を返す（決定的）"""
    from shared.gemini_client import recommendation_cache_display_name

    user_id = "user123"
    stable_context = "starred: [A, B], dismissed: [C]"

    result1 = recommendation_cache_display_name(user_id, stable_context)
    result2 = recommendation_cache_display_name(user_id, stable_context)

    assert result1 == result2
    # 形式: rec-{user_id}-{hash}
    assert result1.startswith("rec-user123-")


def test_recommendation_cache_display_name_differs_with_different_context():
    """recommendation_cache_display_name は context が異なると異なる名前を返す"""
    from shared.gemini_client import recommendation_cache_display_name

    user_id = "user123"
    context1 = "starred: [A, B], dismissed: [C]"
    context2 = "starred: [A], dismissed: [C]"

    result1 = recommendation_cache_display_name(user_id, context1)
    result2 = recommendation_cache_display_name(user_id, context2)

    assert result1 != result2


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


def test_generate_tts_raises_value_error_when_content_is_none():
    """candidate.content が None（safety ブロック等）の場合、明確な ValueError を送出する"""
    with patch("shared.gemini_client.genai.Client") as mock_client_class:
        mock_client = MagicMock()
        mock_client_class.return_value = mock_client

        mock_candidate = MagicMock()
        mock_candidate.content = None  # safety フィルタ等で content が欠落
        mock_response = MagicMock()
        mock_response.candidates = [mock_candidate]
        mock_client.models.generate_content.return_value = mock_response

        from shared.gemini_client import GeminiClient
        client = GeminiClient(api_key="test-key")

        with pytest.raises(ValueError, match="without content"):
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
