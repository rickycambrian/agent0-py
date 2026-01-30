"""
Test suite for SpendingWallet system.

Tests the complete SpendingWallet system including:
- Wallet creation and configuration
- Spending policy enforcement
- HD wallet derivation
- Integration with x402 client

Run with: pytest tests/test_spending_wallet.py -v
"""

import os
import sys
import pytest
import tempfile
import time
from unittest.mock import Mock, patch, MagicMock

from agent0_sdk.core.spending_wallet import (
    SpendingWallet,
    SpendingWalletConfig,
    SpendingRecord,
    SpendingPolicyError,
    SpendingLimitExceededError,
    EndpointNotWhitelistedError,
    DuplicatePaymentError,
    CircuitBreakerTrippedError,
)
from agent0_sdk.core.spending_policy import (
    SpendingPolicy,
    SpendingPolicyConfig,
    PolicyResult,
    PolicyViolationType,
)
from agent0_sdk.core.spending_tracker import (
    SpendingTracker,
    TrackedPayment,
)
from agent0_sdk.core.wallet_derivation import (
    generate_seed_phrase,
    derive_spending_wallet,
    validate_seed_phrase,
    generate_spending_wallet,
    derive_multiple_wallets,
    get_derivation_path,
    private_key_to_address,
    AGENT0_DERIVATION_PATH,
)


class TestSpendingWalletConfig:
    """Tests for SpendingWalletConfig."""

    def test_default_values(self):
        """Config has sensible defaults."""
        config = SpendingWalletConfig()
        assert config.max_per_transaction == 0.10
        assert config.max_per_day == 5.0
        assert config.max_per_week == 20.0
        assert config.preferred_network == "base-sepolia"
        assert config.deduplication_window_seconds == 60
        assert config.circuit_breaker_threshold == 10

    def test_custom_values(self):
        """Config accepts custom values."""
        config = SpendingWalletConfig(
            private_key="0x" + "1" * 64,
            max_per_transaction=0.50,
            max_per_day=10.0,
            max_per_week=50.0,
            endpoint_whitelist=["api.example.com"],
            preferred_network="base",
        )
        assert config.private_key == "0x" + "1" * 64
        assert config.max_per_transaction == 0.50
        assert config.max_per_day == 10.0
        assert config.endpoint_whitelist == ["api.example.com"]


class TestSpendingWalletCreation:
    """Tests for SpendingWallet creation."""

    @patch("agent0_sdk.core.spending_wallet.SpendingWallet._derive_address")
    def test_create_from_private_key(self, mock_derive):
        """Create wallet from private key."""
        mock_derive.return_value = "0x1234567890abcdef"
        private_key = "0x" + "a" * 64
        config = SpendingWalletConfig(private_key=private_key)

        wallet = SpendingWallet.from_private_key(private_key, config)

        assert wallet.get_address() == "0x1234567890abcdef"
        assert wallet.config.max_per_transaction == 0.10

    @patch("agent0_sdk.core.wallet_derivation.derive_spending_wallet")
    def test_create_from_seed_phrase(self, mock_derive):
        """Create wallet from seed phrase."""
        mock_derive.return_value = ("0x" + "b" * 64, "0xDerivedAddress")

        config = SpendingWalletConfig(
            seed_phrase="test seed phrase words",
            derivation_index=0,
        )
        wallet = SpendingWallet.create(config)

        assert wallet.get_address() == "0xDerivedAddress"
        mock_derive.assert_called_once_with("test seed phrase words", 0)


class TestSpendingWalletValidation:
    """Tests for SpendingWallet payment validation."""

    @pytest.fixture
    def wallet(self):
        """Create wallet for testing."""
        with patch("agent0_sdk.core.spending_wallet.SpendingWallet._derive_address") as mock:
            mock.return_value = "0xTestAddress"
            config = SpendingWalletConfig(
                private_key="0x" + "1" * 64,
                max_per_transaction=0.10,
                max_per_day=5.0,
                max_per_week=20.0,
                endpoint_whitelist=["api.example.com", "trusted.com"],
            )
            return SpendingWallet.from_private_key("0x" + "1" * 64, config)

    def test_validate_within_limits(self, wallet):
        """Payment within all limits is approved."""
        result = wallet.validate_payment(0.05, "https://api.example.com/endpoint")
        assert result is True

    def test_validate_exceeds_transaction_limit(self, wallet):
        """Payment exceeding transaction limit is rejected."""
        with pytest.raises(SpendingLimitExceededError) as exc_info:
            wallet.validate_payment(0.20, "https://api.example.com/endpoint")
        assert "per-transaction limit" in str(exc_info.value)

    def test_validate_endpoint_not_whitelisted(self, wallet):
        """Payment to non-whitelisted endpoint is rejected."""
        with pytest.raises(EndpointNotWhitelistedError) as exc_info:
            wallet.validate_payment(0.05, "https://completely-unknown-domain.xyz/endpoint")
        assert "not in whitelist" in str(exc_info.value)

    def test_validate_endpoint_whitelisted(self, wallet):
        """Payment to whitelisted endpoint is approved."""
        result = wallet.validate_payment(0.05, "https://trusted.com/endpoint")
        assert result is True

    def test_validate_duplicate_payment(self, wallet):
        """Duplicate payment is detected and rejected."""
        endpoint = "https://api.example.com/endpoint"

        # First payment should succeed
        wallet.validate_payment(0.05, endpoint)
        wallet.record_payment(0.05, endpoint)

        # Second identical payment within window should fail
        with pytest.raises(DuplicatePaymentError):
            wallet.validate_payment(0.05, endpoint)

    def test_validate_daily_limit(self, wallet):
        """Payment exceeding daily limit is rejected."""
        endpoint = "https://api.example.com/endpoint"

        # Record payments up to near the limit ($5.0)
        # 50 payments of $0.10 = $5.0, which fills the daily limit exactly
        for i in range(50):
            # Record without validation to bypass dedup
            wallet._spending_history.append(
                SpendingRecord(
                    timestamp=time.time() - i,
                    amount=0.10,
                    endpoint=f"{endpoint}/{i}",
                )
            )

        # Should now exceed daily limit since we're at exactly $5.00
        with pytest.raises(SpendingLimitExceededError) as exc_info:
            wallet.validate_payment(0.05, f"{endpoint}/51")
        assert "daily limit" in str(exc_info.value)


class TestSpendingWalletCircuitBreaker:
    """Tests for circuit breaker functionality."""

    @pytest.fixture
    def wallet(self):
        """Create wallet with low circuit breaker threshold."""
        with patch("agent0_sdk.core.spending_wallet.SpendingWallet._derive_address") as mock:
            mock.return_value = "0xTestAddress"
            config = SpendingWalletConfig(
                private_key="0x" + "1" * 64,
                circuit_breaker_threshold=3,
                circuit_breaker_window_seconds=60,
            )
            return SpendingWallet.from_private_key("0x" + "1" * 64, config)

    def test_circuit_breaker_trips(self, wallet):
        """Circuit breaker trips after threshold failures."""
        for i in range(3):
            wallet.record_failure("https://api.example.com", f"Error {i}")

        with pytest.raises(CircuitBreakerTrippedError):
            wallet.validate_payment(0.05, "https://api.example.com")

    def test_circuit_breaker_reset(self, wallet):
        """Circuit breaker can be manually reset."""
        for i in range(3):
            wallet.record_failure("https://api.example.com", f"Error {i}")

        # Reset circuit breaker
        wallet.reset_circuit_breaker()

        # Should work now
        result = wallet.validate_payment(0.05, "https://api.example.com")
        assert result is True


class TestSpendingWalletStats:
    """Tests for spending statistics."""

    @pytest.fixture
    def wallet(self):
        """Create wallet for testing."""
        with patch("agent0_sdk.core.spending_wallet.SpendingWallet._derive_address") as mock:
            mock.return_value = "0xTestAddress"
            config = SpendingWalletConfig(private_key="0x" + "1" * 64)
            return SpendingWallet.from_private_key("0x" + "1" * 64, config)

    def test_initial_stats(self, wallet):
        """Initial stats are zeroed."""
        stats = wallet.get_stats()
        assert stats["daily_spending"] == 0.0
        assert stats["weekly_spending"] == 0.0
        assert stats["session_spending"] == 0.0
        assert stats["total_payments"] == 0

    def test_stats_after_payments(self, wallet):
        """Stats update after payments."""
        wallet.record_payment(0.05, "https://api.example.com/1")
        wallet.record_payment(0.03, "https://api.example.com/2")

        stats = wallet.get_stats()
        assert stats["daily_spending"] == 0.08
        assert stats["session_spending"] == 0.08
        assert stats["total_payments"] == 2


class TestSpendingPolicy:
    """Tests for standalone SpendingPolicy."""

    @pytest.fixture
    def policy(self):
        """Create policy for testing."""
        return SpendingPolicy(
            max_per_transaction=0.10,
            max_per_day=5.0,
            endpoint_blacklist=["malicious.com"],
        )

    def test_validate_allowed(self, policy):
        """Valid payment is allowed."""
        result = policy.validate(0.05, "https://api.example.com")
        assert result.allowed is True

    def test_validate_transaction_limit(self, policy):
        """Transaction limit is enforced."""
        result = policy.validate(0.20, "https://api.example.com")
        assert result.allowed is False
        assert result.violation_type == PolicyViolationType.TRANSACTION_LIMIT

    def test_validate_blacklist(self, policy):
        """Blacklist is enforced."""
        result = policy.validate(0.05, "https://malicious.com/endpoint")
        assert result.allowed is False
        assert result.violation_type == PolicyViolationType.ENDPOINT_BLACKLISTED

    def test_record_and_get_spending(self, policy):
        """Spending is tracked correctly."""
        policy.record_payment(0.05, "https://api.example.com")
        policy.record_payment(0.03, "https://api.example.com")

        assert policy.get_spending("session") == 0.08


class TestSpendingTracker:
    """Tests for SpendingTracker persistent storage."""

    @pytest.fixture
    def tracker(self):
        """Create tracker with temp database."""
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            tracker = SpendingTracker(
                storage_path=f.name,
                wallet_address="0xTestWallet"
            )
            yield tracker
            # Cleanup
            os.unlink(f.name)

    def test_record_payment(self, tracker):
        """Payments are recorded."""
        record_id = tracker.record(
            amount=0.05,
            endpoint="https://api.example.com",
            tx_hash="0xabc",
        )
        assert record_id > 0

    def test_get_spending_for_period(self, tracker):
        """Spending is calculated correctly."""
        tracker.record(amount=0.05, endpoint="https://api.example.com")
        tracker.record(amount=0.03, endpoint="https://api.example.com")

        daily = tracker.get_spending_for_period("day")
        assert daily == 0.08

    def test_get_history(self, tracker):
        """History is retrieved correctly."""
        tracker.record(amount=0.05, endpoint="https://api.example.com/1")
        tracker.record(amount=0.03, endpoint="https://api.example.com/2")

        history = tracker.get_history()
        assert len(history) == 2
        # Most recent first
        assert history[0].amount == 0.03

    def test_export_and_import(self, tracker):
        """Data can be exported and imported."""
        tracker.record(amount=0.05, endpoint="https://api.example.com")

        data = tracker.export()
        assert "history" in data
        assert len(data["history"]) == 1

        # Clear and reimport
        tracker.clear()
        assert tracker.get_spending_for_period("all") == 0.0

        imported = tracker.import_data(data)
        assert imported == 1
        assert tracker.get_spending_for_period("all") == 0.05


class TestWalletDerivation:
    """Tests for HD wallet derivation.

    These tests run against the actual eth_account library when available.
    """

    def test_derivation_path_format(self):
        """Derivation path has correct format."""
        assert AGENT0_DERIVATION_PATH == "m/44'/60'/8004'/0"

        path = get_derivation_path(0)
        assert path == "m/44'/60'/8004'/0/0"

        path = get_derivation_path(5)
        assert path == "m/44'/60'/8004'/0/5"

    def test_generate_seed_phrase(self):
        """Seed phrase is generated correctly."""
        try:
            seed = generate_seed_phrase()
            assert isinstance(seed, str)
            words = seed.split()
            assert len(words) in [12, 24]  # BIP-39 standard lengths
        except ImportError:
            pytest.skip("mnemonic or eth_account not installed")

    def test_validate_seed_phrase_length(self):
        """Seed phrase validation checks word count."""
        # Valid lengths
        assert validate_seed_phrase(" ".join(["word"] * 12)) is True
        assert validate_seed_phrase(" ".join(["word"] * 24)) is True

        # Invalid lengths
        assert validate_seed_phrase(" ".join(["word"] * 10)) is False
        assert validate_seed_phrase(" ".join(["word"] * 13)) is False

    def test_derive_spending_wallet(self):
        """Wallet is derived correctly from seed."""
        try:
            # Generate a real seed phrase
            seed = generate_seed_phrase()

            # Derive a wallet
            pk, addr = derive_spending_wallet(seed, 0)

            # Verify format
            assert pk.startswith("0x")
            assert len(pk) == 66  # 0x + 64 hex chars
            assert addr.startswith("0x")
            assert len(addr) == 42  # Standard Ethereum address

        except ImportError:
            pytest.skip("eth_account not installed")

    def test_derive_multiple_wallets(self):
        """Multiple wallets can be derived."""
        try:
            seed = generate_seed_phrase()

            wallets = derive_multiple_wallets(seed, count=3)

            assert len(wallets) == 3
            assert wallets[0][0] == 0  # Index
            assert wallets[1][0] == 1
            assert wallets[2][0] == 2

            # Each wallet should have unique address
            addresses = [w[2] for w in wallets]
            assert len(set(addresses)) == 3

        except ImportError:
            pytest.skip("eth_account not installed")

    def test_generate_spending_wallet(self):
        """Random wallet can be generated."""
        try:
            pk, addr = generate_spending_wallet()

            assert pk.startswith("0x")
            assert len(pk) == 66  # 0x + 64 hex chars
            assert addr.startswith("0x")
            assert len(addr) == 42

        except ImportError:
            pytest.skip("eth_account not installed")

    def test_private_key_to_address(self):
        """Private key can be converted to address."""
        try:
            # Generate a wallet
            pk, expected_addr = generate_spending_wallet()

            # Convert private key back to address
            addr = private_key_to_address(pk)

            assert addr == expected_addr

        except ImportError:
            pytest.skip("eth_account not installed")


class TestX402Integration:
    """Tests for SpendingWallet x402 integration."""

    @pytest.fixture
    def wallet(self):
        """Create wallet for testing."""
        with patch("agent0_sdk.core.spending_wallet.SpendingWallet._derive_address") as mock:
            mock.return_value = "0xTestAddress"
            config = SpendingWalletConfig(
                private_key="0x" + "1" * 64,
                max_per_transaction=0.10,
            )
            return SpendingWallet.from_private_key("0x" + "1" * 64, config)

    def test_get_x402_client(self, wallet):
        """X402 client is created correctly."""
        # Patch inside the spending_wallet module where X402Client is imported
        with patch("agent0_sdk.core.x402_client.X402Client") as mock_client:
            with patch("agent0_sdk.core.x402_client.X402Config") as mock_config:
                mock_client_instance = Mock()
                mock_client.return_value = mock_client_instance

                x402 = wallet.get_x402_client()

                # X402Client should be instantiated
                assert x402 is not None


class TestSpendingRecord:
    """Tests for SpendingRecord dataclass."""

    def test_to_dict(self):
        """SpendingRecord converts to dict correctly."""
        record = SpendingRecord(
            timestamp=1234567890.0,
            amount=0.05,
            endpoint="https://api.example.com",
            tx_hash="0xabc",
            success=True,
        )

        d = record.to_dict()
        assert d["timestamp"] == 1234567890.0
        assert d["amount"] == 0.05
        assert d["endpoint"] == "https://api.example.com"
        assert d["tx_hash"] == "0xabc"

    def test_from_dict(self):
        """SpendingRecord can be created from dict."""
        d = {
            "timestamp": 1234567890.0,
            "amount": 0.05,
            "endpoint": "https://api.example.com",
            "tx_hash": "0xabc",
            "success": True,
        }

        record = SpendingRecord.from_dict(d)
        assert record.amount == 0.05
        assert record.endpoint == "https://api.example.com"


class TestSDKIntegration:
    """Tests for SDK SpendingWallet integration."""

    @patch("agent0_sdk.core.sdk.Web3Client")
    @patch("agent0_sdk.core.wallet_derivation.derive_spending_wallet")
    def test_sdk_with_spending_wallet_seed(self, mock_derive, mock_web3):
        """SDK initializes with spending wallet from seed."""
        mock_derive.return_value = ("0x" + "a" * 64, "0xDerivedAddress")

        from agent0_sdk import SDK, SpendingWalletConfig

        config = SpendingWalletConfig(max_per_day=10.0)

        sdk = SDK(
            chainId=11155111,
            rpcUrl="https://example.com",
            spendingWalletSeed="test seed phrase words",
            spendingWalletIndex=0,
            spendingWalletConfig=config,
        )

        assert sdk.spending_wallet is not None
        assert sdk.spending_wallet.get_address() == "0xDerivedAddress"
