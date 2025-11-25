"""Persistence helpers for agent conversation history."""

from __future__ import annotations

from datetime import datetime
from typing import Iterable, List, Optional, Sequence

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import AsyncSessionLocal
from ..models import (
    ConversationMessage,
    ConversationMessageCreate,
    ConversationMessageResponse,
    ConversationRole,
    ConversationSource,
)
from ..utils.time import utc_now


async def save_conversation_messages(
    messages: Sequence[ConversationMessageCreate],
    *,
    session: Optional[AsyncSession] = None,
) -> List[ConversationMessage]:
    """Persist one or more conversation messages.

    Args:
        messages: Sequence of message payloads to store.
        session: Optional existing session; if omitted a new session is created.

    Returns:
        List of ConversationMessage instances saved (ordered by timestamp ascending).
    """

    if not messages:
        return []

    own_session = False
    if session is None:
        session = AsyncSessionLocal()
        own_session = True

    try:
        orm_messages: List[ConversationMessage] = []
        for payload in messages:
            data = payload.model_dump()
            timestamp = data.get("timestamp") or utc_now()
            message = ConversationMessage(
                timestamp=timestamp,
                source=ConversationSource(data["source"]),
                role=ConversationRole(data["role"]),
                content=data["content"],
                rule_id=data.get("rule_id"),
                rule_name=data.get("rule_name"),
                tool_calls=data.get("tool_calls"),
                message_meta=data.get("message_meta"),
            )
            session.add(message)
            orm_messages.append(message)

        await session.flush()
        if own_session:
            await session.commit()
        else:
            await session.flush()

        # Ensure all ORM instances have primary keys populated
        for message in orm_messages:
            await session.refresh(message)

        # Return in chronological order
        return sorted(orm_messages, key=lambda msg: msg.timestamp)
    finally:
        if own_session:
            await session.close()


async def _run_history_query(stmt: Select[ConversationMessage], *, session: Optional[AsyncSession] = None) -> List[ConversationMessage]:
    own_session = False
    if session is None:
        session = AsyncSessionLocal()
        own_session = True

    try:
        result = await session.execute(stmt)
        rows = result.scalars().all()
        return list(rows)
    finally:
        if own_session:
            await session.close()


async def get_conversation_messages(
    *,
    limit: int = 100,
    since: Optional[datetime] = None,
    source: Optional[str] = None,
    session: Optional[AsyncSession] = None,
) -> List[ConversationMessage]:
    """Return conversation messages ordered chronologically.

    Args:
        limit: Maximum number of messages to return.
        since: If provided, only messages with timestamp greater than this are returned.
        source: Optional ConversationSource value to filter on.
        session: Optional existing DB session.
    """

    stmt = select(ConversationMessage)
    if since is not None:
        stmt = stmt.where(ConversationMessage.timestamp > since)
    if source is not None:
        stmt = stmt.where(ConversationMessage.source == ConversationSource(source))

    stmt = stmt.order_by(ConversationMessage.timestamp.desc()).limit(limit)

    rows = await _run_history_query(stmt, session=session)
    return list(reversed(rows))


async def get_recent_automated_highlights(
    *,
    limit: int = 5,
    session: Optional[AsyncSession] = None,
) -> List[ConversationMessage]:
    """Return the most recent automated assistant messages for dashboard highlights."""

    stmt = (
        select(ConversationMessage)
        .where(
            ConversationMessage.source == ConversationSource.AUTOMATED,
            ConversationMessage.role == ConversationRole.ASSISTANT,
        )
        .order_by(ConversationMessage.timestamp.desc())
        .limit(limit)
    )

    rows = await _run_history_query(stmt, session=session)
    return list(reversed(rows))


def to_conversation_response(message: ConversationMessage) -> ConversationMessageResponse:
    """Convert ORM instance to Pydantic response."""

    return ConversationMessageResponse(
        id=message.id,
        timestamp=message.timestamp,
        created_at=message.created_at,
        source=message.source.value,
        role=message.role.value,
        content=message.content,
        rule_id=message.rule_id,
        rule_name=message.rule_name,
        tool_calls=message.tool_calls,
        message_meta=message.message_meta,
    )






