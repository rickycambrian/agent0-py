"""
Agent0 SDK - Python SDK for agent portability, discovery and trust based on ERC-8004.
"""

from .core.models import (
    AgentId,
    ChainId,
    Address,
    URI,
    CID,
    Timestamp,
    IdemKey,
    EndpointType,
    TrustModel,
    Endpoint,
    RegistrationFile,
    AgentSummary,
    Feedback,
    SearchParams,
    SearchFeedbackParams,
)

# Try to import SDK and Agent (may fail if web3 is not installed)
try:
    from .core.sdk import SDK
    from .core.agent import Agent
    _sdk_available = True
except ImportError:
    SDK = None
    Agent = None
    _sdk_available = False

# x402 payment support (optional - gracefully degrades if x402 package not installed)
try:
    from .core.x402_client import (
        X402Client,
        X402Config,
        X402PaymentError,
        X402PriceExceededError,
        X402PaymentDeclinedError,
        create_x402_client,
    )
    _x402_available = True
except ImportError:
    X402Client = None
    X402Config = None
    X402PaymentError = None
    X402PriceExceededError = None
    X402PaymentDeclinedError = None
    create_x402_client = None
    _x402_available = False

# MCP Client for runtime tool calls (always available, x402 is optional)
from .core.mcp_client import (
    MCPClient,
    MCPError,
    MCPToolCallError,
    MCPConnectionError,
    MCPPaymentRequiredError,
)

__version__ = "1.0.2"
__all__ = [
    "SDK",
    "Agent",
    "AgentId",
    "ChainId",
    "Address",
    "URI",
    "CID",
    "Timestamp",
    "IdemKey",
    "EndpointType",
    "TrustModel",
    "Endpoint",
    "RegistrationFile",
    "AgentSummary",
    "Feedback",
    "SearchParams",
    "SearchFeedbackParams",
    # x402 payment support
    "X402Client",
    "X402Config",
    "X402PaymentError",
    "X402PriceExceededError",
    "X402PaymentDeclinedError",
    "create_x402_client",
    # MCP runtime client
    "MCPClient",
    "MCPError",
    "MCPToolCallError",
    "MCPConnectionError",
    "MCPPaymentRequiredError",
]
