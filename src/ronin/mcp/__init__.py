"""MCP, both directions: Ronin as a client of other servers, and as a server itself.

JSON-RPC 2.0 is implemented here over the standard library rather than pulled in as
a dependency. It is a few hundred lines, it is exactly testable offline, and the
alternative — the ``mcp`` SDK — would be a hard dependency on the import path of
every session for a protocol whose whole surface is five methods.

Layout:

* :mod:`~ronin.mcp.jsonrpc` — messages, the codec, and the error-code table. The
  codes are the part worth getting right: a client that reads ``-32602`` as
  ``-32603`` retries a malformed call forever.
* :mod:`~ronin.mcp.transport` — three framings (stdio, SSE, HTTP) behind one
  ``Transport`` protocol, plus an SSE reader that reassembles frames split at any
  byte boundary. Nothing opens a socket unless you hand it a sender.
* :mod:`~ronin.mcp.config` — ``.ronin/mcp.json``, validated strictly, with every
  message naming the offending server and key.
* :mod:`~ronin.mcp.tools` — the ``mcp__<server>__<tool>`` namespace and the
  wrapper that makes a remote tool an ordinary ``ronin.tools.base.Tool``.
* :mod:`~ronin.mcp.client` — the handshake, capability negotiation, and the
  degradation path for a server that dies mid-session.
* :mod:`~ronin.mcp.server` — Ronin's own tools over MCP, plus ``ronin_task``,
  which runs a nested agent loop. Read its module docstring for the trust model.

The three rules that carry the most weight:

1. **A collision between two servers' tools is impossible by construction.** A
   server name may not contain the ``__`` separator and server names are JSON
   object keys, so ``mcp__<server>__<tool>`` is unique and parses back
   unambiguously. There is no de-duplication pass, because there is nothing to
   de-duplicate.
2. **An MCP tool never defaults to un-gated.** A server that does not declare a
   danger level is treated as ``MUTATING`` and requiring approval, and a server's
   own annotations may raise that but never lower it. Waiving the gate takes two
   explicit keys in the config.
3. **A dead server does not end the session.** Its tools stay in the registry and
   return ``ToolResult(ok=False)`` with a message telling the model what is gone
   and what to do instead, after one bounded reconnect attempt. The rest of the
   session — including other servers' tools — keeps working.

Nothing here imports ``ronin.providers``. The network transports take an injected
sender and the nested-agent runner is an injected callable, which is what keeps
this package on the ``core`` + ``tools`` side of the dependency boundary.
"""

from __future__ import annotations

from .client import (
    CLIENT_NAME,
    CLIENT_VERSION_UNKNOWN,
    DEFAULT_MAX_RECONNECTS,
    MAX_TOOL_LIST_PAGES,
    PROTOCOL_VERSION,
    SUPPORTED_PROTOCOL_VERSIONS,
    McpClient,
    McpToolset,
    ServerCapabilities,
    TokenRefresher,
    TransportProvider,
    connect_all,
    default_transport_provider,
    render_content,
)
from .config import (
    MCP_CONFIG_RELATIVE_PATH,
    SERVERS_KEY,
    UNDECLARED_DANGER_LEVEL,
    AuthKind,
    ConfigError,
    McpServerConfig,
    TransportKind,
    enabled_servers,
    load_mcp_config,
    parse_mcp_config,
)
from .jsonrpc import (
    APPROVAL_REQUIRED,
    INTERNAL_ERROR,
    INVALID_PARAMS,
    INVALID_REQUEST,
    JSONRPC_VERSION,
    METHOD_NOT_FOUND,
    PARSE_ERROR,
    SERVER_ERROR_RANGE,
    TOOL_EXECUTION_FAILED,
    JsonRpcError,
    McpError,
    ProtocolError,
    Request,
    Response,
    TransportClosed,
    encode,
    error_response,
    is_retryable,
    parse_request,
    parse_response,
)
from .oauth import (
    PKCE_METHOD,
    AuthServerMetadata,
    ClientRegistration,
    OAuthError,
    PkcePair,
    ProtectedResourceMetadata,
    TokenRequest,
    TokenSet,
    authorization_code_request,
    authorization_url,
    parse_redirect,
    parse_www_authenticate,
    pkce_from_verifier,
    refresh_request,
    registration_request,
)
from .oauth_driver import (
    DEFAULT_REDIRECT_URI,
    Authorizer,
    AuthStore,
    FileAuthStore,
    HttpFetcher,
    HttpReply,
    HttpxFetcher,
    InMemoryAuthStore,
    LoopbackAuthorizer,
    OAuthDriver,
    PersistedAuth,
    ResolvedAuth,
    authorize_servers,
    default_oauth_driver,
    token_refresher,
)
from .server import (
    DEFAULT_EXPOSED_TOOLS,
    DEFAULT_SERVER_VERSION,
    RONIN_TASK,
    SERVER_NAME,
    Approver,
    McpServer,
    NestedRunner,
    RoninTaskTool,
    render_call,
    serve_stdio,
)
from .tools import (
    MAX_TOOL_NAME_CHARS,
    NAME_SEPARATOR,
    NAMESPACE_PREFIX,
    NamingError,
    RemoteInvoke,
    RemoteTool,
    adapt_description,
    danger_for,
    extend_registry,
    is_namespaced,
    namespaced_name,
    remote_tools,
    spec_for,
    split_namespaced_name,
)
from .transport import (
    DEFAULT_TIMEOUT_SECONDS,
    MAX_FRAME_BYTES,
    ByteReader,
    ByteWriter,
    HttpSender,
    HttpTransport,
    HttpxSender,
    PipeWriter,
    SseFrame,
    SseFrameReader,
    SseTransport,
    StdioTransport,
    StreamPair,
    Transport,
    TransportTimeout,
    Unauthorized,
    frames_of,
    memory_duplex,
    read_frame,
    sse_responses,
)

__all__ = [
    "APPROVAL_REQUIRED",
    "CLIENT_NAME",
    "CLIENT_VERSION_UNKNOWN",
    "DEFAULT_EXPOSED_TOOLS",
    "DEFAULT_MAX_RECONNECTS",
    "DEFAULT_REDIRECT_URI",
    "DEFAULT_SERVER_VERSION",
    "DEFAULT_TIMEOUT_SECONDS",
    "INTERNAL_ERROR",
    "INVALID_PARAMS",
    "INVALID_REQUEST",
    "JSONRPC_VERSION",
    "MAX_FRAME_BYTES",
    "MAX_TOOL_LIST_PAGES",
    "MAX_TOOL_NAME_CHARS",
    "MCP_CONFIG_RELATIVE_PATH",
    "METHOD_NOT_FOUND",
    "NAMESPACE_PREFIX",
    "NAME_SEPARATOR",
    "PARSE_ERROR",
    "PKCE_METHOD",
    "PROTOCOL_VERSION",
    "RONIN_TASK",
    "SERVERS_KEY",
    "SERVER_ERROR_RANGE",
    "SERVER_NAME",
    "SUPPORTED_PROTOCOL_VERSIONS",
    "TOOL_EXECUTION_FAILED",
    "UNDECLARED_DANGER_LEVEL",
    "Approver",
    "AuthKind",
    "AuthServerMetadata",
    "AuthStore",
    "Authorizer",
    "ByteReader",
    "ByteWriter",
    "ClientRegistration",
    "ConfigError",
    "FileAuthStore",
    "HttpFetcher",
    "HttpReply",
    "HttpSender",
    "HttpTransport",
    "HttpxFetcher",
    "HttpxSender",
    "InMemoryAuthStore",
    "JsonRpcError",
    "LoopbackAuthorizer",
    "McpClient",
    "McpError",
    "McpServer",
    "McpServerConfig",
    "McpToolset",
    "NamingError",
    "NestedRunner",
    "OAuthDriver",
    "OAuthError",
    "PersistedAuth",
    "PipeWriter",
    "PkcePair",
    "ProtectedResourceMetadata",
    "ProtocolError",
    "RemoteInvoke",
    "RemoteTool",
    "Request",
    "ResolvedAuth",
    "Response",
    "RoninTaskTool",
    "ServerCapabilities",
    "SseFrame",
    "SseFrameReader",
    "SseTransport",
    "StdioTransport",
    "StreamPair",
    "TokenRefresher",
    "TokenRequest",
    "TokenSet",
    "Transport",
    "TransportClosed",
    "TransportKind",
    "TransportProvider",
    "TransportTimeout",
    "Unauthorized",
    "adapt_description",
    "authorization_code_request",
    "authorization_url",
    "authorize_servers",
    "connect_all",
    "danger_for",
    "default_oauth_driver",
    "default_transport_provider",
    "enabled_servers",
    "encode",
    "error_response",
    "extend_registry",
    "frames_of",
    "is_namespaced",
    "is_retryable",
    "load_mcp_config",
    "memory_duplex",
    "namespaced_name",
    "parse_mcp_config",
    "parse_redirect",
    "parse_request",
    "parse_response",
    "parse_www_authenticate",
    "pkce_from_verifier",
    "read_frame",
    "refresh_request",
    "registration_request",
    "remote_tools",
    "render_call",
    "render_content",
    "serve_stdio",
    "spec_for",
    "split_namespaced_name",
    "sse_responses",
    "token_refresher",
]
