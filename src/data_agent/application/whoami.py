"""The data agent's only tool in milestone 1: report who the directory says the caller is."""

from typing import Protocol


class Directory(Protocol):
    async def display_name(self) -> str: ...


async def whoami(directory: Directory) -> str:
    return f"data-agent sees {await directory.display_name()}"
