"""ronin-relay: a self-hosted relay for remote Ronin gateway access.

A phone sends a task to a relay server you own (a free VM). A laptop runs a
connector that holds a persistent OUTBOUND websocket to that relay. The relay
forwards the phone request down that websocket to the connector, the connector
calls the local Ronin gateway, and the response travels back the same path.

The laptop never opens an inbound port. Every endpoint requires a shared token.
"""

__version__ = "0.1.0"
