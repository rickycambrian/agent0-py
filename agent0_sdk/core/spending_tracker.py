"""
SpendingTracker - Persistent Spending History Storage

Provides persistent storage for spending records across sessions:
- SQLite database for local storage
- JSON file backup
- Query by time period, endpoint, or amount
- Export/import functionality

Example usage:

    from agent0_sdk import SpendingTracker

    tracker = SpendingTracker("~/.agent0/spending.db")

    # Record payments
    tracker.record(amount=0.05, endpoint="api.example.com", tx_hash="0x...")

    # Query history
    today = tracker.get_spending_for_period("day")
    history = tracker.get_history(limit=100)

    # Export
    data = tracker.export()
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Generator, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class TrackedPayment:
    """A tracked payment record."""

    id: Optional[int]
    timestamp: float
    amount: float
    endpoint: str
    wallet_address: str
    tx_hash: Optional[str] = None
    method: Optional[str] = None
    tool_name: Optional[str] = None
    success: bool = True
    error: Optional[str] = None
    network: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "id": self.id,
            "timestamp": self.timestamp,
            "datetime": datetime.fromtimestamp(self.timestamp).isoformat(),
            "amount": self.amount,
            "endpoint": self.endpoint,
            "wallet_address": self.wallet_address,
            "tx_hash": self.tx_hash,
            "method": self.method,
            "tool_name": self.tool_name,
            "success": self.success,
            "error": self.error,
            "network": self.network,
            "metadata": self.metadata,
        }


class SpendingTracker:
    """
    Persistent spending history tracker.

    Uses SQLite for reliable local storage with JSON export support.
    """

    def __init__(
        self,
        storage_path: Optional[str] = None,
        wallet_address: Optional[str] = None,
    ):
        """
        Initialize spending tracker.

        Args:
            storage_path: Path to SQLite database (default: ~/.agent0/spending.db)
            wallet_address: Default wallet address for tracking
        """
        if storage_path is None:
            storage_path = os.path.expanduser("~/.agent0/spending.db")

        self.storage_path = Path(storage_path)
        self.wallet_address = wallet_address

        # Ensure directory exists
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)

        # Initialize database
        self._init_db()

        logger.info("SpendingTracker initialized: %s", self.storage_path)

    def _init_db(self) -> None:
        """Initialize the SQLite database."""
        with self._get_connection() as conn:
            cursor = conn.cursor()

            # Create payments table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS payments (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp REAL NOT NULL,
                    amount REAL NOT NULL,
                    endpoint TEXT NOT NULL,
                    wallet_address TEXT NOT NULL,
                    tx_hash TEXT,
                    method TEXT,
                    tool_name TEXT,
                    success INTEGER DEFAULT 1,
                    error TEXT,
                    network TEXT,
                    metadata TEXT
                )
            """)

            # Create indexes for common queries
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_payments_timestamp
                ON payments (timestamp)
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_payments_wallet
                ON payments (wallet_address)
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_payments_endpoint
                ON payments (endpoint)
            """)

            conn.commit()

    @contextmanager
    def _get_connection(self) -> Generator[sqlite3.Connection, None, None]:
        """Get a database connection."""
        conn = sqlite3.connect(str(self.storage_path))
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    def record(
        self,
        amount: float,
        endpoint: str,
        wallet_address: Optional[str] = None,
        tx_hash: Optional[str] = None,
        method: Optional[str] = None,
        tool_name: Optional[str] = None,
        success: bool = True,
        error: Optional[str] = None,
        network: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        timestamp: Optional[float] = None,
    ) -> int:
        """
        Record a payment.

        Args:
            amount: Payment amount in USD
            endpoint: Target endpoint URL
            wallet_address: Wallet address (uses default if not provided)
            tx_hash: Transaction hash
            method: Request method (e.g., "tools/call")
            tool_name: Tool name
            success: Whether payment succeeded
            error: Error message if failed
            network: Network used
            metadata: Additional metadata
            timestamp: Custom timestamp (uses current time if not provided)

        Returns:
            Record ID

        Example:
            record_id = tracker.record(
                amount=0.05,
                endpoint="https://api.example.com/",
                tx_hash="0x123...",
                tool_name="generate_text"
            )
        """
        wallet = wallet_address or self.wallet_address or "unknown"
        ts = timestamp or time.time()
        meta_json = json.dumps(metadata) if metadata else None

        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO payments
                (timestamp, amount, endpoint, wallet_address, tx_hash, method,
                 tool_name, success, error, network, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    ts,
                    amount,
                    endpoint,
                    wallet,
                    tx_hash,
                    method,
                    tool_name,
                    1 if success else 0,
                    error,
                    network,
                    meta_json,
                ),
            )
            conn.commit()
            record_id = cursor.lastrowid

        logger.debug(
            "Recorded payment #%d: $%.4f to %s", record_id, amount, endpoint
        )
        return record_id

    def get_spending_for_period(
        self,
        period: str = "day",
        wallet_address: Optional[str] = None,
    ) -> float:
        """
        Get total spending for a time period.

        Args:
            period: "day", "week", "month", or "all"
            wallet_address: Filter by wallet address

        Returns:
            Total spending in USD
        """
        wallet = wallet_address or self.wallet_address

        # Calculate cutoff timestamp
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
        elif period == "all":
            cutoff = 0
        else:
            raise ValueError(f"Unknown period: {period}")

        with self._get_connection() as conn:
            cursor = conn.cursor()

            if wallet:
                cursor.execute(
                    """
                    SELECT COALESCE(SUM(amount), 0) as total
                    FROM payments
                    WHERE timestamp >= ? AND wallet_address = ? AND success = 1
                    """,
                    (cutoff, wallet),
                )
            else:
                cursor.execute(
                    """
                    SELECT COALESCE(SUM(amount), 0) as total
                    FROM payments
                    WHERE timestamp >= ? AND success = 1
                    """,
                    (cutoff,),
                )

            row = cursor.fetchone()
            return float(row["total"]) if row else 0.0

    def get_history(
        self,
        limit: int = 100,
        offset: int = 0,
        wallet_address: Optional[str] = None,
        endpoint: Optional[str] = None,
        success_only: bool = False,
        start_time: Optional[float] = None,
        end_time: Optional[float] = None,
    ) -> List[TrackedPayment]:
        """
        Get payment history.

        Args:
            limit: Maximum records to return
            offset: Records to skip
            wallet_address: Filter by wallet address
            endpoint: Filter by endpoint (partial match)
            success_only: Only return successful payments
            start_time: Filter by start timestamp
            end_time: Filter by end timestamp

        Returns:
            List of TrackedPayment records
        """
        wallet = wallet_address or self.wallet_address

        conditions = []
        params = []

        if wallet:
            conditions.append("wallet_address = ?")
            params.append(wallet)

        if endpoint:
            conditions.append("endpoint LIKE ?")
            params.append(f"%{endpoint}%")

        if success_only:
            conditions.append("success = 1")

        if start_time:
            conditions.append("timestamp >= ?")
            params.append(start_time)

        if end_time:
            conditions.append("timestamp <= ?")
            params.append(end_time)

        where_clause = " AND ".join(conditions) if conditions else "1=1"
        params.extend([limit, offset])

        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                f"""
                SELECT * FROM payments
                WHERE {where_clause}
                ORDER BY timestamp DESC
                LIMIT ? OFFSET ?
                """,
                params,
            )

            rows = cursor.fetchall()
            return [self._row_to_payment(row) for row in rows]

    def _row_to_payment(self, row: sqlite3.Row) -> TrackedPayment:
        """Convert database row to TrackedPayment."""
        metadata = None
        if row["metadata"]:
            try:
                metadata = json.loads(row["metadata"])
            except json.JSONDecodeError:
                pass

        return TrackedPayment(
            id=row["id"],
            timestamp=row["timestamp"],
            amount=row["amount"],
            endpoint=row["endpoint"],
            wallet_address=row["wallet_address"],
            tx_hash=row["tx_hash"],
            method=row["method"],
            tool_name=row["tool_name"],
            success=bool(row["success"]),
            error=row["error"],
            network=row["network"],
            metadata=metadata,
        )

    def get_stats(self, wallet_address: Optional[str] = None) -> Dict[str, Any]:
        """
        Get spending statistics.

        Args:
            wallet_address: Filter by wallet address

        Returns:
            Dictionary with spending stats
        """
        wallet = wallet_address or self.wallet_address

        with self._get_connection() as conn:
            cursor = conn.cursor()

            # Base conditions
            wallet_cond = "wallet_address = ?" if wallet else "1=1"
            params = [wallet] if wallet else []

            # Total payments
            cursor.execute(
                f"SELECT COUNT(*) as count FROM payments WHERE {wallet_cond}",
                params,
            )
            total_payments = cursor.fetchone()["count"]

            # Successful payments
            cursor.execute(
                f"SELECT COUNT(*) as count FROM payments WHERE {wallet_cond} AND success = 1",
                params,
            )
            successful_payments = cursor.fetchone()["count"]

            # Failed payments
            cursor.execute(
                f"SELECT COUNT(*) as count FROM payments WHERE {wallet_cond} AND success = 0",
                params,
            )
            failed_payments = cursor.fetchone()["count"]

            # Total spent
            cursor.execute(
                f"SELECT COALESCE(SUM(amount), 0) as total FROM payments "
                f"WHERE {wallet_cond} AND success = 1",
                params,
            )
            total_spent = cursor.fetchone()["total"]

            # Unique endpoints
            cursor.execute(
                f"SELECT COUNT(DISTINCT endpoint) as count FROM payments WHERE {wallet_cond}",
                params,
            )
            unique_endpoints = cursor.fetchone()["count"]

        return {
            "total_payments": total_payments,
            "successful_payments": successful_payments,
            "failed_payments": failed_payments,
            "total_spent": total_spent,
            "daily_spending": self.get_spending_for_period("day", wallet),
            "weekly_spending": self.get_spending_for_period("week", wallet),
            "monthly_spending": self.get_spending_for_period("month", wallet),
            "unique_endpoints": unique_endpoints,
        }

    def get_spending_by_endpoint(
        self,
        wallet_address: Optional[str] = None,
        period: str = "all",
    ) -> Dict[str, float]:
        """
        Get spending grouped by endpoint.

        Args:
            wallet_address: Filter by wallet address
            period: Time period ("day", "week", "month", "all")

        Returns:
            Dictionary mapping endpoint to total spending
        """
        wallet = wallet_address or self.wallet_address

        # Calculate cutoff
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
            cutoff = 0

        conditions = ["timestamp >= ?", "success = 1"]
        params = [cutoff]

        if wallet:
            conditions.append("wallet_address = ?")
            params.append(wallet)

        where_clause = " AND ".join(conditions)

        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                f"""
                SELECT endpoint, SUM(amount) as total
                FROM payments
                WHERE {where_clause}
                GROUP BY endpoint
                ORDER BY total DESC
                """,
                params,
            )

            return {row["endpoint"]: row["total"] for row in cursor.fetchall()}

    def export(
        self,
        wallet_address: Optional[str] = None,
        output_path: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Export spending data.

        Args:
            wallet_address: Filter by wallet address
            output_path: Optional path to write JSON file

        Returns:
            Dictionary with all spending data
        """
        wallet = wallet_address or self.wallet_address
        history = self.get_history(limit=10000, wallet_address=wallet)
        stats = self.get_stats(wallet)

        export_data = {
            "exported_at": datetime.now().isoformat(),
            "wallet_address": wallet,
            "stats": stats,
            "history": [p.to_dict() for p in history],
        }

        if output_path:
            path = Path(output_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "w") as f:
                json.dump(export_data, f, indent=2)
            logger.info("Exported spending data to %s", output_path)

        return export_data

    def import_data(self, data: Dict[str, Any]) -> int:
        """
        Import spending data from export.

        Args:
            data: Export data dictionary

        Returns:
            Number of records imported
        """
        imported = 0
        for record in data.get("history", []):
            self.record(
                amount=record["amount"],
                endpoint=record["endpoint"],
                wallet_address=record.get("wallet_address"),
                tx_hash=record.get("tx_hash"),
                method=record.get("method"),
                tool_name=record.get("tool_name"),
                success=record.get("success", True),
                error=record.get("error"),
                network=record.get("network"),
                metadata=record.get("metadata"),
                timestamp=record.get("timestamp"),
            )
            imported += 1

        logger.info("Imported %d spending records", imported)
        return imported

    def clear(self, wallet_address: Optional[str] = None) -> int:
        """
        Clear spending history.

        Args:
            wallet_address: Only clear for this wallet (clears all if None)

        Returns:
            Number of records deleted
        """
        wallet = wallet_address or self.wallet_address

        with self._get_connection() as conn:
            cursor = conn.cursor()

            if wallet:
                cursor.execute(
                    "DELETE FROM payments WHERE wallet_address = ?", (wallet,)
                )
            else:
                cursor.execute("DELETE FROM payments")

            deleted = cursor.rowcount
            conn.commit()

        logger.info("Cleared %d spending records", deleted)
        return deleted

    def __repr__(self) -> str:
        return f"SpendingTracker(path={self.storage_path})"
