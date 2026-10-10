import asyncio
import os
import sys
import warnings
from pathlib import Path

import keyring
from cryptography.fernet import Fernet
from dotenv import load_dotenv
from fastmcp import Client
from fastmcp.client.auth import OAuth
from fastmcp.client.group import ClientGroup
from fastmcp.client.transports import StdioTransport, StreamableHttpTransport
from key_value.aio.stores.filetree import (
    FileTreeStore,
    FileTreeV1CollectionSanitizationStrategy,
    FileTreeV1KeySanitizationStrategy,
)
from key_value.aio.wrappers.encryption import FernetEncryptionWrapper
from langchain_core._api import LangChainBetaWarning

# langchain.mcp is in beta and warns once on import; hide it so it doesn't
# clutter the chat.
warnings.filterwarnings("ignore", category=LangChainBetaWarning)

from langchain.mcp import MCPAdapter  # noqa: E402

# Note: this file must NOT be named mcp.py, or it would shadow the real `mcp`
# package that FastMCP (and MCPAdapter) import under the hood.

load_dotenv()  # server settings below come from .env (see .env.example)

PROJECT_DIR = Path(__file__).resolve().parent

# Logins are kept in your user folder, outside the project and OneDrive, so
# George's file tools can't read them.
GEORGE_HOME = Path.home() / ".george"

# uv normally hardlinks packages out of its cache, which this PC refuses
# ("incompatible hardlinks", os error 396); copying works everywhere.
UVX_ENV = {"UV_LINK_MODE": "copy"}

# Each OAuth server needs its own fixed port so its redirect URL
# (http://localhost:<port>/callback) matches the one registered for the app.
DRIVE_CALLBACK_PORT = 8765
SLACK_CALLBACK_PORT = 8766

# Ask for as little access as each purpose needs.
DRIVE_SCOPES = [
    "https://www.googleapis.com/auth/drive.readonly",  # find and read files
    "https://www.googleapis.com/auth/drive.file",  # only files George creates
]
SLACK_SCOPES = [  # user scopes; public channels only
    "search:read.public",
    "search:read.users",
    "channels:read",
    "channels:history",
    "users:read",
    "canvases:read",
    "chat:write",
]

# What each server is for, added to George's system prompt only when that
# server is connected. Tool names are prefixed with the server name.
SERVER_GUIDES = {
    "langchain": "LangChain or LangGraph questions: search the langchain_ docs tools.",
    "microsoft": "C#, .NET, C++ (MSVC), SQL Server, or Azure questions: search the microsoft_ docs tools.",
    "context7": (
        "Everything else (PostgreSQL, Oracle, Python, Polars, pandas, Lets-Plot, PySpark, "
        "R, the Tidyverse, HTML, Quarto, R Markdown, etc.): first call "
        "context7_resolve-library-id to get the library ID, then context7_query-docs."
    ),
    "drive": (
        "Google Drive holds the user's queries, scripts, and datasets. When they mention "
        "one, find it with drive_search_files, read it with drive_read_file_content, then "
        "review it. Save a finished query to Drive only when the user asks."
    ),
    "slack": (
        "Slack is where teammates ask for help with queries. Use the slack_ tools to find "
        "those questions and read the whole thread for context, then review the code. "
        "Only post a reply when the user asks, and show them the reply first."
    ),
    "windows": (
        "To confirm what a query or script really returns, run it with windows_PowerShell "
        "(e.g. `python script.py`, `Rscript file.R`, `dotnet run`, `sqlite3 db \".read q.sql\"`) "
        "instead of guessing. windows_Clipboard can copy a fixed query for the user, "
        "windows_App can open their editor, and windows_Notification can tell them when a "
        "long run finishes. Never read or print .env files, tokens, or passwords."
    ),
    "weather": (
        "For weather, air quality, or time-zone questions, use the weather_ tools with a "
        "city name. Grumble about Rexburg winters when it's cold."
    ),
    "radio": (
        "When the user settles in to code or debug, or asks for music, you may put on "
        "focus music: radio_find_stations with a tag like 'lofi' or 'ambient', then "
        "radio_play_station with a station's id. Grumble about lyrics being distracting "
        "and prefer instrumental stations. radio_stop turns it off."
    ),
}

# The Windows tools George may use. Mouse/keyboard control, the registry, and
# killing processes stay off: a small model driving your mouse is asking for trouble.
WINDOWS_TOOLS = {"App", "PowerShell", "FileSystem", "Clipboard", "Notification", "Screenshot", "Wait"}

# MCP tools George may not use: submit_feedback would post reports to the
# LangChain docs team on the user's behalf.
BLOCKED_MCP_TOOLS = {"langchain_submit_feedback"}

# Windows tools that are safe to run without asking. Everything else there
# (running commands, touching files, sending your screen to the model) asks first.
WINDOWS_NO_APPROVAL = {"Notification", "Wait"}

# Slack and Drive tools run without asking only if their name says they just
# read, and nothing in it says they change anything.
READ_WORDS = {"search", "read", "get", "list", "fetch"}
WRITE_WORDS = {"send", "post", "create", "update", "add", "upload", "schedule",
               "delete", "remove", "copy", "write", "download"}


def is_allowed(tool_name: str) -> bool:
    """Whether George gets this MCP tool at all."""
    server, _, name = tool_name.partition("_")
    if server == "windows":
        return name in WINDOWS_TOOLS
    return tool_name not in BLOCKED_MCP_TOOLS


def needs_approval(tool_name: str) -> bool:
    """Whether the user must OK this MCP tool call before it runs."""
    server, _, name = tool_name.partition("_")
    if server == "windows":
        return name not in WINDOWS_NO_APPROVAL
    if server in {"slack", "drive"}:
        words = set(name.lower().split("_"))
        return not (words & READ_WORDS) or bool(words & WRITE_WORDS)
    return False  # docs lookups


def _token_store() -> FernetEncryptionWrapper:
    """An encrypted, on-disk store for OAuth logins so you only sign in once.

    The encryption key lives in Windows Credential Manager, not next to the files.
    """
    key = keyring.get_password("george-agent", "oauth-token-key")
    if key is None:
        key = Fernet.generate_key().decode()
        keyring.set_password("george-agent", "oauth-token-key", key)
    token_dir = GEORGE_HOME / "oauth"
    token_dir.mkdir(parents=True, exist_ok=True)
    files = FileTreeStore(
        data_directory=token_dir,
        key_sanitization_strategy=FileTreeV1KeySanitizationStrategy(token_dir),
        collection_sanitization_strategy=FileTreeV1CollectionSanitizationStrategy(token_dir),
    )
    return FernetEncryptionWrapper(key_value=files, fernet=Fernet(key.encode()))


def _oauth(prefix: str, scopes: list[str], port: int) -> OAuth:
    """Browser sign-in using the app you registered (<prefix>_CLIENT_ID/SECRET)."""
    return OAuth(
        client_id=os.environ[f"{prefix}_CLIENT_ID"],
        client_secret=os.environ[f"{prefix}_CLIENT_SECRET"],
        scopes=scopes,
        callback_port=port,
        token_storage=_token_store(),
    )


def _has(*names: str) -> bool:
    return all(os.getenv(name) for name in names)


def _log(name: str) -> Path:
    """Where a local server's console output goes, so it doesn't clutter the chat.

    If a local server shows up as "Connection closed", its log says why.
    """
    log_dir = GEORGE_HOME / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    return log_dir / f"{name}.log"


def _clients() -> dict[str, Client]:
    """A client for every docs server, plus each optional server set up in .env."""
    context7_key = os.getenv("CONTEXT7_API_KEY")  # optional; raises rate limits
    clients = {
        "langchain": Client("https://docs.langchain.com/mcp"),
        "microsoft": Client("https://learn.microsoft.com/api/mcp"),
        "context7": Client(StreamableHttpTransport(
            "https://mcp.context7.com/mcp",
            headers={"CONTEXT7_API_KEY": context7_key} if context7_key else None,
        )),
    }
    if _has("GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET"):
        clients["drive"] = Client(
            "https://drivemcp.googleapis.com/mcp/v1",
            auth=_oauth("GOOGLE", DRIVE_SCOPES, DRIVE_CALLBACK_PORT),
        )
    if _has("SLACK_CLIENT_ID", "SLACK_CLIENT_SECRET"):
        clients["slack"] = Client(
            "https://mcp.slack.com/mcp",
            auth=_oauth("SLACK", SLACK_SCOPES, SLACK_CALLBACK_PORT),
        )
    if os.getenv("ENABLE_WINDOWS_MCP", "").lower() == "true":
        # Pinned so an unreviewed new release can't slip in.
        clients["windows"] = Client(StdioTransport(
            "uvx", ["windows-mcp==0.8.5", "serve"],
            env={"ANONYMIZED_TELEMETRY": "false", **UVX_ENV},
            log_file=_log("windows"),
        ))
    if os.getenv("ENABLE_WEATHER_MCP", "").lower() == "true":
        # 0.6.1 was written against mcp 1.x and crashes on import with mcp 2.x
        # (McpError was renamed MCPError), so its own mcp is pinned below 2.
        # That makes it a legacy-era server, so skip the modern-era probe it
        # doesn't understand (see "Protocol eras" in the LangChain MCP docs).
        clients["weather"] = Client(
            StdioTransport(
                "uvx",
                ["--from", "mcp-weather-server==0.6.1", "--with", "mcp<2",
                 "python", "-m", "mcp_weather_server"],
                env=UVX_ENV,
                log_file=_log("weather"),
            ),
            mode="legacy",
        )
    if os.getenv("ENABLE_RADIO_MCP", "").lower() == "true":
        clients["radio"] = Client(StdioTransport(
            sys.executable, [str(PROJECT_DIR / "radio_server.py")],
            log_file=_log("radio"),
        ))
    return clients


async def _try_connect(client: Client) -> Exception | None:
    """Connect once to see if a server works (and sign in on first use)."""
    try:
        async with client.new():
            return None
    except Exception as error:
        return error


async def connect_adapter() -> tuple[MCPAdapter, dict[str, str]]:
    """Build one adapter for every server that connects.

    A ClientGroup fails as a whole if any server fails, so each one is tried on
    its own first and a broken one (say, an expired Slack login) is left out.

    Returns:
        The adapter, and {server name: error} for servers that were left out.
    """
    clients = _clients()
    errors = await asyncio.gather(*(_try_connect(c) for c in clients.values()))
    failed = {
        name: f"{type(error).__name__}: {error}"
        for name, error in zip(clients, errors)
        if error is not None
    }
    working = {name: c for name, c in clients.items() if name not in failed}
    # ClientGroup gives each server its own connection (no proxy in between).
    return MCPAdapter(ClientGroup(working)), failed
