"""Wire protocol shared by the relay and the connector.

There are two message types that travel over the connector websocket:

  RelayRequest   relay  -> connector   "please call the local target with this"
  ConnectorReply connector -> relay     "here is what the local target returned"

They are correlated by a request id so the relay can match a reply to the
phone request that is still waiting. Everything is plain JSON so it can be
inspected easily and so the connector stays tiny.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

# Message kinds, kept as short strings on the wire.
KIND_REQUEST = "relay_request"
KIND_REPLY = "connector_reply"


class TaskRequest(BaseModel):
    """What the phone POSTs to the relay.

    This describes one call to make against the local Ronin gateway. The
    connector maps method/path/body onto an HTTP call to its configured
    target_url. There is no host or command field, on purpose: the phone
    cannot redirect the connector anywhere.
    """

    method: str = Field(default="POST")
    path: str = Field(default="/")
    headers: dict[str, str] = Field(default_factory=dict)
    # JSON body to send to the local target. Kept as an arbitrary JSON value.
    body: Any = None


class RelayRequest(BaseModel):
    """Sent down the websocket from relay to connector."""

    kind: str = Field(default=KIND_REQUEST)
    id: str
    request: TaskRequest


class ConnectorReply(BaseModel):
    """Sent back up the websocket from connector to relay."""

    kind: str = Field(default=KIND_REPLY)
    id: str
    status: int
    headers: dict[str, str] = Field(default_factory=dict)
    body: Any = None
    # Set when the connector could not reach the local target at all.
    error: str | None = None


def encode(message: BaseModel) -> str:
    """Serialize a protocol message to a JSON string for the websocket."""
    return message.model_dump_json()
