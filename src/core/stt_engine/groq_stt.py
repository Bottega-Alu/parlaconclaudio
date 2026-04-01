"""
Groq STT Engine — Cloud speech-to-text via Groq's OpenAI-compatible API.

Free tier: 2000 requests/day, 7200 audio seconds/hour, no credit card required.
Endpoint: https://api.groq.com/openai/v1/audio/transcriptions
"""

import json
import logging
import time
import urllib.error
import urllib.request
from typing import Optional, Union
from uuid import uuid4

import numpy as np

from .base import (
    STTEngine,
    TranscriptionResult,
    TranscriptionSegment,
    ndarray_to_wav_bytes,
)

logger = logging.getLogger(__name__)

_ENDPOINT = "https://api.groq.com/openai/v1/audio/transcriptions"
_MODEL = "whisper-large-v3"
_CONNECT_TIMEOUT = 5
_READ_TIMEOUT = 45
_MAX_RETRIES = 1


class GroqSTTEngine(STTEngine):
    """Cloud STT engine using Groq's Whisper API."""

    def __init__(self, api_key: str):
        self._api_key = api_key

    @property
    def name(self) -> str:
        return "groq"

    @property
    def is_local(self) -> bool:
        return False

    @property
    def supports_word_timestamps(self) -> bool:
        return False

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
        boundary = f"----boundary{uuid4().hex}"
        body = self._build_multipart(wav_bytes, language, boundary)
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "User-Agent": "parlaconclaudio/1.0",
        }

        req = urllib.request.Request(_ENDPOINT, data=body, headers=headers, method="POST")

        try:
            with urllib.request.urlopen(req, timeout=_READ_TIMEOUT) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                return self._parse_response(data)

        except urllib.error.HTTPError as e:
            status = e.code
            if status == 401:
                logger.error("Groq: invalid API key (401)")
                return self._error_result("auth_failed")
            if status == 429:
                retry_after = e.headers.get("Retry-After")
                logger.warning(f"Groq: rate limited (429), Retry-After={retry_after}")
                if _retry < _MAX_RETRIES and retry_after:
                    wait = min(float(retry_after), 10.0)
                    time.sleep(wait)
                    return self._call_api(wav_bytes, language, _retry + 1)
                return self._error_result("rate_limit")
            if status >= 500 and _retry < _MAX_RETRIES:
                logger.warning(f"Groq: server error ({status}), retrying...")
                time.sleep(1)
                return self._call_api(wav_bytes, language, _retry + 1)
            logger.error(f"Groq: HTTP {status}")
            return self._error_result(f"http_{status}")

        except (urllib.error.URLError, TimeoutError, OSError) as e:
            if _retry < _MAX_RETRIES:
                logger.warning(f"Groq: network error ({e}), retrying...")
                time.sleep(1)
                return self._call_api(wav_bytes, language, _retry + 1)
            logger.error(f"Groq: network error: {e}")
            return self._error_result("network_error")

        except Exception as e:
            logger.error(f"Groq: unexpected error: {e}")
            return self._error_result("unknown_error")

    def _build_multipart(
        self, wav_bytes: bytes, language: Optional[str], boundary: str
    ) -> bytes:
        parts: list[bytes] = []

        def add_field(name: str, value: str) -> None:
            parts.append(f"--{boundary}\r\n".encode())
            parts.append(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode())
            parts.append(f"{value}\r\n".encode())

        def add_file(name: str, filename: str, data: bytes, content_type: str) -> None:
            parts.append(f"--{boundary}\r\n".encode())
            parts.append(
                f'Content-Disposition: form-data; name="{name}"; filename="{filename}"\r\n'.encode()
            )
            parts.append(f"Content-Type: {content_type}\r\n\r\n".encode())
            parts.append(data)
            parts.append(b"\r\n")

        add_file("file", "audio.wav", wav_bytes, "audio/wav")
        add_field("model", _MODEL)
        add_field("response_format", "verbose_json")
        if language:
            add_field("language", language)

        parts.append(f"--{boundary}--\r\n".encode())
        return b"".join(parts)

    def _parse_response(self, data: dict) -> TranscriptionResult:
        segments = []
        for seg in data.get("segments", []):
            segments.append(
                TranscriptionSegment(
                    text=seg.get("text", ""),
                    start=seg.get("start", 0.0),
                    end=seg.get("end", 0.0),
                    confidence=seg.get("avg_logprob"),
                )
            )

        return TranscriptionResult(
            text=data.get("text", "").strip(),
            segments=segments,
            language=data.get("language", ""),
            duration=data.get("duration"),
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
