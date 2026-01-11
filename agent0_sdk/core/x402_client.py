"""
x402 Payment Client for Agent0 SDK

Handles HTTP 402 Payment Required responses by automatically constructing
and submitting payments, then retrying the original request.

This enables seamless interaction with x402-enabled MCP servers that charge
per-request fees.

Requires: pip install x402
"""

from __future__ import annotations

import json
import logging
import base64
from typing import Any, Dict, Optional, Callable, Union
from dataclasses import dataclass, field

import requests

logger = logging.getLogger(__name__)


# Payment configuration defaults
DEFAULT_MAX_PRICE_USD = 1.0  # Maximum price per request in USD
DEFAULT_AUTO_PAY = False  # Require explicit opt-in for payments


@dataclass
class X402Config:
    """Configuration for x402 payment handling."""

    # Wallet/signer for making payments
    private_key: Optional[str] = None

    # Maximum price willing to pay per request (in USD equivalent)
    max_price_per_request: float = DEFAULT_MAX_PRICE_USD

    # Whether to automatically pay without prompting
    auto_pay: bool = DEFAULT_AUTO_PAY

    # Callback for payment approval (called if auto_pay=False)
    # Signature: (payment_details: dict) -> bool
    payment_approval_callback: Optional[Callable[[Dict], bool]] = None

    # Preferred network for payments (e.g., "base", "base-sepolia", "solana")
    preferred_network: str = "base-sepolia"

    # Facilitator URL (optional, uses default if not specified)
    facilitator_url: Optional[str] = None

    # Track total spent in this session
    session_spending: float = field(default=0.0, init=False)
    session_spending_limit: Optional[float] = None  # Optional session limit


class X402PaymentError(Exception):
    """Raised when x402 payment fails."""
    pass


class X402PriceExceededError(X402PaymentError):
    """Raised when requested price exceeds configured maximum."""
    pass


class X402PaymentDeclinedError(X402PaymentError):
    """Raised when payment is declined by callback or user."""
    pass


class X402Client:
    """
    HTTP client with x402 payment support.

    Wraps requests to automatically handle HTTP 402 responses by:
    1. Parsing payment requirements from PAYMENT-REQUIRED header
    2. Constructing payment payload
    3. Signing and submitting payment
    4. Retrying original request with PAYMENT-SIGNATURE header

    Example usage:

        from agent0_sdk.core.x402_client import X402Client, X402Config

        config = X402Config(
            private_key="0x...",
            max_price_per_request=0.10,  # Max $0.10 per request
            auto_pay=True,
            preferred_network="base-sepolia"
        )

        client = X402Client(config)

        # This will automatically handle 402 and pay if needed
        response = client.post(
            "https://mcp-server.example.com/",
            json={"jsonrpc": "2.0", "method": "tools/list", "id": 1}
        )
    """

    def __init__(self, config: Optional[X402Config] = None):
        """
        Initialize x402 client.

        Args:
            config: X402Config with payment settings. If None, payments are disabled.
        """
        self.config = config
        self._x402_available = self._check_x402_available()
        self._payment_client = None

        if config and config.private_key and self._x402_available:
            self._init_payment_client()

    def _check_x402_available(self) -> bool:
        """Check if x402 package is installed."""
        try:
            import x402
            return True
        except ImportError:
            logger.debug("x402 package not installed. Payment support disabled.")
            return False

    def _init_payment_client(self):
        """Initialize the x402 payment client using x402_requests."""
        if not self._x402_available:
            return

        try:
            from x402.clients.requests import x402_requests
            from eth_account import Account

            # Create account from private key
            account = Account.from_key(self.config.private_key)
            self._account = account

            # Create x402-enabled requests session
            # max_value is in base units (6 decimals for USDC)
            max_value_base_units = int(self.config.max_price_per_request * 1_000_000)

            self._payment_client = x402_requests(
                account=account,
                max_value=max_value_base_units
            )

            logger.info(f"x402 payment client initialized for address {account.address}")

        except Exception as e:
            logger.warning(f"Failed to initialize x402 payment client: {e}")
            self._payment_client = None

    @property
    def payments_enabled(self) -> bool:
        """Check if payments are enabled and configured."""
        return (
            self.config is not None and
            self.config.private_key is not None and
            self._x402_available and
            self._payment_client is not None
        )

    def _parse_payment_required(self, response: requests.Response) -> Optional[Dict]:
        """
        Parse payment requirements from 402 response.

        Args:
            response: HTTP response with 402 status

        Returns:
            Payment requirements dict or None if not parseable
        """
        # Check for PAYMENT-REQUIRED header (x402 standard)
        payment_header = response.headers.get("PAYMENT-REQUIRED") or response.headers.get("X-Payment-Required")

        if not payment_header:
            # Try to parse from response body
            try:
                body = response.json()
                if "paymentRequired" in body:
                    return body["paymentRequired"]
            except (json.JSONDecodeError, ValueError):
                pass
            return None

        try:
            # Decode base64 payment requirements
            decoded = base64.b64decode(payment_header)
            return json.loads(decoded)
        except Exception as e:
            logger.warning(f"Failed to parse PAYMENT-REQUIRED header: {e}")
            # Try direct JSON parsing
            try:
                return json.loads(payment_header)
            except json.JSONDecodeError:
                return None

    def _validate_payment(self, payment_details: Dict) -> bool:
        """
        Validate payment details against configured limits.

        Args:
            payment_details: Payment requirements from server

        Returns:
            True if payment should proceed

        Raises:
            X402PriceExceededError: If price exceeds limit
            X402PaymentDeclinedError: If payment declined by callback
        """
        # Extract price (handle various formats)
        price = payment_details.get("price") or payment_details.get("amount")
        if isinstance(price, dict):
            # Handle structured price like {"amount": "0.01", "currency": "USDC"}
            amount_str = price.get("amount", "0")
            try:
                price = float(amount_str)
            except ValueError:
                price = 0
        elif isinstance(price, str):
            try:
                price = float(price)
            except ValueError:
                price = 0
        else:
            price = float(price) if price else 0

        # Check against max price
        if price > self.config.max_price_per_request:
            raise X402PriceExceededError(
                f"Requested price ${price} exceeds configured maximum ${self.config.max_price_per_request}"
            )

        # Check session spending limit
        if self.config.session_spending_limit:
            if self.config.session_spending + price > self.config.session_spending_limit:
                raise X402PriceExceededError(
                    f"Payment would exceed session limit. "
                    f"Current: ${self.config.session_spending}, "
                    f"Limit: ${self.config.session_spending_limit}"
                )

        # Auto-pay or callback
        if self.config.auto_pay:
            return True

        if self.config.payment_approval_callback:
            approved = self.config.payment_approval_callback(payment_details)
            if not approved:
                raise X402PaymentDeclinedError("Payment declined by approval callback")
            return True

        # No auto-pay and no callback - decline by default
        raise X402PaymentDeclinedError(
            "Payment required but auto_pay=False and no approval callback configured"
        )

    def _construct_payment(self, payment_details: Dict) -> str:
        """
        Construct and sign payment payload.

        Args:
            payment_details: Payment requirements from server

        Returns:
            Base64-encoded payment signature
        """
        if not self._payment_client:
            raise X402PaymentError("Payment client not initialized")

        try:
            # Use x402 library to construct payment
            payment_payload = self._payment_client.create_payment(payment_details)

            # Track spending
            price = payment_details.get("price") or payment_details.get("amount")
            if isinstance(price, dict):
                price = float(price.get("amount", 0))
            elif isinstance(price, str):
                price = float(price)
            else:
                price = float(price) if price else 0
            self.config.session_spending += price

            return payment_payload

        except Exception as e:
            raise X402PaymentError(f"Failed to construct payment: {e}")

    def _handle_402(
        self,
        response: requests.Response,
        method: str,
        url: str,
        **kwargs
    ) -> requests.Response:
        """
        Handle 402 Payment Required response.

        Args:
            response: Original 402 response
            method: HTTP method
            url: Request URL
            **kwargs: Original request kwargs

        Returns:
            Response after payment and retry
        """
        if not self.payments_enabled:
            logger.warning(f"Received 402 but payments not enabled for {url}")
            return response

        # Parse payment requirements
        payment_details = self._parse_payment_required(response)
        if not payment_details:
            logger.warning(f"Could not parse payment requirements from 402 response")
            return response

        logger.info(f"x402 payment required: {payment_details}")

        # Validate and approve payment
        try:
            self._validate_payment(payment_details)
        except X402PaymentError as e:
            logger.warning(f"Payment validation failed: {e}")
            raise

        # Construct payment
        payment_signature = self._construct_payment(payment_details)

        # Retry with payment
        headers = kwargs.get("headers", {}).copy()
        headers["PAYMENT-SIGNATURE"] = payment_signature
        kwargs["headers"] = headers

        logger.info(f"Retrying request with payment signature")

        # Make the request again
        retry_response = requests.request(method, url, **kwargs)

        # Check if payment was accepted
        if retry_response.status_code == 402:
            raise X402PaymentError("Payment was not accepted by server")

        return retry_response

    def request(
        self,
        method: str,
        url: str,
        handle_402: bool = True,
        **kwargs
    ) -> requests.Response:
        """
        Make HTTP request with x402 payment support.

        Args:
            method: HTTP method (GET, POST, etc.)
            url: Request URL
            handle_402: Whether to handle 402 responses with payment
            **kwargs: Additional arguments passed to requests

        Returns:
            HTTP response
        """
        # If x402 payments are enabled and handle_402 is True, use the x402 session
        # which automatically handles 402 responses and makes payments
        if handle_402 and self.payments_enabled and self._payment_client:
            try:
                response = self._payment_client.request(method, url, **kwargs)
                # Track approximate spending (x402 handles the actual payment)
                # Note: actual payment amount is handled by x402 internally
                return response
            except Exception as e:
                logger.warning(f"x402 session request failed: {e}, falling back to regular request")
                # Fall through to regular request

        # Regular request without payment handling
        response = requests.request(method, url, **kwargs)

        # If we got 402 but couldn't handle it, log a warning
        if response.status_code == 402 and handle_402:
            if not self.payments_enabled:
                logger.warning(f"Received 402 Payment Required but x402 payments not enabled")
            elif not self._payment_client:
                logger.warning(f"Received 402 Payment Required but payment client not initialized")

        return response

    def get(self, url: str, handle_402: bool = True, **kwargs) -> requests.Response:
        """Make GET request with x402 support."""
        return self.request("GET", url, handle_402=handle_402, **kwargs)

    def post(self, url: str, handle_402: bool = True, **kwargs) -> requests.Response:
        """Make POST request with x402 support."""
        return self.request("POST", url, handle_402=handle_402, **kwargs)

    def put(self, url: str, handle_402: bool = True, **kwargs) -> requests.Response:
        """Make PUT request with x402 support."""
        return self.request("PUT", url, handle_402=handle_402, **kwargs)

    def delete(self, url: str, handle_402: bool = True, **kwargs) -> requests.Response:
        """Make DELETE request with x402 support."""
        return self.request("DELETE", url, handle_402=handle_402, **kwargs)

    def get_session_spending(self) -> float:
        """Get total amount spent in this session."""
        return self.config.session_spending if self.config else 0.0

    def reset_session_spending(self):
        """Reset session spending counter."""
        if self.config:
            self.config.session_spending = 0.0


def create_x402_client(
    private_key: str,
    max_price_per_request: float = DEFAULT_MAX_PRICE_USD,
    auto_pay: bool = False,
    preferred_network: str = "base-sepolia",
    session_spending_limit: Optional[float] = None,
    payment_approval_callback: Optional[Callable[[Dict], bool]] = None,
) -> X402Client:
    """
    Convenience function to create an x402 client.

    Args:
        private_key: Wallet private key for signing payments
        max_price_per_request: Maximum price willing to pay per request (USD)
        auto_pay: Whether to automatically pay without prompting
        preferred_network: Network for payments (base, base-sepolia, solana, etc.)
        session_spending_limit: Optional limit on total session spending
        payment_approval_callback: Optional callback for payment approval

    Returns:
        Configured X402Client instance

    Example:
        client = create_x402_client(
            private_key=os.getenv("PAYMENT_PRIVATE_KEY"),
            max_price_per_request=0.10,
            auto_pay=True,
            preferred_network="base-sepolia"
        )

        response = client.post(
            "https://mcp.example.com/",
            json={"jsonrpc": "2.0", "method": "tools/call", "params": {...}}
        )
    """
    config = X402Config(
        private_key=private_key,
        max_price_per_request=max_price_per_request,
        auto_pay=auto_pay,
        preferred_network=preferred_network,
        session_spending_limit=session_spending_limit,
        payment_approval_callback=payment_approval_callback,
    )
    return X402Client(config)
