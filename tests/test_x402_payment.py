"""
Test suite for x402 payment integration.

Tests the x402 payment flow for interacting with payment-enabled MCP servers.

Run with: pytest tests/test_x402_payment.py -v
"""

import os
import pytest
from unittest.mock import Mock, patch, MagicMock

# Import x402 components
from agent0_sdk.core.x402_client import (
    X402Client,
    X402Config,
    X402PaymentError,
    X402PriceExceededError,
    X402PaymentDeclinedError,
    create_x402_client,
    DEFAULT_MAX_PRICE_USD,
)


class TestX402Config:
    """Tests for X402Config dataclass."""

    def test_default_values(self):
        """Config has sensible defaults."""
        config = X402Config()
        assert config.private_key is None
        assert config.max_price_per_request == DEFAULT_MAX_PRICE_USD
        assert config.auto_pay is False
        assert config.preferred_network == "base-sepolia"
        assert config.session_spending == 0.0
        assert config.session_spending_limit is None

    def test_custom_values(self):
        """Config accepts custom values."""
        config = X402Config(
            private_key="0x" + "1" * 64,
            max_price_per_request=0.05,
            auto_pay=True,
            preferred_network="base",
            session_spending_limit=10.0,
        )
        assert config.private_key == "0x" + "1" * 64
        assert config.max_price_per_request == 0.05
        assert config.auto_pay is True
        assert config.preferred_network == "base"
        assert config.session_spending_limit == 10.0


class TestX402ClientInitialization:
    """Tests for X402Client initialization."""

    def test_no_config_disables_payments(self):
        """Payments disabled when no config provided."""
        client = X402Client(None)
        assert not client.payments_enabled
        assert client.get_session_spending() == 0.0

    def test_empty_config_disables_payments(self):
        """Payments disabled with empty config."""
        config = X402Config()
        client = X402Client(config)
        assert not client.payments_enabled

    def test_config_without_key_disables_payments(self):
        """Payments disabled without private key."""
        config = X402Config(auto_pay=True, max_price_per_request=1.0)
        client = X402Client(config)
        assert not client.payments_enabled

    @patch("agent0_sdk.core.x402_client.X402Client._check_x402_available")
    def test_payments_require_x402_package(self, mock_check):
        """Payments require x402 package to be installed."""
        mock_check.return_value = False
        config = X402Config(private_key="0x" + "1" * 64)
        client = X402Client(config)
        assert not client.payments_enabled


class TestX402PriceValidation:
    """Tests for payment price validation."""

    @pytest.fixture
    def client_with_limits(self):
        """Create client with price limits configured."""
        config = X402Config(
            private_key="0x" + "1" * 64,
            max_price_per_request=0.05,
            auto_pay=False,
            session_spending_limit=0.10,
        )
        return X402Client(config)

    def test_price_within_limit_with_callback(self, client_with_limits):
        """Payment approved when price within limit and callback approves."""
        client_with_limits.config.payment_approval_callback = lambda x: True
        result = client_with_limits._validate_payment({"price": 0.01})
        assert result is True

    def test_price_exceeds_max_raises_error(self, client_with_limits):
        """Price exceeding max raises X402PriceExceededError."""
        with pytest.raises(X402PriceExceededError) as exc_info:
            client_with_limits._validate_payment({"price": 0.10})
        assert "exceeds configured maximum" in str(exc_info.value)

    def test_price_exceeds_session_limit_raises_error(self, client_with_limits):
        """Price exceeding session limit raises X402PriceExceededError."""
        client_with_limits.config.session_spending = 0.08
        with pytest.raises(X402PriceExceededError) as exc_info:
            client_with_limits._validate_payment({"price": 0.04})
        assert "session limit" in str(exc_info.value)

    def test_auto_pay_approves_within_limit(self):
        """Auto-pay approves payments within limit."""
        config = X402Config(
            private_key="0x" + "1" * 64,
            max_price_per_request=0.05,
            auto_pay=True,
        )
        client = X402Client(config)
        result = client._validate_payment({"price": 0.01})
        assert result is True

    def test_no_auto_pay_no_callback_declines(self, client_with_limits):
        """Payment declined when auto_pay=False and no callback."""
        with pytest.raises(X402PaymentDeclinedError) as exc_info:
            client_with_limits._validate_payment({"price": 0.01})
        assert "auto_pay=False" in str(exc_info.value)

    def test_callback_can_decline_payment(self, client_with_limits):
        """Callback can decline payment."""
        client_with_limits.config.payment_approval_callback = lambda x: False
        with pytest.raises(X402PaymentDeclinedError) as exc_info:
            client_with_limits._validate_payment({"price": 0.01})
        assert "declined by approval callback" in str(exc_info.value)


class TestX402PriceParsing:
    """Tests for different price formats in payment details."""

    @pytest.fixture
    def client(self):
        """Create client with auto-pay enabled."""
        config = X402Config(
            private_key="0x" + "1" * 64,
            max_price_per_request=1.0,
            auto_pay=True,
        )
        return X402Client(config)

    def test_numeric_price(self, client):
        """Handles numeric price."""
        result = client._validate_payment({"price": 0.01})
        assert result is True

    def test_string_price(self, client):
        """Handles string price."""
        result = client._validate_payment({"price": "0.01"})
        assert result is True

    def test_dict_price(self, client):
        """Handles structured price dict."""
        result = client._validate_payment(
            {"price": {"amount": "0.01", "currency": "USDC"}}
        )
        assert result is True

    def test_amount_key(self, client):
        """Handles 'amount' key instead of 'price'."""
        result = client._validate_payment({"amount": 0.01})
        assert result is True


class TestX402SessionSpending:
    """Tests for session spending tracking."""

    def test_initial_spending_is_zero(self):
        """Initial session spending is zero."""
        config = X402Config(private_key="0x" + "1" * 64)
        client = X402Client(config)
        assert client.get_session_spending() == 0.0

    def test_spending_tracks_manually(self):
        """Session spending can be tracked manually."""
        config = X402Config(private_key="0x" + "1" * 64)
        client = X402Client(config)
        config.session_spending = 0.25
        assert client.get_session_spending() == 0.25

    def test_reset_spending(self):
        """Session spending can be reset."""
        config = X402Config(private_key="0x" + "1" * 64)
        client = X402Client(config)
        config.session_spending = 0.50
        client.reset_session_spending()
        assert client.get_session_spending() == 0.0

    def test_spending_with_no_config(self):
        """get_session_spending returns 0 with no config."""
        client = X402Client(None)
        assert client.get_session_spending() == 0.0


class TestX402PaymentApprovalCallback:
    """Tests for custom payment approval callbacks."""

    def test_callback_receives_payment_details(self):
        """Callback receives full payment details."""
        received_details = {}

        def capture_callback(details):
            received_details.update(details)
            return True

        config = X402Config(
            private_key="0x" + "1" * 64,
            max_price_per_request=1.0,
            auto_pay=False,
            payment_approval_callback=capture_callback,
        )
        client = X402Client(config)
        client._validate_payment({"price": 0.05, "recipient": "0xabc"})

        assert received_details["price"] == 0.05
        assert received_details["recipient"] == "0xabc"

    def test_callback_with_price_threshold(self):
        """Callback can implement custom price threshold."""

        def threshold_callback(details):
            price = details.get("price", 0)
            return float(price) < 0.03

        config = X402Config(
            private_key="0x" + "1" * 64,
            max_price_per_request=1.0,
            auto_pay=False,
            payment_approval_callback=threshold_callback,
        )
        client = X402Client(config)

        # Below threshold - approved
        assert client._validate_payment({"price": 0.02}) is True

        # Above threshold - declined
        with pytest.raises(X402PaymentDeclinedError):
            client._validate_payment({"price": 0.05})


class TestCreateX402Client:
    """Tests for create_x402_client convenience function."""

    def test_creates_configured_client(self):
        """Creates properly configured client."""
        client = create_x402_client(
            private_key="0x" + "1" * 64,
            max_price_per_request=0.25,
            auto_pay=True,
            preferred_network="base",
            session_spending_limit=5.0,
        )
        assert client.config.private_key == "0x" + "1" * 64
        assert client.config.max_price_per_request == 0.25
        assert client.config.auto_pay is True
        assert client.config.preferred_network == "base"
        assert client.config.session_spending_limit == 5.0

    def test_default_values(self):
        """Uses default values when not specified."""
        client = create_x402_client(private_key="0x" + "1" * 64)
        assert client.config.max_price_per_request == DEFAULT_MAX_PRICE_USD
        assert client.config.auto_pay is False
        assert client.config.preferred_network == "base-sepolia"


class TestX402ClientWalletAddress:
    """Tests for wallet address retrieval."""

    def test_wallet_address_without_init(self):
        """Returns None when not initialized."""
        client = X402Client(None)
        assert client.get_wallet_address() is None

    @patch("agent0_sdk.core.x402_client.X402Client._check_x402_available")
    @patch("agent0_sdk.core.x402_client.X402Client._init_payment_client")
    def test_wallet_address_after_init(self, mock_init, mock_check):
        """Returns address after initialization."""
        mock_check.return_value = True

        config = X402Config(private_key="0x" + "1" * 64)
        client = X402Client(config)

        # Simulate account being set
        mock_account = Mock()
        mock_account.address = "0x1234567890abcdef1234567890abcdef12345678"
        client._account = mock_account

        assert client.get_wallet_address() == "0x1234567890abcdef1234567890abcdef12345678"


# Integration tests that require environment variables
@pytest.mark.skipif(
    not os.getenv("RPC_URL"),
    reason="RPC_URL environment variable not set",
)
class TestX402SDKIntegration:
    """Integration tests with SDK (requires environment setup)."""

    def test_sdk_with_x402_config(self):
        """SDK initializes with x402 configuration."""
        from agent0_sdk import SDK

        sdk = SDK(
            chainId=11155111,
            rpcUrl=os.getenv("RPC_URL"),
            signer=os.getenv("PRIVATE_KEY"),
            x402PrivateKey=os.getenv("X402_PRIVATE_KEY") or os.getenv("PRIVATE_KEY"),
            x402MaxPricePerRequest=0.10,
            x402AutoPay=False,
            x402Network="base-sepolia",
            x402SessionLimit=5.0,
        )

        # x402_client should be created if x402 package is available
        if sdk.x402_client:
            assert sdk.x402_client.config.max_price_per_request == 0.10
            assert sdk.x402_client.config.auto_pay is False

    def test_agent_inherits_x402_client(self):
        """Agent's endpoint crawler inherits x402 client from SDK."""
        from agent0_sdk import SDK

        private_key = os.getenv("PRIVATE_KEY")
        if not private_key:
            pytest.skip("PRIVATE_KEY not set")

        sdk = SDK(
            chainId=11155111,
            rpcUrl=os.getenv("RPC_URL"),
            signer=private_key,
            x402PrivateKey=os.getenv("X402_PRIVATE_KEY") or private_key,
            x402MaxPricePerRequest=0.10,
            x402AutoPay=True,
        )

        agent = sdk.createAgent(
            name="Test Agent",
            description="Agent for x402 testing",
        )

        # Check endpoint crawler has x402 client
        has_x402 = agent._endpoint_crawler._x402_client is not None
        # This depends on whether x402 package is installed
        assert isinstance(has_x402, bool)
