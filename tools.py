from dotenv import load_dotenv
from langchain.chat_models import init_chat_model
from langchain.tools import tool

load_dotenv()  # loads GEMINI_API_KEY from .env

SQL_EXPERT_PROMPT = """You are a senior SQL expert. You will be given a request.
- If the request contains a SQL query: check that it is valid, point out any errors,
  give a corrected version if needed, and explain what the query returns.
- If the request is not a SQL query: write the best SQL query to accomplish it,
  and briefly explain how it works and any assumptions (table/column names).
Be accurate and concise. Put SQL in ```sql code blocks."""

sql_model = init_chat_model(
    "google_genai:gemini-flash-lite-latest",
    temperature=0,  # low temperature for precise, consistent SQL
)


def get_response(prompt: str) -> str:
    """Ask a SQL expert model for help. Use this to check whether a SQL query is
    correct and what it returns, or to write a SQL query for a plain-English request.

    Args:
        prompt: The SQL query to check, or a description of the data the user wants
    """
    response = sql_model.invoke([
        {"role": "system", "content": SQL_EXPERT_PROMPT},
        {"role": "user", "content": prompt},
    ])
    return response.text
