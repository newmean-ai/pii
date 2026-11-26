"""Mask what an agent reads, not only what the user types (LangChain, newmean-pii 1.7).

The user asks about a customer. The agent looks the customer up in a CRM tool and searches support tickets; those
results hold e-mail addresses, phone numbers and other people's names that the user never typed. The middleware masks
them before the next model call, so the hosted model only ever sees placeholders. With a vault, the CRM tool still gets
the real name the user asked about, and the answer comes back with the values.

    pip install "newmean-pii[langchain]" langchain-openai
    OPENAI_API_KEY=... python agent_tool_results.py "Did the refund for Kim Minsu go through?"

Without a vault, the same protection for tool results is ``pii_middleware(redactor, apply_to_tool_results=True)``
(LangChain's PIIMiddleware; the model gets "[REDACTED_EMAIL]" and nothing is put back).
"""
import json
import os
import sys

from langchain.agents import create_agent
from langchain_core.tools import tool

from newmean_pii import Vault, create_redactor
from newmean_pii.langchain import pii_middleware

# Stand-ins for a CRM and a ticket search (fake people)
CUSTOMERS = {
    "Kim Minsu": {"email": "minsu.kim@example.com", "phone": "+82 10-2345-6789", "plan": "Pro", "open_ticket": "T-1042"},
}
TICKETS = [
    "T-1042: Refund requested on 2026-11-20. Agent Park Jiyoung called the customer back at +82 10-2345-6789 "
    "and promised the refund by Friday. Escalated to Lee Sora (lee.sora@example.com).",
]


@tool
def lookup_customer(name: str) -> str:
    """Look up a customer record by full name."""
    name = name.strip().removesuffix("'s").removesuffix("’s")  # a detector may take "Kim Minsu's" as the name
    return json.dumps(CUSTOMERS.get(name, {"error": f"no customer named {name}"}))


@tool
def search_tickets(query: str) -> str:
    """Search support tickets."""
    return "\n".join(t for t in TICKETS if any(w.lower() in t.lower() for w in query.split())) or "no tickets"


def build_agent(model, redactor, vault):
    """The agent: both tools' results are masked before each model call; only lookup_customer gets real values back."""
    return create_agent(
        model,
        tools=[lookup_customer, search_tickets],
        middleware=[pii_middleware(redactor, vault=vault, restore_tools=["lookup_customer"])],
        system_prompt="You help support staff. Use the tools, then answer briefly.",
    )


if __name__ == "__main__":
    question = sys.argv[1] if len(sys.argv) > 1 else "Did the refund for Kim Minsu go through?"
    redactor = create_redactor(size=os.environ.get("PII_SIZE", "medium"))
    vault = Vault()  # one per conversation
    agent = build_agent(os.environ.get("MODEL", "openai:gpt-6.1"), redactor, vault)
    result = agent.invoke({"messages": [{"role": "user", "content": question}]})
    print(result["messages"][-1].content)
