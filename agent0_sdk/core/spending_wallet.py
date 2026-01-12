"""
SpendingWallet - Safe Spending Wallet for x402 Payments

A dedicated wallet system for making x402 payments with built-in safety controls:
- Isolation: Separate wallet from main funds
- Defense in Depth: Multiple limit layers (per-tx, daily, weekly, session)
- Deduplication: Prevent duplicate payments
- Whitelist: Only pay approved endpoints
- Circuit Breaker: Auto-halt on anomalies
- Auditability: Full spending history
- Recoverability: HD derivation from seed phrase

Example usage:

    from agent0_sdk import SpendingWallet, SpendingWalletConfig

    # Create with HD derivation from seed phrase
    config = SpendingWalletConfig(
        seed_phrase="your twelve word seed phrase here...",
        derivation_index=0,
        max_per_transaction=0.10,
        max_per_day=5.0,
        max_per_week=20.0,
    )
    wallet = SpendingWallet.create(config)

    # Fund the wallet
    wallet.fund(1.0, from_wallet="0x...")

    # Get x402 client for making payments
    x402 = wallet.get_x402_client()
    response = x402.post("https://api.example.com/", json=data)

    # Check spending
    print(f"Today: ${wallet.get_daily_spending()}")
    print(f"This week: ${wallet.get_weekly_spending()}")
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple, TYPE_CHECKING

if TYPE_CHECKING:
    from .x402_client import X402Client

logger = logging.getLogger(__name__)


# Default configuration values
DEFAULT_MAX_PER_TRANSACTION = 0.10  # USD
DEFAULT_MAX_PER_DAY = 5.0  # USD
DEFAULT_MAX_PER_WEEK = 20.0  # USD
DEFAULT_DEDUPLICATION_WINDOW = 60  # seconds
DEFAULT_CIRCUIT_BREAKER_THRESHOLD = 10  # failures before halt
DEFAULT_REQUIRE_CONFIRMATION_ABOVE = 0.50  # USD


@dataclass
class SpendingRecord:
    """Record of a single payment."""

    timestamp: float
    amount: float
    endpoint: str
    tx_hash: Optional[str] = None
    method: Optional[str] = None
    tool_name: Optional[str] = None
    success: bool = True
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for storage."""
        return {
            "timestamp": self.timestamp,
            "amount": self.amount,
            "endpoint": self.endpoint,
            "tx_hash": self.tx_hash,
            "method": self.method,
            "tool_name": self.tool_name,
            "success": self.success,
            "error": self.error,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SpendingRecord":
        """Create from dictionary."""
        return cls(
            timestamp=data["timestamp"],
            amount=data["amount"],
            endpoint=data["endpoint"],
            tx_hash=data.get("tx_hash"),
            method=data.get("method"),
            tool_name=data.get("tool_name"),
            success=data.get("success", True),
            error=data.get("error"),
        )


@dataclass
class SpendingWalletConfig:
    """Configuration for a safe spending wallet."""

    # Wallet derivation (one of these required)
    seed_phrase: Optional[str] = None  # BIP-39 mnemonic
    derivation_index: int = 0  # m/44'/60'/8004'/0/{index}
    private_key: Optional[str] = None  # Direct key (if not using HD)

    # Spending limits
    max_per_transaction: float = DEFAULT_MAX_PER_TRANSACTION
    max_per_day: float = DEFAULT_MAX_PER_DAY
    max_per_week: float = DEFAULT_MAX_PER_WEEK
    max_per_session: Optional[float] = None

    # Security controls
    endpoint_whitelist: Optional[List[str]] = None  # Allowed domains
    require_confirmation_above: float = DEFAULT_REQUIRE_CONFIRMATION_ABOVE
    confirmation_callback: Optional[Callable[[Dict], bool]] = None

    # Deduplication
    deduplication_window_seconds: int = DEFAULT_DEDUPLICATION_WINDOW

    # Circuit breaker
    circuit_breaker_threshold: int = DEFAULT_CIRCUIT_BREAKER_THRESHOLD
    circuit_breaker_window_seconds: int = 300  # 5 minutes

    # Persistence
    storage_path: Optional[str] = None  # Path for spending history

    # Network
    preferred_network: str = "base-sepolia"


class SpendingPolicyError(Exception):
    """Raised when a spending policy is violated."""

    pass


class SpendingLimitExceededError(SpendingPolicyError):
    """Raised when a spending limit is exceeded."""

    pass


class EndpointNotWhitelistedError(SpendingPolicyError):
    """Raised when endpoint is not in whitelist."""

    pass


class DuplicatePaymentError(SpendingPolicyError):
    """Raised when duplicate payment detected."""

    pass


class CircuitBreakerTrippedError(SpendingPolicyError):
    """Raised when circuit breaker has tripped."""

    pass


class SpendingWallet:
    """
    Safe spending wallet for x402 payments.

    Provides a dedicated wallet with built-in safety controls for making
    x402 payments without risking main funds.
    """

    def __init__(
        self,
        private_key: str,
        config: SpendingWalletConfig,
        address: Optional[str] = None,
    ):
        """
        Initialize spending wallet.

        Use SpendingWallet.create() or SpendingWallet.from_private_key() instead.

        Args:
            private_key: Wallet private key
            config: Spending configuration
            address: Wallet address (derived if not provided)
        """
        self._private_key = private_key
        self.config = config
        self._address = address

        # Derive address if not provided
        if not self._address:
            self._address = self._derive_address(private_key)

        # Initialize state
        self._spending_history: List[SpendingRecord] = []
        self._session_spending: float = 0.0
        self._failure_count: int = 0
        self._failure_window_start: float = 0.0
        self._recent_payments: List[Tuple[float, str, float]] = []  # (time, endpoint, amount)

        # Load persisted history
        if config.storage_path:
            self._load_history()

        logger.info(
            "SpendingWallet initialized: address=%s, max_tx=$%.2f, max_day=$%.2f",
            self._address,
            config.max_per_transaction,
            config.max_per_day,
        )

    @classmethod
    def create(cls, config: SpendingWalletConfig) -> "SpendingWallet":
        """
        Create a new spending wallet.

        If seed_phrase is provided, derives wallet using HD derivation.
        If private_key is provided, uses it directly.
        If neither is provided, generates a new wallet.

        Args:
            config: Spending wallet configuration

        Returns:
            Configured SpendingWallet instance

        Example:
            config = SpendingWalletConfig(
                seed_phrase="your twelve word seed phrase...",
                max_per_transaction=0.10,
                max_per_day=5.0,
            )
            wallet = SpendingWallet.create(config)
        """
        if config.seed_phrase:
            # HD derivation from seed phrase
            from .wallet_derivation import derive_spending_wallet

            private_key, address = derive_spending_wallet(
                config.seed_phrase, config.derivation_index
            )
            return cls(private_key, config, address)

        elif config.private_key:
            # Use provided private key
            return cls(config.private_key, config)

        else:
            # Generate new wallet
            from .wallet_derivation import generate_spending_wallet

            private_key, address = generate_spending_wallet()
            logger.warning(
                "Generated new spending wallet. Save this address: %s", address
            )
            return cls(private_key, config, address)

    @classmethod
    def from_private_key(
        cls, private_key: str, config: Optional[SpendingWalletConfig] = None
    ) -> "SpendingWallet":
        """
        Import existing wallet with safety controls.

        Args:
            private_key: Wallet private key (with or without 0x prefix)
            config: Optional spending configuration (uses defaults if not provided)

        Returns:
            SpendingWallet instance

        Example:
            wallet = SpendingWallet.from_private_key(
                "0x...",
                SpendingWalletConfig(max_per_day=10.0)
            )
        """
        if config is None:
            config = SpendingWalletConfig(private_key=private_key)
        else:
            config.private_key = private_key

        return cls(private_key, config)

    def _derive_address(self, private_key: str) -> str:
        """Derive address from private key."""
        try:
            from eth_account import Account

            account = Account.from_key(private_key)
            return account.address
        except ImportError:
            logger.warning(
                "eth_account not installed, cannot derive address from private key"
            )
            return "unknown"

    def get_address(self) -> str:
        """Get wallet address."""
        return self._address

    def get_private_key(self) -> str:
        """Get wallet private key."""
        return self._private_key

    def get_balance(
        self, token: str = "USDC", network: Optional[str] = None
    ) -> Optional[float]:
        """
        Check current token balance.

        Args:
            token: Token symbol (default: USDC)
            network: Network to check (default: from config)

        Returns:
            Balance in token units, or None if unable to check
        """
        network = network or self.config.preferred_network

        try:
            from web3 import Web3

            # Network RPC URLs
            rpc_urls = {
                "base-sepolia": "https://sepolia.base.org",
                "base": "https://mainnet.base.org",
            }

            # USDC contract addresses
            usdc_addresses = {
                "base-sepolia": "0x036CbD53842c5426634e7929541eC2318f3dCF7e",
                "base": "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
            }

            if network not in rpc_urls:
                logger.warning("Unknown network: %s", network)
                return None

            w3 = Web3(Web3.HTTPProvider(rpc_urls[network]))

            if token.upper() == "USDC":
                # ERC20 balanceOf ABI
                abi = [
                    {
                        "constant": True,
                        "inputs": [{"name": "_owner", "type": "address"}],
                        "name": "balanceOf",
                        "outputs": [{"name": "balance", "type": "uint256"}],
                        "type": "function",
                    }
                ]
                contract_address = usdc_addresses.get(network)
                if not contract_address:
                    return None

                contract = w3.eth.contract(
                    address=Web3.to_checksum_address(contract_address), abi=abi
                )
                balance_raw = contract.functions.balanceOf(
                    Web3.to_checksum_address(self._address)
                ).call()
                # USDC has 6 decimals
                return balance_raw / 1_000_000

            elif token.upper() == "ETH":
                balance_raw = w3.eth.get_balance(
                    Web3.to_checksum_address(self._address)
                )
                return balance_raw / 1e18

            return None

        except ImportError:
            logger.warning("web3 not installed, cannot check balance")
            return None
        except Exception as e:
            logger.warning("Failed to check balance: %s", e)
            return None

    def get_x402_client(self) -> "X402Client":
        """
        Get x402 client configured with this spending wallet.

        Returns:
            X402Client with spending policy enforcement

        Example:
            x402 = wallet.get_x402_client()
            response = x402.post("https://api.example.com/", json=data)
        """
        from .x402_client import X402Client, X402Config

        config = X402Config(
            private_key=self._private_key,
            max_price_per_request=self.config.max_per_transaction,
            auto_pay=True,  # Policy handles approval
            preferred_network=self.config.preferred_network,
            session_spending_limit=self.config.max_per_session,
            payment_approval_callback=self._payment_approval_callback,
        )

        return X402Client(config)

    def _payment_approval_callback(self, payment_details: Dict) -> bool:
        """
        Internal callback for x402 client payment approval.

        Validates against all spending policies before approving.
        """
        # Extract price
        price = self._extract_price(payment_details)
        endpoint = payment_details.get("endpoint", "")

        try:
            self.validate_payment(price, endpoint)
            return True
        except SpendingPolicyError as e:
            logger.warning("Payment rejected by policy: %s", e)
            return False

    def _extract_price(self, payment_details: Dict) -> float:
        """Extract price from payment details."""
        price = payment_details.get("price") or payment_details.get("amount", 0)
        if isinstance(price, dict):
            price = price.get("amount", 0)
        return float(price) if price else 0.0

    def validate_payment(self, amount: float, endpoint: str) -> bool:
        """
        Validate a payment against all policies.

        Args:
            amount: Payment amount in USD
            endpoint: Target endpoint URL

        Returns:
            True if payment should proceed

        Raises:
            SpendingLimitExceededError: If any limit exceeded
            EndpointNotWhitelistedError: If endpoint not whitelisted
            DuplicatePaymentError: If duplicate detected
            CircuitBreakerTrippedError: If circuit breaker active
        """
        # Check circuit breaker
        if self._is_circuit_breaker_tripped():
            raise CircuitBreakerTrippedError(
                f"Circuit breaker tripped after {self.config.circuit_breaker_threshold} "
                "failures. Reset manually with reset_circuit_breaker()."
            )

        # Check endpoint whitelist
        if self.config.endpoint_whitelist:
            if not self._is_endpoint_whitelisted(endpoint):
                raise EndpointNotWhitelistedError(
                    f"Endpoint '{endpoint}' is not in whitelist. "
                    f"Allowed: {self.config.endpoint_whitelist}"
                )

        # Check per-transaction limit
        if amount > self.config.max_per_transaction:
            raise SpendingLimitExceededError(
                f"Amount ${amount:.4f} exceeds per-transaction limit "
                f"${self.config.max_per_transaction:.2f}"
            )

        # Check daily limit
        daily_spending = self.get_daily_spending()
        if daily_spending + amount > self.config.max_per_day:
            raise SpendingLimitExceededError(
                f"Payment would exceed daily limit. "
                f"Today: ${daily_spending:.2f}, Request: ${amount:.4f}, "
                f"Limit: ${self.config.max_per_day:.2f}"
            )

        # Check weekly limit
        weekly_spending = self.get_weekly_spending()
        if weekly_spending + amount > self.config.max_per_week:
            raise SpendingLimitExceededError(
                f"Payment would exceed weekly limit. "
                f"This week: ${weekly_spending:.2f}, Request: ${amount:.4f}, "
                f"Limit: ${self.config.max_per_week:.2f}"
            )

        # Check session limit
        if self.config.max_per_session is not None:
            if self._session_spending + amount > self.config.max_per_session:
                raise SpendingLimitExceededError(
                    f"Payment would exceed session limit. "
                    f"Session: ${self._session_spending:.2f}, Request: ${amount:.4f}, "
                    f"Limit: ${self.config.max_per_session:.2f}"
                )

        # Check for duplicate payment
        if self._is_duplicate_payment(endpoint, amount):
            raise DuplicatePaymentError(
                f"Duplicate payment detected for endpoint '{endpoint}' "
                f"with amount ${amount:.4f} within {self.config.deduplication_window_seconds}s"
            )

        # Check if requires confirmation
        if amount > self.config.require_confirmation_above:
            if self.config.confirmation_callback:
                confirmed = self.config.confirmation_callback(
                    {
                        "amount": amount,
                        "endpoint": endpoint,
                        "daily_spending": daily_spending,
                        "weekly_spending": weekly_spending,
                    }
                )
                if not confirmed:
                    raise SpendingLimitExceededError(
                        f"Payment of ${amount:.4f} requires confirmation "
                        "but was declined."
                    )
            else:
                logger.warning(
                    "Payment of $%.4f exceeds confirmation threshold $%.2f "
                    "but no confirmation_callback configured",
                    amount,
                    self.config.require_confirmation_above,
                )

        return True

    def _is_endpoint_whitelisted(self, endpoint: str) -> bool:
        """Check if endpoint is in whitelist."""
        if not self.config.endpoint_whitelist:
            return True

        for allowed in self.config.endpoint_whitelist:
            if allowed in endpoint:
                return True
        return False

    def _is_duplicate_payment(self, endpoint: str, amount: float) -> bool:
        """Check for duplicate payment within deduplication window."""
        current_time = time.time()
        cutoff = current_time - self.config.deduplication_window_seconds

        # Clean old entries
        self._recent_payments = [
            (t, e, a) for t, e, a in self._recent_payments if t > cutoff
        ]

        # Check for duplicate
        for t, e, a in self._recent_payments:
            if e == endpoint and abs(a - amount) < 0.0001:  # Same endpoint and amount
                return True

        return False

    def _is_circuit_breaker_tripped(self) -> bool:
        """Check if circuit breaker is active."""
        current_time = time.time()

        # Reset if outside window
        if (
            current_time - self._failure_window_start
            > self.config.circuit_breaker_window_seconds
        ):
            self._failure_count = 0
            self._failure_window_start = current_time

        return self._failure_count >= self.config.circuit_breaker_threshold

    def record_payment(
        self,
        amount: float,
        endpoint: str,
        tx_hash: Optional[str] = None,
        method: Optional[str] = None,
        tool_name: Optional[str] = None,
    ) -> None:
        """
        Record a successful payment.

        Args:
            amount: Payment amount in USD
            endpoint: Target endpoint
            tx_hash: Transaction hash (if available)
            method: Request method (e.g., "tools/call")
            tool_name: Tool name (if applicable)
        """
        record = SpendingRecord(
            timestamp=time.time(),
            amount=amount,
            endpoint=endpoint,
            tx_hash=tx_hash,
            method=method,
            tool_name=tool_name,
            success=True,
        )

        self._spending_history.append(record)
        self._session_spending += amount
        self._recent_payments.append((time.time(), endpoint, amount))

        # Persist if configured
        if self.config.storage_path:
            self._save_history()

        logger.info(
            "Payment recorded: $%.4f to %s (total today: $%.2f)",
            amount,
            endpoint,
            self.get_daily_spending(),
        )

    def record_failure(self, endpoint: str, error: str) -> None:
        """
        Record a payment failure.

        Args:
            endpoint: Target endpoint
            error: Error message
        """
        # Update circuit breaker
        current_time = time.time()
        if (
            current_time - self._failure_window_start
            > self.config.circuit_breaker_window_seconds
        ):
            self._failure_count = 1
            self._failure_window_start = current_time
        else:
            self._failure_count += 1

        # Record in history
        record = SpendingRecord(
            timestamp=current_time,
            amount=0.0,
            endpoint=endpoint,
            success=False,
            error=error,
        )
        self._spending_history.append(record)

        if self.config.storage_path:
            self._save_history()

        logger.warning(
            "Payment failure recorded: %s (failures: %d/%d)",
            error,
            self._failure_count,
            self.config.circuit_breaker_threshold,
        )

    def reset_circuit_breaker(self) -> None:
        """Reset the circuit breaker."""
        self._failure_count = 0
        self._failure_window_start = time.time()
        logger.info("Circuit breaker reset")

    def get_daily_spending(self) -> float:
        """Get total spending for today."""
        today_start = datetime.now().replace(
            hour=0, minute=0, second=0, microsecond=0
        ).timestamp()

        return sum(
            r.amount
            for r in self._spending_history
            if r.timestamp >= today_start and r.success
        )

    def get_weekly_spending(self) -> float:
        """Get total spending for this week."""
        week_start = (
            datetime.now() - timedelta(days=datetime.now().weekday())
        ).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()

        return sum(
            r.amount
            for r in self._spending_history
            if r.timestamp >= week_start and r.success
        )

    def get_session_spending(self) -> float:
        """Get total spending in current session."""
        return self._session_spending

    def get_spending_history(self, limit: int = 100) -> List[SpendingRecord]:
        """
        Get payment history.

        Args:
            limit: Maximum records to return

        Returns:
            List of SpendingRecord (most recent first)
        """
        return sorted(
            self._spending_history, key=lambda r: r.timestamp, reverse=True
        )[:limit]

    def get_stats(self) -> Dict[str, Any]:
        """
        Get spending statistics.

        Returns:
            Dictionary with spending stats
        """
        return {
            "address": self._address,
            "daily_spending": self.get_daily_spending(),
            "daily_limit": self.config.max_per_day,
            "daily_remaining": self.config.max_per_day - self.get_daily_spending(),
            "weekly_spending": self.get_weekly_spending(),
            "weekly_limit": self.config.max_per_week,
            "weekly_remaining": self.config.max_per_week - self.get_weekly_spending(),
            "session_spending": self._session_spending,
            "session_limit": self.config.max_per_session,
            "total_payments": len([r for r in self._spending_history if r.success]),
            "total_failures": len(
                [r for r in self._spending_history if not r.success]
            ),
            "circuit_breaker_failures": self._failure_count,
            "circuit_breaker_threshold": self.config.circuit_breaker_threshold,
            "circuit_breaker_tripped": self._is_circuit_breaker_tripped(),
        }

    def _load_history(self) -> None:
        """Load spending history from storage."""
        if not self.config.storage_path:
            return

        path = Path(self.config.storage_path)
        if not path.exists():
            return

        try:
            with open(path, "r") as f:
                data = json.load(f)

            self._spending_history = [
                SpendingRecord.from_dict(r) for r in data.get("history", [])
            ]
            logger.debug("Loaded %d spending records", len(self._spending_history))

        except Exception as e:
            logger.warning("Failed to load spending history: %s", e)

    def _save_history(self) -> None:
        """Save spending history to storage."""
        if not self.config.storage_path:
            return

        path = Path(self.config.storage_path)

        try:
            # Ensure directory exists
            path.parent.mkdir(parents=True, exist_ok=True)

            data = {"history": [r.to_dict() for r in self._spending_history]}

            with open(path, "w") as f:
                json.dump(data, f, indent=2)

        except Exception as e:
            logger.warning("Failed to save spending history: %s", e)

    def export_history(self) -> Dict[str, Any]:
        """
        Export spending history and stats.

        Returns:
            Dictionary with full history and stats
        """
        return {
            "wallet_address": self._address,
            "stats": self.get_stats(),
            "history": [r.to_dict() for r in self._spending_history],
            "exported_at": datetime.now().isoformat(),
        }

    def __repr__(self) -> str:
        return (
            f"SpendingWallet(address={self._address}, "
            f"daily=${self.get_daily_spending():.2f}/${self.config.max_per_day:.2f})"
        )
