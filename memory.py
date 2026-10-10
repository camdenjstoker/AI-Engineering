"""George's memory: the conversation survives restarts, and facts survive across chats.

- Short-term memory (a checkpointer) saves every turn of the current conversation.
- Long-term memory (a store) holds facts George was asked to keep, in any chat.

Both live in SQLite files in your user folder (~/.george), outside the project
and OneDrive, so George's file tools can't read them and they don't get synced.
See the LangChain docs: short-term-memory and long-term-memory.
"""

import uuid

from langchain.tools import ToolRuntime

from mcp_tools import GEORGE_HOME

CONVERSATIONS_DB = GEORGE_HOME / "conversations.sqlite"
MEMORIES_DB = GEORGE_HOME / "memories.sqlite"
THREAD_FILE = GEORGE_HOME / "thread_id"

# Every long-term memory is filed under this namespace (like a folder).
NAMESPACE = ("george", "user")

# Saving or deleting a memory changes what George knows about you, so it asks first.
MEMORY_TOOLS_NEEDING_APPROVAL = {"remember", "forget"}


def current_thread_id() -> str:
    """The conversation to resume, so George picks up where you left off."""
    GEORGE_HOME.mkdir(parents=True, exist_ok=True)
    if THREAD_FILE.exists():
        return THREAD_FILE.read_text().strip()
    return new_thread_id()


def new_thread_id() -> str:
    """Start a fresh conversation (long-term memories are kept)."""
    thread_id = str(uuid.uuid4())
    THREAD_FILE.write_text(thread_id)
    return thread_id


async def remember(fact: str, runtime: ToolRuntime) -> str:
    """Save a lasting fact about the user so you know it in future conversations,
    e.g. their name, preferred languages, or how they like explanations.
    Only save what the user would want kept; never save passwords, keys, or
    other people's private information.

    Args:
        fact: One short fact in plain words, e.g. "Prefers Polars over pandas"
    """
    memory_id = uuid.uuid4().hex[:8]
    await runtime.store.aput(NAMESPACE, memory_id, {"fact": fact})
    return f"Remembered (id {memory_id}): {fact}"


async def recall(runtime: ToolRuntime) -> str:
    """List everything you remember about the user from past conversations.
    Check this at the start of a conversation and before coding questions."""
    items = await runtime.store.asearch(NAMESPACE, limit=100)
    if not items:
        return "No memories saved yet."
    return "\n".join(f"- {item.value['fact']} (id {item.key})" for item in items)


async def forget(memory_id: str, runtime: ToolRuntime) -> str:
    """Delete a saved memory when it is wrong or the user asks you to forget it.

    Args:
        memory_id: The id shown next to the memory by recall
    """
    if await runtime.store.aget(NAMESPACE, memory_id) is None:
        return f"No memory with id {memory_id}. Use recall to see the ids."
    await runtime.store.adelete(NAMESPACE, memory_id)
    return f"Forgot memory {memory_id}."


MEMORY_TOOLS = [remember, recall, forget]
