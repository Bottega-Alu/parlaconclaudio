"""
Deepgram STT Engine — Cloud speech-to-text via Deepgram Nova-3.

Free tier: $200 credit (no expiration), no credit card required.
Endpoint: https://api.deepgram.com/v1/listen
"""

import json
import logging
import time
import urllib.error
import urllib.request
from typing import Optional, Union

import numpy as np

from .base import (
    STTEngine,
    TranscriptionResult,
    TranscriptionSegment,
    ndarray_to_wav_bytes,
)

logger = logging.getLogger(__name__)

_ENDPOINT = "https://api.deepgram.com/v1/listen"
_MODEL = "nova-3"
_CONNECT_TIMEOUT = 5
_READ_TIMEOUT = 45
_MAX_RETRIES = 1


class DeepgramSTTEngine(STTEngine):
    """Cloud STT engine using Deepgram's Nova-3 API."""

    def __init__(self, api_key: str):
        self._api_key = api_key

    @property
    def name(self) -> str:
        return "deepgram"

    @property
    def is_local(self) -> bool:
        return False

    @property
    def supports_word_timestamps(self) -> bool:
        return True

    def transcribe(
        self,
        audio: Union[np.ndarray, str],
        language: Optional[str] = None,
    ) -> TranscriptionResult:
        t0 = time.monotonic()

        if isinstance(audio, np.ndarray):
            wav_bytes = ndarray_to_wav_bytes(audio)
        else:
            with open(audio, "rb") as f:
                wav_bytes = f.read()

        if not wav_bytes:
            return TranscriptionResult(
                text="", segments=[], language="", provider=self.name, error="empty_audio"
            )

        result = self._call_api(wav_bytes, language)
        result.latency_ms = int((time.monotonic() - t0) * 1000)
        return result

    def _call_api(
        self, wav_bytes: bytes, language: Optional[str], _retry: int = 0
    ) -> TranscriptionResult:
        params = f"model={_MODEL}&punctuate=true&smart_format=true"
        if language:
            params += f"&language={language}"
        url = f"{_ENDPOINT}?{params}"

        headers = {
            "Authorization": f"Token {self._api_key}",
            "Content-Type": "audio/wav",
            "User-Agent": "parlaconclaudio/1.0",
        }

        req = urllib.request.Request(url, data=wav_bytes, headers=headers, method="POST")

        try:
            with urllib.request.urlopen(req, timeout=_READ_TIMEOUT) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                return self._parse_response(data)

        except urllib.error.HTTPError as e:
            status = e.code
            if status == 401:
                logger.error("Deepgram: invalid API key (401)")
                return self._error_result("auth_failed")
            if status == 429:
                retry_after = e.headers.get("Retry-After")
                logger.warning(f"Deepgram: rate limited (429), Retry-After={retry_after}")
                if _retry < _MAX_RETRIES and retry_after:
                    wait = min(float(retry_after), 10.0)
                    time.sleep(wait)
                    return self._call_api(wav_bytes, language, _retry + 1)
                return self._error_result("rate_limit")
            if status >= 500 and _retry < _MAX_RETRIES:
                logger.warning(f"Deepgram: server error ({status}), retrying...")
                time.sleep(1)
                return self._call_api(wav_bytes, language, _retry + 1)
            logger.error(f"Deepgram: HTTP {status}")
            return self._error_result(f"http_{status}")

        except (urllib.error.URLError, TimeoutError, OSError) as e:
            if _retry < _MAX_RETRIES:
                logger.warning(f"Deepgram: network error ({e}), retrying...")
                time.sleep(1)
                return self._call_api(wav_bytes, language, _retry + 1)
            logger.error(f"Deepgram: network error: {e}")
            return self._error_result("network_error")

        except Exception as e:
            logger.error(f"Deepgram: unexpected error: {e}")
            return self._error_result("unknown_error")

    def _parse_response(self, data: dict) -> TranscriptionResult:
        segments = []
        text = ""
        language = ""
        duration = None

        try:
            results = data.get("results", {})
            channels = results.get("channels", [])
            if channels:
                channel = channels[0]
                alternatives = channel.get("alternatives", [])
                if alternatives:
                    alt = alternatives[0]
                    text = alt.get("transcript", "")

                    for word in alt.get("words", []):
                        segments.append(
                            TranscriptionSegment(
                                text=word.get("word", ""),
                                start=word.get("start", 0.0),
                                end=word.get("end", 0.0),
                                confidence=word.get("confidence"),
                            )
                        )

                    detected = channel.get("detected_language")
                    if detected:
                        language = detected

            metadata = data.get("metadata", {})
            duration = metadata.get("duration")
        except Exception as e:
            logger.warning(f"Deepgram: response parsing issue: {e}")

        return TranscriptionResult(
            text=text.strip(),
            segments=segments,
            language=language,
            duration=duration,
            provider=self.name,
            model=_MODEL,
        )

    def _error_result(self, error: str) -> TranscriptionResult:
        return TranscriptionResult(
            text="",
            segments=[],
            language="",
            provider=self.name,
            model=_MODEL,
            error=error,
        )
