"""
interfaces/__init__.py
---------------------
Interface adapters for the agent framework.
Each adapter implements a common pattern: receive bus + safety + creator,
register handlers, and run concurrently with other interfaces.
"""

from .telegram import TelegramInterface
from .cli import CLIInterface
from .http import HTTPInterface

__all__ = ["TelegramInterface", "CLIInterface", "HTTPInterface"]