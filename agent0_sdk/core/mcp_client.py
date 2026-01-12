"""
MCP Client for Runtime Tool Calls with x402 Payment Support

Provides a high-level client for interacting with MCP servers at runtime,
including automatic payment handling for x402-enabled servers.

This is the runtime counterpart to the EndpointCrawler (which handles discovery).
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional, TYPE_CHECKING

import requests

if TYPE_CHECKING:
    from .x402_client import X402Client

logger = logging.getLogger(__name__)


# JSON-RPC request ID counter
_request_id = 0


def _next_request_id() -> int:
    """Generate next JSON-RPC request ID."""
    global _request_id
    _request_id += 1
    return _request_id


class MCPError(Exception):
    """Base exception for MCP client errors."""

    pass


class MCPToolCallError(MCPError):
    """Raised when a tool call fails."""

    def __init__(self, message: str, code: Optional[int] = None, data: Any = None):
        super().__init__(message)
        self.code = code
        self.data = data


class MCPConnectionError(MCPError):
    """Raised when connection to MCP server fails."""

    pass


class MCPPaymentRequiredError(MCPError):
    """Raised when payment is required but not configured."""

    pass


class MCPClient:
    """
    Runtime MCP client with x402 payment support.

    Enables agents to make paid tool calls to x402-enabled MCP servers.
    Handles JSON-RPC protocol, SSE responses, and automatic payments.

    Example usage:

        from agent0_sdk import SDK

        sdk = SDK(
            chainId=11155111,
            rpcUrl="...",
            signer="...",
            x402PrivateKey="0x...",
            x402AutoPay=True,
            x402MaxPricePerRequest=0.10
        )

        agent = sdk.loadAgent("11155111:123")
        mcp = agent.getMCPClient()

        # Call a paid tool
        result = mcp.call_tool("generate_text", {"prompt": "Hello world"})
        print(f"Result: {result}")
        print(f"Session spending: ${mcp.get_session_spending()}")
    """

    def __init__(
        self,
        endpoint: str,
        x402_client: Optional["X402Client"] = None,
        timeout: int = 30,
    ):
        """
        Initialize MCP client.

        Args:
            endpoint: MCP server endpoint URL
            x402_client: Optional x402 client for payment-enabled servers
            timeout: Request timeout in seconds (default: 30)
        """
        if not endpoint:
            raise ValueError("MCP endpoint URL is required")

        self.endpoint = endpoint.rstrip("/")
        self._x402_client = x402_client
        self.timeout = timeout
        self._initialized = False
        self._server_info: Optional[Dict] = None
        self._available_tools: Optional[List[Dict]] = None

    @property
    def payments_enabled(self) -> bool:
        """Check if x402 payments are enabled."""
        return self._x402_client is not None and self._x402_client.payments_enabled

    def _make_request(
        self,
        method: str,
        params: Optional[Dict] = None,
    ) -> Any:
        """
        Make a JSON-RPC request to the MCP server.

        Args:
            method: JSON-RPC method name
            params: Optional parameters

        Returns:
            Result from the server

        Raises:
            MCPError: On various failure conditions
        """
        payload = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params or {},
            "id": _next_request_id(),
        }

        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }

        try:
            # Use x402 client if available for payment handling
            if self._x402_client:
                response = self._x402_client.post(
                    self.endpoint,
                    json=payload,
                    headers=headers,
                    timeout=self.timeout,
                    stream=True,
                    handle_402=True,
                )
            else:
                response = requests.post(
                    self.endpoint,
                    json=payload,
                    headers=headers,
                    timeout=self.timeout,
                    stream=True,
                )

            # Handle 402 Payment Required
            if response.status_code == 402:
                raise MCPPaymentRequiredError(
                    "MCP server requires payment but x402 is not configured. "
                    "Initialize SDK with x402PrivateKey to enable payments."
                )

            # Handle other error status codes
            if response.status_code != 200:
                raise MCPConnectionError(
                    f"MCP server returned status {response.status_code}: "
                    f"{response.text[:200]}"
                )

            # Parse response (handle both JSON and SSE)
            content_type = response.headers.get("content-type", "")
            if "text/event-stream" in content_type:
                return self._parse_sse_response(response.text)
            else:
                return self._parse_json_response(response.json())

        except requests.exceptions.Timeout:
            raise MCPConnectionError(
                "Request to MCP server timed out after %ds" % self.timeout
            )
        except requests.exceptions.ConnectionError as e:
            raise MCPConnectionError(f"Failed to connect to MCP server: {e}")
        except json.JSONDecodeError as e:
            raise MCPError(f"Invalid JSON response from MCP server: {e}")

    def _parse_json_response(self, data: Dict) -> Any:
        """Parse a JSON-RPC response."""
        if "error" in data:
            error = data["error"]
            raise MCPToolCallError(
                message=error.get("message", "Unknown error"),
                code=error.get("code"),
                data=error.get("data"),
            )

        return data.get("result")

    def _parse_sse_response(self, sse_text: str) -> Any:
        """Parse a Server-Sent Events response."""
        for line in sse_text.split("\n"):
            if line.startswith("data: "):
                json_str = line[6:]  # Remove "data: " prefix
                try:
                    data = json.loads(json_str)
                    return self._parse_json_response(data)
                except json.JSONDecodeError:
                    continue

        raise MCPError("No valid data found in SSE response")

    def initialize(self) -> Dict:
        """
        Initialize connection with the MCP server.

        Returns:
            Server information and capabilities
        """
        if self._initialized:
            return self._server_info

        result = self._make_request(
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {
                    "name": "agent0-sdk",
                    "version": "1.0.2",
                },
            },
        )

        self._server_info = result
        self._initialized = True

        logger.info(
            "MCP client initialized with server: %s",
            result.get("serverInfo", {}).get("name", "unknown"),
        )

        return result

    def list_tools(self) -> List[Dict]:
        """
        List available tools from the MCP server.

        Returns:
            List of tool definitions with names, descriptions, and schemas
        """
        result = self._make_request("tools/list")
        self._available_tools = result.get("tools", [])
        return self._available_tools

    def call_tool(self, name: str, arguments: Optional[Dict] = None) -> Any:
        """
        Call a tool on the MCP server.

        This is the main method for runtime tool invocation.
        Automatically handles x402 payments if configured.

        Args:
            name: Tool name
            arguments: Tool arguments (optional)

        Returns:
            Tool execution result

        Raises:
            MCPToolCallError: If the tool call fails
            MCPPaymentRequiredError: If payment is required but not configured

        Example:
            result = mcp.call_tool("generate_text", {"prompt": "Hello"})
        """
        result = self._make_request(
            "tools/call",
            {
                "name": name,
                "arguments": arguments or {},
            },
        )

        # Extract content from result
        if isinstance(result, dict) and "content" in result:
            content = result["content"]
            # If single text content, return just the text
            if (
                isinstance(content, list)
                and len(content) == 1
                and isinstance(content[0], dict)
                and content[0].get("type") == "text"
            ):
                return content[0].get("text")
            return content

        return result

    def list_prompts(self) -> List[Dict]:
        """
        List available prompts from the MCP server.

        Returns:
            List of prompt definitions
        """
        result = self._make_request("prompts/list")
        return result.get("prompts", [])

    def get_prompt(self, name: str, arguments: Optional[Dict] = None) -> Dict:
        """
        Get a prompt from the MCP server.

        Args:
            name: Prompt name
            arguments: Prompt arguments (optional)

        Returns:
            Prompt content
        """
        return self._make_request(
            "prompts/get",
            {
                "name": name,
                "arguments": arguments or {},
            },
        )

    def list_resources(self) -> List[Dict]:
        """
        List available resources from the MCP server.

        Returns:
            List of resource definitions
        """
        result = self._make_request("resources/list")
        return result.get("resources", [])

    def read_resource(self, uri: str) -> Dict:
        """
        Read a resource from the MCP server.

        Args:
            uri: Resource URI

        Returns:
            Resource content
        """
        return self._make_request("resources/read", {"uri": uri})

    def get_session_spending(self) -> float:
        """
        Get total amount spent in this session.

        Returns:
            Total spending in USD
        """
        if self._x402_client:
            return self._x402_client.get_session_spending()
        return 0.0

    def get_wallet_address(self) -> Optional[str]:
        """
        Get the wallet address used for payments.

        Returns:
            Wallet address or None if payments not configured
        """
        if self._x402_client:
            return self._x402_client.get_wallet_address()
        return None

    def close(self) -> None:
        """Close the MCP client connection."""
        self._initialized = False
        self._server_info = None
        self._available_tools = None
        logger.debug("MCP client closed")

    def __enter__(self) -> "MCPClient":
        """Context manager entry."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        """Context manager exit."""
        self.close()

    def __repr__(self) -> str:
        return (
            f"MCPClient(endpoint={self.endpoint!r}, "
            f"payments_enabled={self.payments_enabled})"
        )
