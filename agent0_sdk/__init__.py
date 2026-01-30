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
    from .core.transaction_handle import TransactionHandle, TransactionMined
    _sdk_available = True
except ImportError:
    SDK = None
    Agent = None
    TransactionHandle = None
    TransactionMined = None
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

# SpendingWallet for safe x402 payments
from .core.spending_wallet import (
    SpendingWallet,
    SpendingWalletConfig,
    SpendingRecord,
    SpendingPolicyError,
    SpendingLimitExceededError,
    EndpointNotWhitelistedError,
    DuplicatePaymentError,
    CircuitBreakerTrippedError,
)

# SpendingPolicy for standalone policy enforcement
from .core.spending_policy import (
    SpendingPolicy,
    SpendingPolicyConfig,
    PolicyResult,
    PolicyViolationType,
)

# SpendingTracker for persistent history
from .core.spending_tracker import (
    SpendingTracker,
    TrackedPayment,
)

# Wallet derivation utilities
from .core.wallet_derivation import (
    generate_seed_phrase,
    derive_spending_wallet,
    validate_seed_phrase,
    generate_spending_wallet,
    derive_multiple_wallets,
    get_derivation_path,
    private_key_to_address,
    AGENT0_DERIVATION_PATH,
)

__version__ = "1.4.2"
__all__ = [
    "SDK",
    "Agent",
    "TransactionHandle",
    "TransactionMined",
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
    # SpendingWallet system
    "SpendingWallet",
    "SpendingWalletConfig",
    "SpendingRecord",
    "SpendingPolicyError",
    "SpendingLimitExceededError",
    "EndpointNotWhitelistedError",
    "DuplicatePaymentError",
    "CircuitBreakerTrippedError",
    # SpendingPolicy
    "SpendingPolicy",
    "SpendingPolicyConfig",
    "PolicyResult",
    "PolicyViolationType",
    # SpendingTracker
    "SpendingTracker",
    "TrackedPayment",
    # Wallet derivation
    "generate_seed_phrase",
    "derive_spending_wallet",
    "validate_seed_phrase",
    "generate_spending_wallet",
    "derive_multiple_wallets",
    "get_derivation_path",
    "private_key_to_address",
    "AGENT0_DERIVATION_PATH",
]
