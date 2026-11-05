"""Smoke-test the server over real MCP stdio: list tools, search, audit."""

from __future__ import annotations

import asyncio
import json
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def main() -> None:
    params = StdioServerParameters(command=sys.executable, args=["-m", "manapool_mcp.server"])
    async with (
        stdio_client(params) as (read, write),
        ClientSession(read, write) as session,
    ):
        await session.initialize()

        tools = await session.list_tools()
        print("TOOLS:", [tool.name for tool in tools.tools])

        resources = await session.list_resources()
        print("RESOURCES:", [str(r.uri) for r in resources.resources])

        result = await session.call_tool(
            "search_decks",
            {
                "commander": "Rendmaw",
                "bracket": 3,
                "min_price": 60,
                "max_price": 80,
            },
        )
        payload = _payload(result)
        print("SEARCH:", payload.get("total_listings"), "matches")
        for deck in payload.get("decks", [])[:3]:
            print("  ", deck["price"], deck["name"], deck["seller"])

        if payload.get("decks"):
            audit = await session.call_tool("audit_deck", {"deck": payload["decks"][0]["url"]})
            data = _payload(audit)
            print("AUDIT:", data["verdict"])
            print("  min bracket:", data["mechanical_minimum_bracket"])


def _payload(result):  # type: ignore[no-untyped-def]
    if getattr(result, "structured_content", None):
        return result.structured_content
    for item in result.content:
        text = getattr(item, "text", None)
        if text:
            return json.loads(text)
    return {}


if __name__ == "__main__":
    asyncio.run(main())
