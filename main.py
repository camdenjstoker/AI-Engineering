"""Chat with George in the terminal. (popup.py is the pop-up window version.)"""

import asyncio
import json

from rich.console import Console
from rich.markdown import Markdown

from george import open_george, parse_answer, run_turn
from memory import current_thread_id, new_thread_id

console = Console()  # renders George's Markdown (code blocks, lists) nicely


async def ask(prompt: str) -> str:
    # Read input in a thread so the MCP connections aren't blocked.
    return await asyncio.to_thread(input, prompt)


async def review_tool_calls(request: dict) -> dict:
    """Show each tool call George wants to make and let the user approve it."""
    decisions = []
    for action in request["action_requests"]:
        console.print(f"\n[bold magenta]George wants to run {action['name']}:[/]")
        console.print(json.dumps(action["args"], indent=2))
        if (await ask("Allow? (y/n): ")).strip().lower() in {"y", "yes"}:
            decisions.append({"type": "approve"})
        else:
            decisions.append({"type": "reject", "message": "The user said no."})
    return {"decisions": decisions}


async def answer_mcp_questions(request: dict) -> dict:
    """Answer an MCP server that asks the user something mid-tool-call."""
    responses = {}
    for question in request["requests"]:
        console.print(f"\n[bold magenta]{request['tool_name']} asks:[/] {question['message']}")
        if question["mode"] == "url":
            console.print(f"Open: {question['url']}")
            done = (await ask("Done? (y/n): ")).strip().lower() in {"y", "yes"}
            responses[question["key"]] = {"action": "accept" if done else "decline"}
            continue
        fields = question["requested_schema"].get("properties", {})
        content = {
            name: parse_answer(await ask(f"{name}: "), field.get("type"))
            for name, field in fields.items()
        }
        responses[question["key"]] = {"action": "accept", "content": content}
    return {"responses": responses}


async def chat():
    console.print("[dim]Connecting to MCP servers (first use may open a browser to sign in)...[/]")
    # Keep the MCP connections and memory databases open for the whole chat.
    async with open_george() as george:
        for name, error in george.failed.items():
            console.print(f"[red]Skipped {name}: {error}[/]")
        console.print(f"[dim]Connected: {', '.join(george.servers)}[/]")
        console.print("[dim]Resuming your last conversation. Type 'new' to start fresh, 'exit' to quit.[/]")

        config = {"configurable": {"thread_id": current_thread_id()}}
        while True:
            console.print("\n[bold cyan]-------- You --------[/]")
            try:
                prompt = await ask("Input: ")
            except EOFError:
                break
            if prompt.strip().lower() == "exit":
                break
            if prompt.strip().lower() == "new":
                config = {"configurable": {"thread_id": new_thread_id()}}
                console.print("[dim]Started a new conversation. Long-term memories are kept.[/]")
                continue
            if not prompt.strip():
                continue

            result = await run_turn(
                george.agent, prompt, config, review_tool_calls, answer_mcp_questions
            )
            console.print("\n[bold yellow]------- George -------[/]")
            console.print(Markdown(result["messages"][-1].text))


def main():
    asyncio.run(chat())


if __name__ == "__main__":
    main()
