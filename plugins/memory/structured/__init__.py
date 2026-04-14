"""Structured memory plugin — register with the Hermes plugin context."""

from .provider import StructuredMemoryProvider


def register(ctx):
    ctx.register_memory_provider(StructuredMemoryProvider())
