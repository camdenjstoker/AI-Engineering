# George: A Grumpy Coding Assistant

George is an AI coding assistant built with [LangChain](https://docs.langchain.com/) and Google Gemini. He's a grumpy old programmer with decades of experience, and under the grumbling he's a patient teacher. He helps with three things:

- **Debugging**: finds out why code fails or gives the wrong result, explains the cause, and shows the smallest fix.
- **Small code snippets**: short, focused pieces of code (a function, a query, a chart), not whole programs.
- **Teaching**: explains how and why code works, so you can do it yourself next time.

### Languages and tools

| Area | Languages |
| --- | --- |
| AI frameworks | LangChain |
| Databases | SQL, PostgreSQL, Oracle (SQL and PL/SQL) |
| Python | Python, Polars, Pandas, Lets-Plot, PySpark |
| R | R, Tidyverse |
| Compiled | C#, C++ |
| Web | HTML |
| Documents | Quarto (`.qmd`), R Markdown (`.rmd`), Markdown (`.md`) |

Before answering a coding question, George looks up the official documentation through his docs servers instead of relying on memory. If you ask about something outside coding or this list, he'll tell you so (grumpily).

## Ways to talk to George

| How | What happens |
| --- | --- |
| `george` in any terminal | Chat in that terminal (PowerShell, CMD, or Git Bash) |
| `george popup` | Opens George's pop-up chat window |
| **Ctrl+Alt+Space** anywhere | Shows or hides the pop-up window (George waits in the system tray) |
| **Ctrl+Alt+G** anywhere | Opens George in a drop-down Windows Terminal; **Win+`** hides and shows it |
| `uv run main.py` in the project folder | Same as `george`, without the launcher |

Inside a chat, type `new` to start a fresh conversation or `exit` to quit. In the pop-up, closing the window hides it to the tray; right-click the gold **G** tray icon to quit.

## What George can do

### Tools

| Tool | What it does | Asks you first? |
| --- | --- | --- |
| `get_response` | Sends SQL questions to a second Gemini model that acts as a senior SQL expert (`temperature=0` for precise answers) | No |
| `find_file`, `read_file`, `list_directory` | Read files in this project folder. They can't see `.env`, `.git`, or `.venv`, or anything outside the project | No |
| `remember`, `recall`, `forget` | George's long-term memory (see [Memory](#memory)) | Saving and deleting do |

### MCP servers

George connects to [MCP](https://modelcontextprotocol.io) servers through LangChain's `MCPAdapter`. Each server's tools are prefixed with its name (for example `weather_get_current_weather`).

| Server | Purpose | Needs |
| --- | --- | --- |
| `langchain` | LangChain and LangGraph docs | Nothing |
| `microsoft` | C#, .NET, C++, SQL Server, and Azure docs | Nothing |
| `context7` | Docs for everything else (PostgreSQL, Oracle, Polars, Lets-Plot, R, Quarto...) | Optional `CONTEXT7_API_KEY` for higher rate limits |
| `windows` | Run scripts and queries to confirm their real output, use the clipboard, open apps | `ENABLE_WINDOWS_MCP=true` |
| `weather` | Weather, air quality, and time zones (free Open-Meteo) | `ENABLE_WEATHER_MCP=true` |
| `radio` | Focus music from 50,000+ free internet radio stations | `ENABLE_RADIO_MCP=true` |
| `drive` | Find and read your queries and scripts in Google Drive | Google OAuth app (see `.env.example`) |
| `slack` | Read teammates' code questions in Slack | Slack app (see `.env.example`) |

If a server fails to connect (an expired login, say), George leaves it out and tells you, and everything else keeps working.

**Approvals:** Windows commands, file actions, screenshots, and any Slack or Drive tool that changes something ask for your OK before they run. In the terminal you type `y`/`n`; in the pop-up you get a Yes/No dialog.

## Memory

- **Conversation memory:** every turn is saved, so George picks up where you left off, even after a restart. Type `new` for a fresh conversation.
- **Long-term memory:** facts you ask George to keep (your name, favorite languages, how you like to learn) are remembered across all conversations. He asks before saving or deleting one, and `recall` shows everything he knows about you.

Both are stored in SQLite files in `~/.george/`, outside the project and OneDrive, where George's file tools can't read them.

## How it works

```
 main.py (terminal) ─┐
                     ├─► george.py ──► Gemini model + tools ──┬─► local tools (tools.py, memory.py)
 popup.py (window) ──┘   open_george()                       └─► MCP servers (mcp_tools.py)
```

| File | What's in it |
| --- | --- |
| `george.py` | George himself: system prompt, model, tool list, and `open_george()`, which connects everything and builds the agent with `create_agent` |
| `main.py` | The terminal chat |
| `popup.py` | The pop-up window, tray icon, and Ctrl+Alt+Space hotkey |
| `tools.py` | The SQL expert tool and the project file tools |
| `memory.py` | Conversation and long-term memory (SQLite checkpointer and store) |
| `mcp_tools.py` | Which MCP servers to connect, what each is for, and which tools need approval |
| `radio_server.py` | The internet radio MCP server |
| `Ethics.md` | The rules George follows when you ask him to read it |

### Changing George

- **His personality or focus:** edit `SYSTEM_PROMPT` in `george.py`.
- **A new tool:** write a function with type hints and a clear docstring (George reads the docstring to decide when to use it), then add it to `LOCAL_TOOLS` in `george.py`.
- **A new MCP server:** add a client in `_clients()` and a guide in `SERVER_GUIDES` in `mcp_tools.py`. Every connected server needs a guide.

## Setup

### Requirements

- Windows 10 or 11 (the pop-up, radio, and Windows tools use Windows features)
- Python 3.12 or newer and [uv](https://docs.astral.sh/uv/)
- A Google Gemini API key, free from [Google AI Studio](https://aistudio.google.com/apikey)

### 1. Install dependencies

```powershell
uv sync
```

### 2. Configure `.env`

Copy `.env.example` to `.env` and fill in what you need. Only `GEMINI_API_KEY` is required; each optional service turns on when its settings are present. `.env.example` has step-by-step setup for Google Drive and Slack.

```
GEMINI_API_KEY=your-api-key-here
ENABLE_WEATHER_MCP=true
ENABLE_RADIO_MCP=true
```

`.env` is in `.gitignore`, so your keys won't be committed. Never put real keys in `.env.example`.

### 3. Run George

```powershell
uv run main.py
```

The `george` command, hotkeys, and tray startup are set up outside the project: `~/.local/bin/george.cmd`, `~/bin/george` (Git Bash), a "George" Windows Terminal profile, and shortcuts in the Start Menu and Startup folder.

## Ethics

George is built to follow [Ethics.md](Ethics.md): be clear and show your work, help the user learn instead of doing the learning for them, get the user's approval before changing anything, and be honest about sources and what the agent did. That's why he prefers explanations and hints over finished answers, and why risky tools ask first.

## Troubleshooting

| Problem | Fix |
| --- | --- |
| API key or authentication error | Check that `.env` is in the project root and `GEMINI_API_KEY` is set. |
| A server shows `Connection closed` | Its log in `~/.george/logs/<server>.log` says why. |
| `uv sync` fails with "Access is denied" | OneDrive is locking files in `.venv`. Set the `UV_PROJECT_ENVIRONMENT` user environment variable to a folder outside OneDrive (for example `C:\Users\<you>\.venvs\ai-engineering`), then run `uv sync` again. |
| uv errors about "incompatible hardlinks" | Set the `UV_LINK_MODE` user environment variable to `copy`. |
| Ctrl+Alt+Space does nothing | Start George with `george popup` (or sign out and back in so the Startup shortcut runs). If another app owns that hotkey, George says so in the window. |
| Rate limit or quota errors | The free Gemini tier has usage limits. Wait a minute and try again. |
