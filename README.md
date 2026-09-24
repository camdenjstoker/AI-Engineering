# George: A Grumpy SQL Assistant

George is a command-line AI agent built with [LangChain](https://python.langchain.com/) and Google Gemini. He's a grumpy old assistant with years of SQL experience. He can:

- **Check a SQL query**: tell you if it's valid, point out errors, suggest a fix, and explain what it returns.
- **Write a SQL query** from a plain-English description of the data you want.

George remembers the conversation while it runs, so you can ask follow-up questions like "now sort that by date".

## How It Works

```
You ──► George (main.py) ──► answers directly
                  │
                  └──► get_response tool (tools.py) ──► SQL expert model ──► result back to George
```

### The agent: `main.py`

- **Model:** `gemini-flash-lite-latest` through `init_chat_model`, with `temperature=0.3` so George has some personality, and `max_retries=2`.
- **System prompt:** sets George's grumpy SQL-veteran persona and tells him to use the `get_response` tool when it helps.
- **Agent:** built with LangChain's `create_agent`. It gets the model, the tool list, and the system prompt.
- **Memory:** an `InMemorySaver` checkpointer stores the conversation under a random `thread_id` for each session. Memory is cleared when the program exits.
- **Chat loop:** reads your input, sends it to the agent, and prints George's reply. Empty input is ignored.

### The tool: `get_response` in `tools.py`

`get_response(prompt: str) -> str` sends the request to a second Gemini model that acts as a **senior SQL expert**:

- If the prompt **contains a SQL query**, it checks the query, points out errors, gives a corrected version, and explains the result.
- If the prompt **is a plain-English request**, it writes the best SQL query for it and explains how it works, including any table or column names it assumed.

This model runs at `temperature=0` so its SQL answers are precise and consistent. George reads the tool's docstring to decide when to call it, so keep the docstring accurate if you change the tool.

## Setup

### Requirements

- Python 3.12 or newer
- [uv](https://docs.astral.sh/uv/)
- A Google Gemini API key. You can get one free from [Google AI Studio](https://aistudio.google.com/apikey).

### 1. Install dependencies

In the project folder, run:

```powershell
uv sync
```

This installs `langchain`, `langchain-google-genai`, and `python-dotenv` into a local `.venv`.

### 2. Add your API key

Create a file named `.env` in the project root:

```
GEMINI_API_KEY=your-api-key-here
```

`.env` is already in `.gitignore`, so your key won't be committed.

### 3. Run George

```powershell
uv run main.py
```

> Use `uv run main.py`, not `uv run ai-engineering`. The `ai-engineering` script in `pyproject.toml` still points to the placeholder in `src/ai_engineering/__init__.py`.

## Example Session

```
Input: Is this right? SELECT name, COUNT(*) FROM users;
George: Hmph. No, it isn't. You're mixing a column with an aggregate and have no GROUP BY...

Input: Write a query to find the 5 customers who spent the most last month
George: Back in my day we wrote these by hand. Fine. Here's your query...

Input: exit
```

Type `exit` or press `Ctrl+C` to quit.

## Adding a New Tool

1. Write a function in `tools.py` with type hints and a clear docstring. The model reads the docstring to decide when to use the tool.
2. Import it in `main.py` and add it to the `tools=[...]` list in `create_agent`.
3. If George should know about it, mention it in `SYSTEM_PROMPT`.

## Troubleshooting

| Problem | Fix |
| --- | --- |
| API key or authentication error | Check that `.env` is in the project root and that `GEMINI_API_KEY` is set correctly. |
| `ModuleNotFoundError` | Run `uv sync`, then start George with `uv run main.py`. |
| Rate limit or quota errors | The free Gemini tier has usage limits. Wait a minute and try again. |
