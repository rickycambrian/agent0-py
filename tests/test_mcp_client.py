"""
Test suite for MCPClient runtime tool calls with x402 payment support.

Tests the MCPClient for making runtime tool calls to MCP servers,
including automatic payment handling for x402-enabled servers.

Run with: pytest tests/test_mcp_client.py -v
"""

import json
import pytest
from unittest.mock import Mock, patch, MagicMock

from agent0_sdk.core.mcp_client import (
    MCPClient,
    MCPError,
    MCPToolCallError,
    MCPConnectionError,
    MCPPaymentRequiredError,
)
from agent0_sdk.core.x402_client import X402Client, X402Config


class TestMCPClientInitialization:
    """Tests for MCPClient initialization."""

    def test_requires_endpoint(self):
        """MCPClient requires an endpoint URL."""
        with pytest.raises(ValueError) as exc_info:
            MCPClient(endpoint="")
        assert "endpoint URL is required" in str(exc_info.value)

    def test_accepts_valid_endpoint(self):
        """MCPClient accepts valid endpoint URL."""
        client = MCPClient(endpoint="https://mcp.example.com")
        assert client.endpoint == "https://mcp.example.com"

    def test_strips_trailing_slash(self):
        """MCPClient strips trailing slash from endpoint."""
        client = MCPClient(endpoint="https://mcp.example.com/")
        assert client.endpoint == "https://mcp.example.com"

    def test_payments_disabled_by_default(self):
        """Payments disabled when no x402 client provided."""
        client = MCPClient(endpoint="https://mcp.example.com")
        assert not client.payments_enabled
        assert client.get_session_spending() == 0.0
        assert client.get_wallet_address() is None

    def test_payments_enabled_with_x402_client(self):
        """Payments enabled when x402 client provided."""
        x402_config = X402Config(private_key="0x" + "1" * 64, auto_pay=True)
        x402_client = Mock(spec=X402Client)
        x402_client.payments_enabled = True
        x402_client.get_session_spending.return_value = 0.0
        x402_client.get_wallet_address.return_value = "0x1234"

        client = MCPClient(
            endpoint="https://mcp.example.com",
            x402_client=x402_client,
        )
        assert client.payments_enabled


class TestMCPClientToolCalls:
    """Tests for MCPClient tool calling."""

    @pytest.fixture
    def mock_response(self):
        """Create a mock successful response."""
        response = Mock()
        response.status_code = 200
        response.headers = {"content-type": "application/json"}
        return response

    @pytest.fixture
    def client(self):
        """Create MCPClient for testing."""
        return MCPClient(endpoint="https://mcp.example.com", timeout=5)

    @patch("agent0_sdk.core.mcp_client.requests.post")
    def test_call_tool_success(self, mock_post, mock_response, client):
        """Successful tool call returns result."""
        mock_response.json.return_value = {
            "jsonrpc": "2.0",
            "result": {
                "content": [{"type": "text", "text": "Hello world"}]
            },
            "id": 1,
        }
        mock_post.return_value = mock_response

        result = client.call_tool("test_tool", {"param": "value"})
        assert result == "Hello world"

    @patch("agent0_sdk.core.mcp_client.requests.post")
    def test_call_tool_with_complex_result(self, mock_post, mock_response, client):
        """Tool call with multiple content items returns list."""
        mock_response.json.return_value = {
            "jsonrpc": "2.0",
            "result": {
                "content": [
                    {"type": "text", "text": "Part 1"},
                    {"type": "text", "text": "Part 2"},
                ]
            },
            "id": 1,
        }
        mock_post.return_value = mock_response

        result = client.call_tool("multi_output_tool")
        assert len(result) == 2
        assert result[0]["text"] == "Part 1"

    @patch("agent0_sdk.core.mcp_client.requests.post")
    def test_call_tool_error(self, mock_post, mock_response, client):
        """Tool call error raises MCPToolCallError."""
        mock_response.json.return_value = {
            "jsonrpc": "2.0",
            "error": {
                "code": -32600,
                "message": "Invalid tool name",
                "data": {"tool": "unknown_tool"},
            },
            "id": 1,
        }
        mock_post.return_value = mock_response

        with pytest.raises(MCPToolCallError) as exc_info:
            client.call_tool("unknown_tool")

        assert exc_info.value.code == -32600
        assert "Invalid tool name" in str(exc_info.value)

    @patch("agent0_sdk.core.mcp_client.requests.post")
    def test_402_without_x402_raises_error(self, mock_post, client):
        """402 response without x402 client raises MCPPaymentRequiredError."""
        response = Mock()
        response.status_code = 402
        mock_post.return_value = response

        with pytest.raises(MCPPaymentRequiredError) as exc_info:
            client.call_tool("paid_tool")

        assert "x402 is not configured" in str(exc_info.value)


class TestMCPClientWithX402:
    """Tests for MCPClient with x402 payment support."""

    @pytest.fixture
    def x402_client(self):
        """Create mock x402 client."""
        client = Mock(spec=X402Client)
        client.payments_enabled = True
        client.get_session_spending.return_value = 0.25
        client.get_wallet_address.return_value = "0x1234abcd"
        return client

    @pytest.fixture
    def client_with_x402(self, x402_client):
        """Create MCPClient with x402 support."""
        return MCPClient(
            endpoint="https://paid-mcp.example.com",
            x402_client=x402_client,
        )

    def test_uses_x402_client_for_requests(self, client_with_x402, x402_client):
        """MCPClient uses x402 client for making requests."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.headers = {"content-type": "application/json"}
        mock_response.json.return_value = {
            "jsonrpc": "2.0",
            "result": {"tools": []},
            "id": 1,
        }
        x402_client.post.return_value = mock_response

        client_with_x402.list_tools()

        x402_client.post.assert_called_once()
        call_kwargs = x402_client.post.call_args[1]
        assert call_kwargs["handle_402"] is True

    def test_session_spending_from_x402(self, client_with_x402, x402_client):
        """Session spending comes from x402 client."""
        assert client_with_x402.get_session_spending() == 0.25

    def test_wallet_address_from_x402(self, client_with_x402, x402_client):
        """Wallet address comes from x402 client."""
        assert client_with_x402.get_wallet_address() == "0x1234abcd"


class TestMCPClientListOperations:
    """Tests for MCPClient list operations."""

    @pytest.fixture
    def client(self):
        """Create MCPClient for testing."""
        return MCPClient(endpoint="https://mcp.example.com")

    @patch("agent0_sdk.core.mcp_client.requests.post")
    def test_list_tools(self, mock_post, client):
        """list_tools returns tool definitions."""
        response = Mock()
        response.status_code = 200
        response.headers = {"content-type": "application/json"}
        response.json.return_value = {
            "jsonrpc": "2.0",
            "result": {
                "tools": [
                    {"name": "tool1", "description": "First tool"},
                    {"name": "tool2", "description": "Second tool"},
                ]
            },
            "id": 1,
        }
        mock_post.return_value = response

        tools = client.list_tools()
        assert len(tools) == 2
        assert tools[0]["name"] == "tool1"

    @patch("agent0_sdk.core.mcp_client.requests.post")
    def test_list_prompts(self, mock_post, client):
        """list_prompts returns prompt definitions."""
        response = Mock()
        response.status_code = 200
        response.headers = {"content-type": "application/json"}
        response.json.return_value = {
            "jsonrpc": "2.0",
            "result": {
                "prompts": [
                    {"name": "prompt1", "description": "First prompt"},
                ]
            },
            "id": 1,
        }
        mock_post.return_value = response

        prompts = client.list_prompts()
        assert len(prompts) == 1
        assert prompts[0]["name"] == "prompt1"

    @patch("agent0_sdk.core.mcp_client.requests.post")
    def test_list_resources(self, mock_post, client):
        """list_resources returns resource definitions."""
        response = Mock()
        response.status_code = 200
        response.headers = {"content-type": "application/json"}
        response.json.return_value = {
            "jsonrpc": "2.0",
            "result": {
                "resources": [
                    {"uri": "file:///data.json", "name": "Data file"},
                ]
            },
            "id": 1,
        }
        mock_post.return_value = response

        resources = client.list_resources()
        assert len(resources) == 1
        assert resources[0]["uri"] == "file:///data.json"


class TestMCPClientSSEResponses:
    """Tests for MCPClient SSE response handling."""

    @pytest.fixture
    def client(self):
        """Create MCPClient for testing."""
        return MCPClient(endpoint="https://mcp.example.com")

    @patch("agent0_sdk.core.mcp_client.requests.post")
    def test_parses_sse_response(self, mock_post, client):
        """MCPClient parses SSE format responses."""
        response = Mock()
        response.status_code = 200
        response.headers = {"content-type": "text/event-stream"}
        response.text = (
            'event: message\n'
            'data: {"jsonrpc": "2.0", "result": {"tools": [{"name": "sse_tool"}]}, "id": 1}\n\n'
        )
        mock_post.return_value = response

        tools = client.list_tools()
        assert len(tools) == 1
        assert tools[0]["name"] == "sse_tool"


class TestMCPClientConnectionErrors:
    """Tests for MCPClient connection error handling."""

    @pytest.fixture
    def client(self):
        """Create MCPClient for testing."""
        return MCPClient(endpoint="https://mcp.example.com", timeout=1)

    @patch("agent0_sdk.core.mcp_client.requests.post")
    def test_timeout_raises_connection_error(self, mock_post, client):
        """Timeout raises MCPConnectionError."""
        import requests
        mock_post.side_effect = requests.exceptions.Timeout()

        with pytest.raises(MCPConnectionError) as exc_info:
            client.list_tools()

        assert "timed out" in str(exc_info.value)

    @patch("agent0_sdk.core.mcp_client.requests.post")
    def test_connection_error_raises_mcp_error(self, mock_post, client):
        """Connection failure raises MCPConnectionError."""
        import requests
        mock_post.side_effect = requests.exceptions.ConnectionError("Connection refused")

        with pytest.raises(MCPConnectionError) as exc_info:
            client.list_tools()

        assert "Failed to connect" in str(exc_info.value)

    @patch("agent0_sdk.core.mcp_client.requests.post")
    def test_http_error_raises_connection_error(self, mock_post, client):
        """HTTP error status raises MCPConnectionError."""
        response = Mock()
        response.status_code = 500
        response.text = "Internal Server Error"
        mock_post.return_value = response

        with pytest.raises(MCPConnectionError) as exc_info:
            client.list_tools()

        assert "500" in str(exc_info.value)


class TestMCPClientContextManager:
    """Tests for MCPClient context manager support."""

    def test_context_manager(self):
        """MCPClient works as context manager."""
        with MCPClient(endpoint="https://mcp.example.com") as client:
            assert client.endpoint == "https://mcp.example.com"

    def test_context_manager_closes(self):
        """Context manager closes client on exit."""
        client = MCPClient(endpoint="https://mcp.example.com")
        client._initialized = True
        client._server_info = {"test": "data"}

        with client:
            pass

        assert not client._initialized
        assert client._server_info is None


class TestMCPClientRepr:
    """Tests for MCPClient string representation."""

    def test_repr_without_x402(self):
        """Repr shows endpoint and payment status."""
        client = MCPClient(endpoint="https://mcp.example.com")
        repr_str = repr(client)
        assert "https://mcp.example.com" in repr_str
        assert "payments_enabled=False" in repr_str

    def test_repr_with_x402(self):
        """Repr shows payment enabled status."""
        x402_client = Mock(spec=X402Client)
        x402_client.payments_enabled = True
        client = MCPClient(
            endpoint="https://mcp.example.com",
            x402_client=x402_client,
        )
        repr_str = repr(client)
        assert "payments_enabled=True" in repr_str


class TestAgentGetMCPClient:
    """Tests for Agent.getMCPClient() integration."""

    @pytest.fixture
    def mock_sdk(self):
        """Create mock SDK."""
        sdk = Mock()
        sdk.x402_client = None
        return sdk

    @pytest.fixture
    def mock_registration_file(self):
        """Create mock registration file."""
        from agent0_sdk.core.models import RegistrationFile, Endpoint, EndpointType

        reg_file = RegistrationFile(
            name="Test Agent",
            description="Test description",
            endpoints=[
                Endpoint(
                    type=EndpointType.MCP,
                    value="https://mcp.example.com",
                    meta={"version": "2024-11-05"},
                )
            ],
        )
        return reg_file

    def test_get_mcp_client_returns_client(self, mock_sdk, mock_registration_file):
        """getMCPClient returns MCPClient when agent has MCP endpoint."""
        from agent0_sdk.core.agent import Agent

        agent = Agent(sdk=mock_sdk, registration_file=mock_registration_file)
        mcp = agent.getMCPClient()

        assert mcp is not None
        assert isinstance(mcp, MCPClient)
        assert mcp.endpoint == "https://mcp.example.com"

    def test_get_mcp_client_returns_none_without_endpoint(self, mock_sdk):
        """getMCPClient returns None when agent has no MCP endpoint."""
        from agent0_sdk.core.agent import Agent
        from agent0_sdk.core.models import RegistrationFile

        reg_file = RegistrationFile(
            name="Test Agent",
            description="No MCP endpoint",
            endpoints=[],
        )
        agent = Agent(sdk=mock_sdk, registration_file=reg_file)
        mcp = agent.getMCPClient()

        assert mcp is None

    def test_get_mcp_client_inherits_x402(self, mock_registration_file):
        """getMCPClient inherits x402 client from SDK."""
        from agent0_sdk.core.agent import Agent

        mock_sdk = Mock()
        mock_sdk.x402_client = Mock(spec=X402Client)
        mock_sdk.x402_client.payments_enabled = True

        agent = Agent(sdk=mock_sdk, registration_file=mock_registration_file)
        mcp = agent.getMCPClient()

        assert mcp is not None
        assert mcp.payments_enabled
