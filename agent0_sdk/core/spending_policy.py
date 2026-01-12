"""
SpendingPolicy - Flexible Spending Policy Enforcement

Provides configurable policy rules for spending wallets:
- Per-transaction limits
- Time-based limits (daily, weekly, monthly)
- Session limits
- Endpoint whitelist/blacklist
- Deduplication
- Circuit breaker pattern
- Custom approval callbacks

Can be used standalone or with SpendingWallet.

Example usage:

    from agent0_sdk import SpendingPolicy

    policy = SpendingPolicy(
        max_per_transaction=0.10,
        max_per_day=5.0,
        endpoint_whitelist=["api.example.com"],
    )

    # Check if payment is allowed
    result = policy.validate(amount=0.05, endpoint="https://api.example.com/")
    if result.allowed:
        # Proceed with payment
        policy.record_payment(0.05, "https://api.example.com/")
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class PolicyViolationType(Enum):
    """Types of policy violations."""

    TRANSACTION_LIMIT = "transaction_limit"
    DAILY_LIMIT = "daily_limit"
    WEEKLY_LIMIT = "weekly_limit"
    MONTHLY_LIMIT = "monthly_limit"
    SESSION_LIMIT = "session_limit"
    ENDPOINT_NOT_WHITELISTED = "endpoint_not_whitelisted"
    ENDPOINT_BLACKLISTED = "endpoint_blacklisted"
    DUPLICATE_PAYMENT = "duplicate_payment"
    CIRCUIT_BREAKER = "circuit_breaker"
    CONFIRMATION_DECLINED = "confirmation_declined"
    CUSTOM_RULE = "custom_rule"


@dataclass
class PolicyResult:
    """Result of policy validation."""

    allowed: bool
    violation_type: Optional[PolicyViolationType] = None
    message: Optional[str] = None
    details: Optional[Dict[str, Any]] = None

    def __bool__(self) -> bool:
        return self.allowed


@dataclass
class PaymentRecord:
    """Record of a validated payment."""

    timestamp: float
    amount: float
    endpoint: str
    validated: bool = True


@dataclass
class SpendingPolicyConfig:
    """Configuration for spending policy."""

    # Per-transaction limit
    max_per_transaction: float = 0.10  # USD

    # Time-based limits
    max_per_day: float = 5.0  # USD
    max_per_week: float = 20.0  # USD
    max_per_month: float = 50.0  # USD
    max_per_session: Optional[float] = None

    # Endpoint controls
    endpoint_whitelist: Optional[List[str]] = None  # If set, only these allowed
    endpoint_blacklist: Optional[List[str]] = None  # These are never allowed

    # Deduplication
    deduplication_enabled: bool = True
    deduplication_window_seconds: int = 60

    # Circuit breaker
    circuit_breaker_enabled: bool = True
    circuit_breaker_threshold: int = 10  # Failures before trip
    circuit_breaker_window_seconds: int = 300  # 5 minutes
    circuit_breaker_cooldown_seconds: int = 600  # 10 minutes

    # Confirmation
    require_confirmation_above: float = 0.50  # USD
    confirmation_callback: Optional[Callable[[Dict], bool]] = None

    # Custom rules
    custom_validators: Optional[List[Callable[[float, str], bool]]] = None


class SpendingPolicy:
    """
    Flexible spending policy enforcement.

    Provides multiple layers of protection for spending wallets.
    """

    def __init__(self, config: Optional[SpendingPolicyConfig] = None, **kwargs):
        """
        Initialize spending policy.

        Args:
            config: SpendingPolicyConfig object
            **kwargs: Can also pass config values directly

        Example:
            # Using config object
            policy = SpendingPolicy(SpendingPolicyConfig(max_per_day=10.0))

            # Using kwargs
            policy = SpendingPolicy(max_per_day=10.0, max_per_week=50.0)
        """
        if config is None:
            config = SpendingPolicyConfig(**kwargs)
        self.config = config

        # State
        self._payment_history: List[PaymentRecord] = []
        self._session_spending: float = 0.0
        self._failure_count: int = 0
        self._failure_window_start: float = 0.0
        self._circuit_breaker_trip_time: Optional[float] = None
        self._recent_payments: List[Tuple[float, str, float]] = []

    def validate(
        self,
        amount: float,
        endpoint: str,
        skip_confirmation: bool = False,
    ) -> PolicyResult:
        """
        Validate a payment against all policy rules.

        Args:
            amount: Payment amount in USD
            endpoint: Target endpoint URL
            skip_confirmation: Skip confirmation callback

        Returns:
            PolicyResult indicating if payment is allowed

        Example:
            result = policy.validate(0.05, "https://api.example.com/")
            if result.allowed:
                # Proceed with payment
                pass
            else:
                print(f"Blocked: {result.message}")
        """
        # Check circuit breaker first
        if self.config.circuit_breaker_enabled:
            result = self._check_circuit_breaker()
            if not result.allowed:
                return result

        # Check endpoint blacklist
        if self.config.endpoint_blacklist:
            result = self._check_blacklist(endpoint)
            if not result.allowed:
                return result

        # Check endpoint whitelist
        if self.config.endpoint_whitelist:
            result = self._check_whitelist(endpoint)
            if not result.allowed:
                return result

        # Check per-transaction limit
        if amount > self.config.max_per_transaction:
            return PolicyResult(
                allowed=False,
                violation_type=PolicyViolationType.TRANSACTION_LIMIT,
                message=(
                    f"Amount ${amount:.4f} exceeds per-transaction "
                    f"limit ${self.config.max_per_transaction:.2f}"
                ),
                details={
                    "amount": amount,
                    "limit": self.config.max_per_transaction,
                },
            )

        # Check daily limit
        daily_spending = self.get_spending("day")
        if daily_spending + amount > self.config.max_per_day:
            return PolicyResult(
                allowed=False,
                violation_type=PolicyViolationType.DAILY_LIMIT,
                message=(
                    f"Would exceed daily limit. Current: ${daily_spending:.2f}, "
                    f"Request: ${amount:.4f}, Limit: ${self.config.max_per_day:.2f}"
                ),
                details={
                    "current": daily_spending,
                    "amount": amount,
                    "limit": self.config.max_per_day,
                },
            )

        # Check weekly limit
        weekly_spending = self.get_spending("week")
        if weekly_spending + amount > self.config.max_per_week:
            return PolicyResult(
                allowed=False,
                violation_type=PolicyViolationType.WEEKLY_LIMIT,
                message=(
                    f"Would exceed weekly limit. Current: ${weekly_spending:.2f}, "
                    f"Request: ${amount:.4f}, Limit: ${self.config.max_per_week:.2f}"
                ),
                details={
                    "current": weekly_spending,
                    "amount": amount,
                    "limit": self.config.max_per_week,
                },
            )

        # Check monthly limit
        monthly_spending = self.get_spending("month")
        if monthly_spending + amount > self.config.max_per_month:
            return PolicyResult(
                allowed=False,
                violation_type=PolicyViolationType.MONTHLY_LIMIT,
                message=(
                    f"Would exceed monthly limit. Current: ${monthly_spending:.2f}, "
                    f"Request: ${amount:.4f}, Limit: ${self.config.max_per_month:.2f}"
                ),
                details={
                    "current": monthly_spending,
                    "amount": amount,
                    "limit": self.config.max_per_month,
                },
            )

        # Check session limit
        if self.config.max_per_session is not None:
            if self._session_spending + amount > self.config.max_per_session:
                return PolicyResult(
                    allowed=False,
                    violation_type=PolicyViolationType.SESSION_LIMIT,
                    message=(
                        f"Would exceed session limit. Current: "
                        f"${self._session_spending:.2f}, Request: ${amount:.4f}, "
                        f"Limit: ${self.config.max_per_session:.2f}"
                    ),
                    details={
                        "current": self._session_spending,
                        "amount": amount,
                        "limit": self.config.max_per_session,
                    },
                )

        # Check deduplication
        if self.config.deduplication_enabled:
            result = self._check_deduplication(amount, endpoint)
            if not result.allowed:
                return result

        # Check custom validators
        if self.config.custom_validators:
            for validator in self.config.custom_validators:
                try:
                    if not validator(amount, endpoint):
                        return PolicyResult(
                            allowed=False,
                            violation_type=PolicyViolationType.CUSTOM_RULE,
                            message="Payment blocked by custom validator",
                        )
                except Exception as e:
                    logger.warning("Custom validator error: %s", e)

        # Check confirmation
        if not skip_confirmation and amount > self.config.require_confirmation_above:
            result = self._check_confirmation(amount, endpoint)
            if not result.allowed:
                return result

        return PolicyResult(allowed=True)

    def _check_circuit_breaker(self) -> PolicyResult:
        """Check if circuit breaker is tripped."""
        current_time = time.time()

        # Check if in cooldown
        if self._circuit_breaker_trip_time is not None:
            cooldown_elapsed = current_time - self._circuit_breaker_trip_time
            if cooldown_elapsed < self.config.circuit_breaker_cooldown_seconds:
                remaining = (
                    self.config.circuit_breaker_cooldown_seconds - cooldown_elapsed
                )
                return PolicyResult(
                    allowed=False,
                    violation_type=PolicyViolationType.CIRCUIT_BREAKER,
                    message=f"Circuit breaker active. Cooldown remaining: {remaining:.0f}s",
                    details={
                        "trip_time": self._circuit_breaker_trip_time,
                        "cooldown_remaining": remaining,
                    },
                )
            else:
                # Cooldown expired, reset
                self._circuit_breaker_trip_time = None
                self._failure_count = 0

        # Check failure count
        if (
            current_time - self._failure_window_start
            > self.config.circuit_breaker_window_seconds
        ):
            self._failure_count = 0
            self._failure_window_start = current_time

        if self._failure_count >= self.config.circuit_breaker_threshold:
            self._circuit_breaker_trip_time = current_time
            return PolicyResult(
                allowed=False,
                violation_type=PolicyViolationType.CIRCUIT_BREAKER,
                message=f"Circuit breaker tripped after {self._failure_count} failures",
                details={
                    "failures": self._failure_count,
                    "threshold": self.config.circuit_breaker_threshold,
                },
            )

        return PolicyResult(allowed=True)

    def _check_whitelist(self, endpoint: str) -> PolicyResult:
        """Check endpoint against whitelist."""
        for allowed in self.config.endpoint_whitelist:
            if allowed in endpoint:
                return PolicyResult(allowed=True)

        return PolicyResult(
            allowed=False,
            violation_type=PolicyViolationType.ENDPOINT_NOT_WHITELISTED,
            message=f"Endpoint '{endpoint}' is not in whitelist",
            details={
                "endpoint": endpoint,
                "whitelist": self.config.endpoint_whitelist,
            },
        )

    def _check_blacklist(self, endpoint: str) -> PolicyResult:
        """Check endpoint against blacklist."""
        for blocked in self.config.endpoint_blacklist:
            if blocked in endpoint:
                return PolicyResult(
                    allowed=False,
                    violation_type=PolicyViolationType.ENDPOINT_BLACKLISTED,
                    message=f"Endpoint '{endpoint}' is blacklisted",
                    details={
                        "endpoint": endpoint,
                        "matched": blocked,
                    },
                )
        return PolicyResult(allowed=True)

    def _check_deduplication(self, amount: float, endpoint: str) -> PolicyResult:
        """Check for duplicate payment."""
        current_time = time.time()
        cutoff = current_time - self.config.deduplication_window_seconds

        # Clean old entries
        self._recent_payments = [
            (t, e, a) for t, e, a in self._recent_payments if t > cutoff
        ]

        # Check for duplicate
        for t, e, a in self._recent_payments:
            if e == endpoint and abs(a - amount) < 0.0001:
                return PolicyResult(
                    allowed=False,
                    violation_type=PolicyViolationType.DUPLICATE_PAYMENT,
                    message=(
                        f"Duplicate payment detected within "
                        f"{self.config.deduplication_window_seconds}s"
                    ),
                    details={
                        "endpoint": endpoint,
                        "amount": amount,
                        "previous_time": t,
                    },
                )

        return PolicyResult(allowed=True)

    def _check_confirmation(self, amount: float, endpoint: str) -> PolicyResult:
        """Check confirmation for large payments."""
        if self.config.confirmation_callback is None:
            logger.warning(
                "Payment of $%.4f exceeds confirmation threshold $%.2f "
                "but no confirmation_callback configured",
                amount,
                self.config.require_confirmation_above,
            )
            return PolicyResult(allowed=True)

        try:
            confirmed = self.config.confirmation_callback(
                {
                    "amount": amount,
                    "endpoint": endpoint,
                    "daily_spending": self.get_spending("day"),
                    "weekly_spending": self.get_spending("week"),
                }
            )
            if not confirmed:
                return PolicyResult(
                    allowed=False,
                    violation_type=PolicyViolationType.CONFIRMATION_DECLINED,
                    message=f"Payment of ${amount:.4f} was not confirmed",
                )
        except Exception as e:
            logger.error("Confirmation callback error: %s", e)
            return PolicyResult(
                allowed=False,
                violation_type=PolicyViolationType.CONFIRMATION_DECLINED,
                message=f"Confirmation callback error: {e}",
            )

        return PolicyResult(allowed=True)

    def record_payment(self, amount: float, endpoint: str) -> None:
        """
        Record a successful payment.

        Args:
            amount: Payment amount in USD
            endpoint: Target endpoint
        """
        current_time = time.time()

        self._payment_history.append(
            PaymentRecord(timestamp=current_time, amount=amount, endpoint=endpoint)
        )
        self._session_spending += amount
        self._recent_payments.append((current_time, endpoint, amount))

        logger.info(
            "Payment recorded: $%.4f to %s (daily: $%.2f)",
            amount,
            endpoint,
            self.get_spending("day"),
        )

    def record_failure(self, error: str) -> None:
        """
        Record a payment failure.

        Args:
            error: Error message
        """
        current_time = time.time()

        if (
            current_time - self._failure_window_start
            > self.config.circuit_breaker_window_seconds
        ):
            self._failure_count = 1
            self._failure_window_start = current_time
        else:
            self._failure_count += 1

        logger.warning(
            "Payment failure: %s (failures: %d/%d)",
            error,
            self._failure_count,
            self.config.circuit_breaker_threshold,
        )

    def get_spending(self, period: str = "day") -> float:
        """
        Get spending for a time period.

        Args:
            period: "day", "week", "month", "session", or "all"

        Returns:
            Total spending in USD
        """
        if period == "session":
            return self._session_spending

        if period == "all":
            return sum(r.amount for r in self._payment_history if r.validated)

        # Calculate time cutoffs
        now = datetime.now()
        if period == "day":
            cutoff = now.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
        elif period == "week":
            cutoff = (now - timedelta(days=now.weekday())).replace(
                hour=0, minute=0, second=0, microsecond=0
            ).timestamp()
        elif period == "month":
            cutoff = now.replace(
                day=1, hour=0, minute=0, second=0, microsecond=0
            ).timestamp()
        else:
            raise ValueError(f"Unknown period: {period}")

        return sum(
            r.amount
            for r in self._payment_history
            if r.timestamp >= cutoff and r.validated
        )

    def get_remaining(self, period: str = "day") -> float:
        """
        Get remaining budget for a period.

        Args:
            period: "day", "week", "month", or "session"

        Returns:
            Remaining budget in USD
        """
        spent = self.get_spending(period)
        if period == "day":
            return max(0, self.config.max_per_day - spent)
        elif period == "week":
            return max(0, self.config.max_per_week - spent)
        elif period == "month":
            return max(0, self.config.max_per_month - spent)
        elif period == "session" and self.config.max_per_session:
            return max(0, self.config.max_per_session - spent)
        return 0.0

    def reset_session(self) -> None:
        """Reset session spending."""
        self._session_spending = 0.0
        logger.info("Session spending reset")

    def reset_circuit_breaker(self) -> None:
        """Reset the circuit breaker."""
        self._failure_count = 0
        self._failure_window_start = time.time()
        self._circuit_breaker_trip_time = None
        logger.info("Circuit breaker reset")

    def get_stats(self) -> Dict[str, Any]:
        """Get policy statistics."""
        return {
            "daily_spending": self.get_spending("day"),
            "daily_limit": self.config.max_per_day,
            "daily_remaining": self.get_remaining("day"),
            "weekly_spending": self.get_spending("week"),
            "weekly_limit": self.config.max_per_week,
            "weekly_remaining": self.get_remaining("week"),
            "monthly_spending": self.get_spending("month"),
            "monthly_limit": self.config.max_per_month,
            "monthly_remaining": self.get_remaining("month"),
            "session_spending": self._session_spending,
            "session_limit": self.config.max_per_session,
            "failure_count": self._failure_count,
            "circuit_breaker_threshold": self.config.circuit_breaker_threshold,
            "circuit_breaker_tripped": self._circuit_breaker_trip_time is not None,
            "total_payments": len(self._payment_history),
        }

    def __repr__(self) -> str:
        return (
            f"SpendingPolicy(daily=${self.get_spending('day'):.2f}"
            f"/${self.config.max_per_day:.2f})"
        )
