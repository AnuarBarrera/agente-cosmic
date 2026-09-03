import hashlib
import json
import logging
import re
from dataclasses import dataclass
from typing import Any

from core.content_pipeline.models import GenerationAuditEvent

logger = logging.getLogger(__name__)

_PREVIEW_LIMIT = 500
_EMAIL_RE = re.compile(r'(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b')
_PHONE_RE = re.compile(r'(?<!\d)(?:\+?\d[\d\s().-]{7,}\d)(?!\d)')
_URL_RE = re.compile(r'(?i)https?://[^\s,;]+')
_SECRET_RE = re.compile(
    r'(?i)(api[_-]?key|authorization|bearer|password|secret|token)\s*[:=]\s*[^\s,;]+',
)


@dataclass(frozen=True)
class GenerationContext:
    job_id: str
    post_id: str | None = None
    asset_id: str | None = None
    day_number: int | None = None
    attempt: int = 1


def _text(value: Any) -> str:
    if value is None:
        return ''
    if not isinstance(value, str):
        value = json.dumps(value, ensure_ascii=False, default=str)
    return value


def content_hash(value: Any) -> str:
    raw = _text(value)
    return hashlib.sha256(raw.encode('utf-8')).hexdigest() if raw else ''


def safe_preview(value: Any) -> str:
    preview = _text(value)
    preview = _EMAIL_RE.sub('[EMAIL]', preview)
    preview = _PHONE_RE.sub('[PHONE]', preview)
    preview = _URL_RE.sub('[URL]', preview)
    preview = _SECRET_RE.sub(lambda match: f'{match.group(1)}=[REDACTED]', preview)
    return preview[:_PREVIEW_LIMIT]


def record_generation_event(
    context: GenerationContext | dict | None,
    *,
    stage: str,
    decision: str,
    flags: dict | None = None,
    prompt: Any = None,
    response: Any = None,
    duration_ms: int | None = None,
    provider: str = '',
    model: str = '',
):
    """Best-effort audit sink. Observability must not break generation."""
    if context is None:
        return None
    if isinstance(context, dict):
        context = GenerationContext(**context)
    try:
        event = GenerationAuditEvent.objects.create(
            job_id=context.job_id,
            post_id=context.post_id,
            reference_asset_id=context.asset_id,
            stage=stage[:100],
            attempt=context.attempt,
            decision=decision,
            flags=flags or {},
            prompt_hash=content_hash(prompt),
            response_hash=content_hash(response),
            prompt_preview=safe_preview(prompt),
            response_preview=safe_preview(response),
            duration_ms=duration_ms,
            provider=provider[:50],
            model=model[:150],
        )
        logger.info(
            'generation_audit_event',
            extra={
                'job_id': context.job_id, 'post_id': context.post_id,
                'asset_id': context.asset_id, 'day_number': context.day_number,
                'attempt': context.attempt, 'stage': stage, 'decision': decision,
            },
        )
        return event
    except Exception:
        logger.exception('No se pudo persistir GenerationAuditEvent stage=%s', stage)
        return None
