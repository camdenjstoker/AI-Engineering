from fnmatch import fnmatch
from pathlib import Path

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


# The agent may only search/read files inside this project folder.
PROJECT_DIR = Path(__file__).resolve().parent
SKIP_DIRS = {".git", ".venv", "__pycache__", "node_modules"}

# Names the agent may never read or see (API keys live in .env), matched
# against every part of the path so ".env" inside a subfolder is blocked too.
SENSITIVE_PATTERNS = [".env", ".env.*"]

# One generic message for every failure so the agent can't probe which
# hidden files exist.
ACCESS_DENIED = "Error: access denied or file not found."


def _is_allowed(resolved: Path) -> bool:
    """Whether a resolved path is inside the project and not sensitive."""
    if not resolved.is_relative_to(PROJECT_DIR):
        return False
    parts = resolved.relative_to(PROJECT_DIR).parts
    if SKIP_DIRS.intersection(parts):
        return False
    return not any(
        fnmatch(part, pattern) for part in parts for pattern in SENSITIVE_PATTERNS
    )


def _resolve_safe(path: str) -> Path | None:
    """Turn a project-relative path into a real path, or None if not allowed."""
    # resolve() collapses ".." and follows symlinks, so the check sees the
    # real location.
    target = (PROJECT_DIR / path).resolve()
    return target if _is_allowed(target) else None


def find_file(filename: str) -> str:
    """Search the project folder for files matching a name. Use this when the user
    asks you to find a file (for example "Ethics.md") before reading it.

    Args:
        filename: The file name or part of it, e.g. "Ethics.md" or "ethics"
    """
    matches = [
        str(path.relative_to(PROJECT_DIR))
        for path in PROJECT_DIR.rglob("*")
        if filename.lower() in path.name.lower()
        and path.is_file()
        and _is_allowed(path.resolve())
    ]
    if not matches:
        return f"No file matching '{filename}' was found in the project."
    return "Found:\n" + "\n".join(matches)


def read_file(path: str) -> str:
    """Read the text contents of a file in the project folder. Use this after
    find_file to get the file's contents so you can follow or use them.

    Args:
        path: The file path returned by find_file, e.g. "Ethics.md"
    """
    target = _resolve_safe(path)
    if target is None or not target.is_file():
        return ACCESS_DENIED
    try:
        return target.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return f"Error: '{path}' is not a text file."


def list_directory(path: str = ".") -> str:
    """List the contents of a folder in the project. Subfolders end with "/".
    Use this to explore what files exist when you don't know an exact name.

    Args:
        path: Folder path relative to the project root. Defaults to the root.
    """
    target = _resolve_safe(path)
    if target is None or not target.is_dir():
        return ACCESS_DENIED

    entries = sorted(
        entry.name + "/" if entry.is_dir() else entry.name
        for entry in target.iterdir()
        if _is_allowed(entry.resolve())
    )
    return "\n".join(entries) or "(empty folder)"
