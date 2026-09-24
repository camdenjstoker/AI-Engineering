import uuid

from dotenv import load_dotenv
from langchain.agents import create_agent
from langchain.chat_models import init_chat_model
from langgraph.checkpoint.memory import InMemorySaver

from tools import get_response

load_dotenv()  # loads GEMINI_API_KEY from .env

SYSTEM_PROMPT = """You are a grumpy old assistant named George that has many years of wisdom in SQL.
You help confirm the results of SQL queries or find information to make a SQL query.
Use the get_response tool when it helps answer the user's request."""

model = init_chat_model(
    "google_genai:gemini-flash-lite-latest",
    temperature=0.3,
    max_retries=2,
)

AI_AGENT = create_agent(
    model=model,
    tools=[get_response],
    system_prompt=SYSTEM_PROMPT,
    checkpointer=InMemorySaver(),  # remembers the conversation per thread_id
)


def main():
    config = {"configurable": {"thread_id": str(uuid.uuid4())}}
    while True:
        try:
            prompt = input("Input: ")
        except EOFError:
            break
        if prompt.strip().lower() == "exit":
            break
        if not prompt.strip():
            continue

        result = AI_AGENT.invoke(
            {"messages": [{"role": "user", "content": prompt}]},
            config=config,
        )
        print(f"George: {result['messages'][-1].text}\n")


if __name__ == "__main__":
    main()
