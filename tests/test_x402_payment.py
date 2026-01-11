"""
Test script for x402 payment integration.

This tests the x402 payment flow for interacting with payment-enabled MCP servers.

Usage:
    # Set environment variables
    export RPC_URL="https://eth-sepolia.g.alchemy.com/v2/YOUR_KEY"
    export PRIVATE_KEY="0x..."  # For on-chain operations
    export X402_PRIVATE_KEY="0x..."  # For x402 payments (can be same as PRIVATE_KEY)

    # Run test
    python tests/test_x402_payment.py
"""

import os
import sys
import logging

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

# Check for required environment variables
RPC_URL = os.getenv("RPC_URL")
PRIVATE_KEY = os.getenv("PRIVATE_KEY")
X402_PRIVATE_KEY = os.getenv("X402_PRIVATE_KEY") or PRIVATE_KEY  # Fall back to main signer

if not RPC_URL:
    print("ERROR: RPC_URL environment variable is required")
    sys.exit(1)


def test_x402_client_initialization():
    """Test that X402Client can be initialized."""
    print("\n" + "="*60)
    print("Test 1: X402Client Initialization")
    print("="*60)

    try:
        from agent0_sdk.core.x402_client import X402Client, X402Config, create_x402_client
        print("✅ x402_client module imported successfully")

        # Test with no config (disabled)
        client = X402Client(None)
        assert not client.payments_enabled
        print("✅ X402Client without config: payments_enabled=False")

        # Test with config but no private key
        config = X402Config()
        client = X402Client(config)
        assert not client.payments_enabled
        print("✅ X402Client with empty config: payments_enabled=False")

        # Test with private key
        if X402_PRIVATE_KEY:
            config = X402Config(
                private_key=X402_PRIVATE_KEY,
                max_price_per_request=0.10,
                auto_pay=False,
                preferred_network="base-sepolia"
            )
            client = X402Client(config)
            # Note: payments_enabled depends on whether x402 package is installed
            print(f"✅ X402Client with private key: payments_enabled={client.payments_enabled}")

            if not client.payments_enabled:
                print("   ⚠️  x402 package not installed. Install with: pip install x402")

        return True

    except Exception as e:
        print(f"❌ Failed: {e}")
        return False


def test_x402_convenience_function():
    """Test the create_x402_client convenience function."""
    print("\n" + "="*60)
    print("Test 2: create_x402_client Convenience Function")
    print("="*60)

    try:
        from agent0_sdk.core.x402_client import create_x402_client

        if not X402_PRIVATE_KEY:
            print("⚠️  Skipping: X402_PRIVATE_KEY not set")
            return True

        client = create_x402_client(
            private_key=X402_PRIVATE_KEY,
            max_price_per_request=0.05,
            auto_pay=True,
            preferred_network="base-sepolia",
            session_spending_limit=1.0
        )

        print(f"✅ create_x402_client: payments_enabled={client.payments_enabled}")
        print(f"   Session spending: ${client.get_session_spending()}")

        return True

    except Exception as e:
        print(f"❌ Failed: {e}")
        return False


def test_sdk_x402_integration():
    """Test SDK initialization with x402 configuration."""
    print("\n" + "="*60)
    print("Test 3: SDK x402 Integration")
    print("="*60)

    try:
        from agent0_sdk import SDK

        # Initialize SDK with x402 config
        sdk = SDK(
            chainId=11155111,  # Sepolia
            rpcUrl=RPC_URL,
            signer=PRIVATE_KEY,
            # x402 configuration
            x402PrivateKey=X402_PRIVATE_KEY,
            x402MaxPricePerRequest=0.10,
            x402AutoPay=False,
            x402Network="base-sepolia",
            x402SessionLimit=5.0
        )

        print("✅ SDK initialized with x402 configuration")

        if sdk.x402_client:
            print(f"   x402 payments enabled: {sdk.x402_client.payments_enabled}")
        else:
            print("   ⚠️  x402_client is None (x402 package may not be installed)")

        return True

    except Exception as e:
        print(f"❌ Failed: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_endpoint_crawler_x402():
    """Test EndpointCrawler with x402 client."""
    print("\n" + "="*60)
    print("Test 4: EndpointCrawler x402 Integration")
    print("="*60)

    try:
        from agent0_sdk.core.endpoint_crawler import EndpointCrawler
        from agent0_sdk.core.x402_client import X402Client, X402Config

        # Test without x402 client
        crawler = EndpointCrawler(timeout=5)
        assert not crawler.x402_enabled
        print("✅ EndpointCrawler without x402: x402_enabled=False")

        # Test with x402 client
        if X402_PRIVATE_KEY:
            config = X402Config(
                private_key=X402_PRIVATE_KEY,
                max_price_per_request=0.10,
                auto_pay=True,
                preferred_network="base-sepolia"
            )
            x402_client = X402Client(config)

            crawler = EndpointCrawler(timeout=5, x402_client=x402_client)
            print(f"✅ EndpointCrawler with x402: x402_enabled={crawler.x402_enabled}")

            # Test set_x402_client
            crawler2 = EndpointCrawler(timeout=5)
            crawler2.set_x402_client(x402_client)
            print(f"✅ set_x402_client: x402_enabled={crawler2.x402_enabled}")

        return True

    except Exception as e:
        print(f"❌ Failed: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_agent_x402_inheritance():
    """Test that Agent inherits x402 client from SDK."""
    print("\n" + "="*60)
    print("Test 5: Agent x402 Inheritance")
    print("="*60)

    try:
        from agent0_sdk import SDK

        if not PRIVATE_KEY:
            print("⚠️  Skipping: PRIVATE_KEY not set")
            return True

        # Initialize SDK with x402
        sdk = SDK(
            chainId=11155111,
            rpcUrl=RPC_URL,
            signer=PRIVATE_KEY,
            x402PrivateKey=X402_PRIVATE_KEY,
            x402MaxPricePerRequest=0.10,
            x402AutoPay=True,
            x402Network="base-sepolia"
        )

        # Create agent
        agent = sdk.createAgent(
            name="Test Agent with x402",
            description="Agent for testing x402 payment integration"
        )

        # Check if agent's endpoint crawler has x402 client
        has_x402 = agent._endpoint_crawler._x402_client is not None
        x402_enabled = agent._endpoint_crawler.x402_enabled

        print(f"✅ Agent created")
        print(f"   Endpoint crawler has x402 client: {has_x402}")
        print(f"   x402 enabled: {x402_enabled}")

        return True

    except Exception as e:
        print(f"❌ Failed: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_price_validation():
    """Test x402 price validation."""
    print("\n" + "="*60)
    print("Test 6: Price Validation")
    print("="*60)

    try:
        from agent0_sdk.core.x402_client import (
            X402Client, X402Config,
            X402PriceExceededError, X402PaymentDeclinedError
        )

        config = X402Config(
            private_key=X402_PRIVATE_KEY or "0x" + "1" * 64,  # Dummy key for testing
            max_price_per_request=0.05,
            auto_pay=False,
            session_spending_limit=0.10
        )
        client = X402Client(config)

        # Test price validation (internal method)
        # This tests the validation logic without making actual payments

        # Price within limit
        try:
            result = client._validate_payment({"price": 0.01})
            print("❌ Should have raised X402PaymentDeclinedError (auto_pay=False)")
        except X402PaymentDeclinedError:
            print("✅ Payment declined when auto_pay=False (expected)")
        except X402PriceExceededError:
            print("❌ Unexpected X402PriceExceededError for price within limit")

        # Price exceeds limit
        try:
            client._validate_payment({"price": 0.10})
            print("❌ Should have raised X402PriceExceededError")
        except X402PriceExceededError as e:
            print(f"✅ Price exceeded error: {e}")

        # Test with auto_pay=True
        config2 = X402Config(
            private_key=X402_PRIVATE_KEY or "0x" + "1" * 64,
            max_price_per_request=0.05,
            auto_pay=True
        )
        client2 = X402Client(config2)

        result = client2._validate_payment({"price": 0.01})
        assert result == True
        print("✅ Payment approved when auto_pay=True and price within limit")

        return True

    except Exception as e:
        print(f"❌ Failed: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_session_spending_tracking():
    """Test session spending tracking."""
    print("\n" + "="*60)
    print("Test 7: Session Spending Tracking")
    print("="*60)

    try:
        from agent0_sdk.core.x402_client import X402Config, X402Client

        config = X402Config(
            private_key=X402_PRIVATE_KEY or "0x" + "1" * 64,
            max_price_per_request=1.0,
            auto_pay=True,
            session_spending_limit=0.50
        )
        client = X402Client(config)

        # Initial spending should be 0
        assert client.get_session_spending() == 0.0
        print("✅ Initial session spending: $0.00")

        # Manually update spending (simulating payments)
        config.session_spending = 0.25
        assert client.get_session_spending() == 0.25
        print("✅ After simulated payment: $0.25")

        # Reset spending
        client.reset_session_spending()
        assert client.get_session_spending() == 0.0
        print("✅ After reset: $0.00")

        return True

    except Exception as e:
        print(f"❌ Failed: {e}")
        return False


def test_payment_approval_callback():
    """Test custom payment approval callback."""
    print("\n" + "="*60)
    print("Test 8: Payment Approval Callback")
    print("="*60)

    try:
        from agent0_sdk.core.x402_client import (
            X402Config, X402Client, X402PaymentDeclinedError
        )

        # Callback that approves payments under $0.05
        def approval_callback(payment_details):
            price = payment_details.get("price", 0)
            if isinstance(price, dict):
                price = float(price.get("amount", 0))
            return float(price) < 0.05

        config = X402Config(
            private_key=X402_PRIVATE_KEY or "0x" + "1" * 64,
            max_price_per_request=1.0,
            auto_pay=False,
            payment_approval_callback=approval_callback
        )
        client = X402Client(config)

        # Test approved payment
        result = client._validate_payment({"price": 0.01})
        assert result == True
        print("✅ Callback approved $0.01 payment")

        # Test declined payment
        try:
            client._validate_payment({"price": 0.10})
            print("❌ Should have raised X402PaymentDeclinedError")
        except X402PaymentDeclinedError:
            print("✅ Callback declined $0.10 payment")

        return True

    except Exception as e:
        print(f"❌ Failed: {e}")
        import traceback
        traceback.print_exc()
        return False


def main():
    """Run all tests."""
    print("\n" + "="*60)
    print("Agent0 SDK x402 Payment Integration Tests")
    print("="*60)

    tests = [
        test_x402_client_initialization,
        test_x402_convenience_function,
        test_sdk_x402_integration,
        test_endpoint_crawler_x402,
        test_agent_x402_inheritance,
        test_price_validation,
        test_session_spending_tracking,
        test_payment_approval_callback,
    ]

    passed = 0
    failed = 0

    for test in tests:
        try:
            if test():
                passed += 1
            else:
                failed += 1
        except Exception as e:
            print(f"❌ Test {test.__name__} crashed: {e}")
            failed += 1

    print("\n" + "="*60)
    print(f"Results: {passed} passed, {failed} failed")
    print("="*60)

    if failed > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
