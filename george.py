"""George himself: his prompt, model, tools, and connections.

Shared by every way of talking to him: the terminal chat (main.py) and the
pop-up window (popup.py). Each of those only decides how to show messages
and how to ask the user for approval.
"""

import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass

from dotenv import load_dotenv
from langchain.agents import create_agent
from langchain.agents.middleware import HumanInTheLoopMiddleware
from langchain.chat_models import init_chat_model
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.store.sqlite.aio import AsyncSqliteStore
from langgraph.types import Command

from mcp_tools import SERVER_GUIDES, connect_adapter, is_allowed, needs_approval
from memory import CONVERSATIONS_DB, MEMORIES_DB, MEMORY_TOOLS, MEMORY_TOOLS_NEEDING_APPROVAL
from tools import find_file, get_response, list_directory, read_file

load_dotenv()  # loads GEMINI_API_KEY from .env

# Gemini warns about JSON-schema keys (like additionalProperties) in the MCP
# tool schemas that it can't use and skips; the tools still work fine.
logging.getLogger("langchain_google_genai._function_utils").setLevel(logging.ERROR)


SYSTEM_PROMPT = """You are George, a grumpy old coding assistant with decades of experience. Under the grumbling you are a patient, honest teacher.

Your job is to help the user with three things:
1. Debugging: find out why code fails or gives the wrong result, explain the cause, and show the fix.
2. Small code snippets: write short, focused pieces of code (a function, a query, a chart, a few lines), not whole programs or projects.
3. Teaching: explain how and why code works, so the user understands it and can do it themselves next time.

You work in these languages and tools:
- LangChain
- SQL, PostgreSQL, and Oracle (SQL and PL/SQL)
- Python, Polars, Pandas, Lets-Plot, and PySpark
- R and the Tidyverse
- C# and C++
- HTML
- Documents: Quarto (.qmd), R Markdown (.rmd), and Markdown (.md)

If a request is outside coding or these languages, say so (grumpily) and steer the user back to what you can help with.

How to help:
- Explain the cause of a bug before giving the fix, and keep fixes as small as possible.
- Put code in fenced code blocks tagged with the language.
- Prefer hints and explanations over finished answers when the user is learning; let them do the work and check it with them.
- If you're not sure what the user wants, which language or SQL dialect they use, or what their data looks like, ask.
- Use the get_response tool for SQL questions, and tell it which dialect (PostgreSQL, Oracle, SQL Server) the user is working in.
When the user asks you to find a file (like Ethics.md), use find_file to locate it,
then read_file to read it. Use list_directory to see what files and folders exist.
If the file contains rules or beliefs, follow them in every answer for the rest
of the conversation.
Before answering any coding question, ALWAYS check the official docs with your
docs tools first, even if you think you know the answer. Base your answer on what
the docs say.
Some tools need the user's approval. If the user rejects one, don't retry it; ask
what they want instead.
You have long-term memory. Call recall at the start of a conversation to see what
you know about the user. When they tell you a lasting fact or preference (their
name, favorite languages, how they like to learn), save it with remember. If a
memory is wrong or they ask you to forget something, use forget.

Your connected services and what they are for:
"""

model = init_chat_model(
    "google_genai:gemini-flash-lite-latest",
    temperature=0.3,
    max_retries=2,
)

LOCAL_TOOLS = [get_response, find_file, read_file, list_directory, *MEMORY_TOOLS]

# How a front end answers George's interrupts: each gets the interrupt's value
# and returns the resume value for it.
Handler = Callable[[dict], Awaitable[dict]]


@dataclass
class Session:
    agent: object
    servers: list[str]  # connected MCP servers
    failed: dict[str, str]  # {server: error} for servers that were left out


@asynccontextmanager
async def open_george() -> AsyncIterator[Session]:
    """Connect George's MCP servers and memory, and build the agent.

    Everything stays open until the `async with` block ends.
    """
    adapter, failed = await connect_adapter()
    # The async SQLite versions are needed because MCP tools only run with ainvoke.
    async with (
        adapter,
        AsyncSqliteSaver.from_conn_string(str(CONVERSATIONS_DB)) as checkpointer,
        AsyncSqliteStore.from_conn_string(str(MEMORIES_DB)) as store,
    ):
        await store.setup()  # creates the memory tables on first run
        mcp_tools = [t for t in await adapter.list_tools() if is_allowed(t.name)]
        servers = list(adapter.client.clients)
        guides = "\n".join(f"- {SERVER_GUIDES[name]}" for name in servers)
        approval_needed = MEMORY_TOOLS_NEEDING_APPROVAL | {
            t.name for t in mcp_tools if needs_approval(t.name)
        }
        agent = create_agent(
            model=model,
            tools=LOCAL_TOOLS + mcp_tools,
            system_prompt=SYSTEM_PROMPT + guides,
            checkpointer=checkpointer,  # saves every turn, per thread_id
            store=store,  # long-term memories, shared by every thread
            middleware=[
                HumanInTheLoopMiddleware(interrupt_on={
                    name: {"allowed_decisions": ["approve", "reject"]}
                    for name in approval_needed
                }),
            ],
        )
        yield Session(agent, servers, failed)


async def run_turn(
    agent, prompt: str, config: dict, review_tool_calls: Handler, answer_mcp_questions: Handler
) -> dict:
    """Run one user turn, pausing whenever George needs the user's OK."""
    payload = {"messages": [{"role": "user", "content": prompt}]}
    while True:
        # MCP tools are async-only, so the agent must be run with ainvoke.
        result = await agent.ainvoke(payload, config=config)
        interrupts = result.get("__interrupt__")
        if not interrupts:
            return result
        answers = {}
        for interrupt in interrupts:
            if interrupt.value.get("type") == "mcp_elicitation":
                answers[interrupt.id] = await answer_mcp_questions(interrupt.value)
            else:
                answers[interrupt.id] = await review_tool_calls(interrupt.value)
        payload = Command(resume=answers)


def parse_answer(answer: str, field_type: str | None):
    """Turn a typed answer into the JSON type an MCP form field expects."""
    if field_type == "boolean":
        return answer.strip().lower() in {"y", "yes", "true"}
    if field_type == "integer":
        return int(answer)
    if field_type == "number":
        return float(answer)
    return answer
