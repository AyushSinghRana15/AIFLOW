"""Model configuration."""
import anthropic

client = anthropic.Anthropic()

ROUTER_MODEL = "claude-haiku-4-5-20251001"
ANSWER_MODEL = "claude-sonnet-4-5"
