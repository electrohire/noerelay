"""Server-Sent Events streaming for the NoeRelay gateway (EPR-API-004).

The gateway serves ``POST /v1/chat/completions`` and ``POST /v1/responses``
over a ``ThreadingHTTPServer``.  Because ``BaseHTTPRequestHandler`` exposes a
plain socket write path, streaming is implemented by running the inference
pipeline to completion first (so the evidence receipt is already issued) and
then writing the response body out as a sequence of SSE ``data:`` frames.

Every stream terminates with a metadata chunk whose ``epr`` block preserves
route identity and makes the evidence receipt discoverable at
``GET /v1/epr/runs/{run_id}``.
"""

from __future__ import annotations

import json
import re
import time
import uuid
from dataclasses import dataclass
from typing import Any

from .render import render_stream_chunk, render_stream_error_chunk

_WORD_TOKEN = re.compile(r"\S+\s*")


@dataclass
class StreamResponse:
    """Marker returned by handlers when the response should be streamed as SSE."""

    chunks: list[dict[str, Any]]


class SSEStreamer:
    """EPR-API-004: Server-Sent Events streaming with route identity."""

    @staticmethod
    def format_chunk(data: dict[str, Any]) -> str:
        """Format a dict as an SSE ``data:`` line."""
        return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"

    @staticmethod
    def format_done() -> str:
        """Format the SSE terminator."""
        return "data: [DONE]\n\n"

    @staticmethod
    def chunk_content(content: str, chunk_size: int = 5) -> list[str]:
        """Split content into word-level chunks for streaming simulation.

        Chunks are produced on whitespace boundaries and, when concatenated,
        reproduce the original content (excluding any leading whitespace).
        """
        if not content:
            return []
        tokens = _WORD_TOKEN.findall(content)
        return [
            "".join(tokens[index : index + chunk_size])
            for index in range(0, len(tokens), chunk_size)
        ]

    @staticmethod
    def build_stream_chunks(
        run_id: str, content: str, epr_metadata: dict[str, Any]
    ) -> list[dict[str, Any]]:
        """Build the full sequence of SSE chunk dicts.

        The sequence is: role chunk, content chunks, finish_reason chunk, and
        the terminal ``epr`` metadata chunk.  ``run_id`` is retained for
        signature compatibility; the stream identifier is a ``chatcmpl-`` id.
        """
        del run_id  # reserved for future use
        stream_id = f"chatcmpl-{uuid.uuid4().hex}"
        created = int(time.time())

        chunks: list[dict[str, Any]] = [
            render_stream_chunk(
                stream_id=stream_id,
                created=created,
                delta={"role": "assistant"},
                finish_reason=None,
            )
        ]
        for piece in SSEStreamer.chunk_content(content):
            chunks.append(
                render_stream_chunk(
                    stream_id=stream_id,
                    created=created,
                    delta={"content": piece},
                    finish_reason=None,
                )
            )
        chunks.append(
            render_stream_chunk(
                stream_id=stream_id,
                created=created,
                delta={},
                finish_reason="stop",
            )
        )
        chunks.append(
            render_stream_chunk(
                stream_id=stream_id,
                created=created,
                choices=[],
                epr=epr_metadata,
            )
        )
        return chunks

    @staticmethod
    def build_error_stream_chunk(
        error: dict[str, Any], epr: dict[str, Any]
    ) -> dict[str, Any]:
        """Build an error chunk for streaming."""
        return render_stream_error_chunk(error, epr)
