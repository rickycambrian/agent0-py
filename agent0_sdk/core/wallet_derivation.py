"""
Wallet Derivation for Agent0 Spending Wallets

HD wallet derivation using BIP-39 and BIP-44 standards with a custom
derivation path for Agent0 spending wallets.

Derivation Path: m/44'/60'/8004'/0/{index}
- 44' = BIP-44
- 60' = Ethereum
- 8004' = ERC-8004 (Agent0's standard)
- 0 = external chain
- {index} = wallet index

This allows users to:
1. Derive multiple spending wallets from a single seed phrase
2. Recover wallets if keys are lost
3. Keep spending wallets isolated from main funds

Example usage:

    from agent0_sdk.core.wallet_derivation import (
        generate_seed_phrase,
        derive_spending_wallet,
        validate_seed_phrase,
    )

    # Generate new seed phrase
    seed = generate_seed_phrase()
    print(f"Save this: {seed}")

    # Derive wallets
    pk1, addr1 = derive_spending_wallet(seed, 0)  # First wallet
    pk2, addr2 = derive_spending_wallet(seed, 1)  # Second wallet

    # Validate existing phrase
    if validate_seed_phrase("your existing phrase..."):
        pk, addr = derive_spending_wallet("your existing phrase...", 0)
"""

from __future__ import annotations

import logging
import secrets
from typing import Tuple

logger = logging.getLogger(__name__)


# Agent0 spending wallet derivation path
# m/44'/60'/8004'/0/{index}
# Using 8004 as the "coin type" slot for ERC-8004 agents
AGENT0_DERIVATION_PATH = "m/44'/60'/8004'/0"


def generate_seed_phrase(strength: int = 128) -> str:
    """
    Generate a new BIP-39 seed phrase for spending wallets.

    Args:
        strength: Entropy bits (128 = 12 words, 256 = 24 words)

    Returns:
        BIP-39 mnemonic phrase

    Example:
        seed = generate_seed_phrase()
        # Returns: "word1 word2 word3 ... word12"
    """
    try:
        from mnemonic import Mnemonic

        mnemo = Mnemonic("english")
        return mnemo.generate(strength=strength)

    except ImportError:
        # Fallback: generate using eth_account if mnemonic not available
        try:
            from eth_account import Account

            Account.enable_unaudited_hdwallet_features()
            acct, mnemonic = Account.create_with_mnemonic()
            return mnemonic

        except ImportError:
            raise ImportError(
                "Neither 'mnemonic' nor 'eth_account' package is installed. "
                "Install with: pip install mnemonic eth_account"
            )


def validate_seed_phrase(phrase: str) -> bool:
    """
    Validate a BIP-39 seed phrase.

    Args:
        phrase: Seed phrase to validate

    Returns:
        True if valid BIP-39 mnemonic

    Example:
        if validate_seed_phrase("your phrase here"):
            # Safe to use
            pass
    """
    try:
        from mnemonic import Mnemonic

        mnemo = Mnemonic("english")
        return mnemo.check(phrase)

    except ImportError:
        # Fallback: basic validation
        words = phrase.strip().split()
        if len(words) not in [12, 15, 18, 21, 24]:
            return False
        return True


def derive_spending_wallet(
    seed_phrase: str,
    index: int = 0,
) -> Tuple[str, str]:
    """
    Derive a spending wallet from seed phrase.

    Uses the Agent0 derivation path: m/44'/60'/8004'/0/{index}

    Args:
        seed_phrase: BIP-39 mnemonic phrase
        index: Wallet index (0, 1, 2, ...)

    Returns:
        Tuple of (private_key, address)

    Example:
        private_key, address = derive_spending_wallet(
            "your twelve word seed phrase here...",
            index=0
        )
    """
    try:
        from eth_account import Account

        Account.enable_unaudited_hdwallet_features()

        # Full derivation path
        path = f"{AGENT0_DERIVATION_PATH}/{index}"

        # Derive account from mnemonic
        acct = Account.from_mnemonic(seed_phrase, account_path=path)

        private_key = acct.key.hex()
        if not private_key.startswith("0x"):
            private_key = "0x" + private_key

        logger.debug(
            "Derived spending wallet %d: %s (path: %s)", index, acct.address, path
        )

        return private_key, acct.address

    except ImportError:
        raise ImportError(
            "eth_account package is required for HD wallet derivation. "
            "Install with: pip install eth_account"
        )


def generate_spending_wallet() -> Tuple[str, str]:
    """
    Generate a new random spending wallet.

    This creates a standalone wallet without HD derivation.
    Use generate_seed_phrase() + derive_spending_wallet() for recoverable wallets.

    Returns:
        Tuple of (private_key, address)

    Example:
        private_key, address = generate_spending_wallet()
        print(f"New wallet: {address}")
        print(f"Save this key securely: {private_key}")
    """
    try:
        from eth_account import Account

        # Generate random private key
        private_key = "0x" + secrets.token_hex(32)
        account = Account.from_key(private_key)

        logger.info(
            "Generated new spending wallet: %s", account.address
        )

        return private_key, account.address

    except ImportError:
        raise ImportError(
            "eth_account package is required for wallet generation. "
            "Install with: pip install eth_account"
        )


def derive_multiple_wallets(
    seed_phrase: str,
    count: int = 5,
    start_index: int = 0,
) -> list:
    """
    Derive multiple spending wallets from a seed phrase.

    Args:
        seed_phrase: BIP-39 mnemonic phrase
        count: Number of wallets to derive
        start_index: Starting wallet index

    Returns:
        List of (index, private_key, address) tuples

    Example:
        wallets = derive_multiple_wallets(seed, count=3)
        for idx, pk, addr in wallets:
            print(f"Wallet {idx}: {addr}")
    """
    wallets = []
    for i in range(start_index, start_index + count):
        private_key, address = derive_spending_wallet(seed_phrase, i)
        wallets.append((i, private_key, address))
    return wallets


def get_derivation_path(index: int = 0) -> str:
    """
    Get the full derivation path for an index.

    Args:
        index: Wallet index

    Returns:
        Full BIP-44 derivation path

    Example:
        path = get_derivation_path(0)
        # Returns: "m/44'/60'/8004'/0/0"
    """
    return f"{AGENT0_DERIVATION_PATH}/{index}"


def private_key_to_address(private_key: str) -> str:
    """
    Convert a private key to its corresponding address.

    Args:
        private_key: Private key (with or without 0x prefix)

    Returns:
        Ethereum address

    Example:
        addr = private_key_to_address("0x...")
    """
    try:
        from eth_account import Account

        account = Account.from_key(private_key)
        return account.address

    except ImportError:
        raise ImportError(
            "eth_account package is required. "
            "Install with: pip install eth_account"
        )
