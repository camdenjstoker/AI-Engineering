"""A small internet radio MCP server so George can put on focus music while you code.

Stations come from Radio Browser (https://www.radio-browser.info), a free, open
directory of 50,000+ stations: no account, no API key, no Premium.

Playback uses Windows' built-in media player (WinRT MediaPlayer) inside this
process, so nothing else needs installing. The music stops when George exits,
because mcp_tools.py starts this file as a subprocess over stdio.
"""

import random

import httpx
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from winrt.windows.foundation import Uri
from winrt.windows.media.core import MediaSource
from winrt.windows.media.playback import MediaPlaybackState, MediaPlayer

# Radio Browser asks every client to send a descriptive User-Agent.
HEADERS = {"User-Agent": "george-agent/0.1"}
FALLBACK_SERVER = "https://de1.api.radio-browser.info"

server = FastMCP("radio")

_player: MediaPlayer | None = None
_station: str | None = None  # name of what's playing, for now_playing
_api_base: str | None = None


async def _api(path: str, **params) -> list | dict:
    """GET a Radio Browser endpoint, picking one of its mirrors on first use."""
    global _api_base
    async with httpx.AsyncClient(headers=HEADERS, timeout=10) as http:
        if _api_base is None:
            # Radio Browser spreads load across mirrors; it asks clients to
            # pick one at random from this list instead of hardcoding one.
            try:
                mirrors = (await http.get(
                    "https://all.api.radio-browser.info/json/servers"
                )).json()
                _api_base = f"https://{random.choice(mirrors)['name']}"
            except (httpx.HTTPError, ValueError, IndexError):
                _api_base = FALLBACK_SERVER
        try:
            response = await http.get(f"{_api_base}{path}", params=params)
        except httpx.HTTPError as error:
            raise ToolError(f"Radio Browser is unreachable right now ({error}).")
        if response.status_code == 404:
            raise ToolError("No such station. Use find_stations to get a valid id.")
        if response.is_error:
            raise ToolError(f"Radio Browser returned an error ({response.status_code}).")
        return response.json()


def _get_player() -> MediaPlayer:
    global _player
    if _player is None:
        _player = MediaPlayer()
    return _player


@server.tool(annotations={"readOnlyHint": True})
async def find_stations(tag: str = "", name: str = "", limit: int = 5) -> str:
    """Search for internet radio stations to code to, most popular first.

    Args:
        tag: A genre or mood, e.g. "lofi", "ambient", "classical", "jazz".
        name: Part of a station's name, e.g. "Groove Salad".
        limit: How many stations to return (1-10).
    """
    if not tag and not name:
        raise ToolError("Give a tag (like 'lofi') or part of a station name.")
    stations = await _api(
        "/json/stations/search",
        tag=tag, name=name, limit=max(1, min(limit, 10)),
        hidebroken="true", order="votes", reverse="true",
    )
    if not stations:
        return f"No stations found for tag={tag!r} name={name!r}."
    return "\n".join(
        f"- {s['name'].strip()} | tags: {s['tags'] or 'none'} | "
        f"{s['bitrate'] or '?'} kbps | id: {s['stationuuid']}"
        for s in stations
    )


@server.tool
async def play_station(station_id: str) -> str:
    """Start playing a station. Replaces whatever is already playing.

    Args:
        station_id: The id of a station from find_stations.
    """
    global _station
    # Only stations listed on Radio Browser can be played, never an arbitrary
    # URL. This endpoint also counts the play, as Radio Browser asks clients to.
    info = await _api(f"/json/url/{station_id}")
    if not info.get("ok") or not info.get("url"):
        raise ToolError(f"No station with id {station_id!r}. Use find_stations first.")
    player = _get_player()
    player.source = MediaSource.create_from_uri(Uri(info["url"]))
    player.play()
    _station = info.get("name", "Unknown station").strip()
    return f"Now playing: {_station}"


@server.tool
def stop() -> str:
    """Stop the radio."""
    global _station
    if _player is None or _station is None:
        return "Nothing is playing."
    _player.pause()  # a live stream can't be resumed where it left off
    _player.source = None
    stopped, _station = _station, None
    return f"Stopped {stopped}."


@server.tool
def set_volume(percent: int) -> str:
    """Set the radio volume.

    Args:
        percent: 0 (silent) to 100 (full).
    """
    percent = max(0, min(percent, 100))
    _get_player().volume = percent / 100
    return f"Volume set to {percent}%."


@server.tool(annotations={"readOnlyHint": True})
def now_playing() -> str:
    """Show which station is playing, if any."""
    if _player is None or _station is None:
        return "Nothing is playing."
    state = _player.playback_session.playback_state
    if state in (MediaPlaybackState.OPENING, MediaPlaybackState.BUFFERING):
        return f"Connecting to {_station}..."
    return f"Playing {_station} at {round(_player.volume * 100)}% volume."


if __name__ == "__main__":
    server.run(show_banner=False)  # stdio: stdout is reserved for MCP
