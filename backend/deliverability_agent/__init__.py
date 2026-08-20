"""Deliverability agent packaged for the observatory backend.

Exposes run_agent() for the FastAPI endpoint. The agent (LangGraph + Ollama +
the deliverability tools) is identical to the standalone CLI version; only the
imports were made package-relative. LLM + data-source config lives in this
package's .env.
"""

from langgraph.errors import GraphRecursionError

from .agent import agent

_EMPTY_UI = {"stats": [], "findings": [], "charts": [], "tables": []}


def _answer_text(content):
    """
    Flatten a message's content to plain text for the UI.

    Ollama returns a plain string, but Anthropic returns a list of content blocks and
    (with extended thinking) includes 'thinking' blocks alongside the 'text' ones.
    Returning that list verbatim made the frontend render nothing, which looked like the
    agent never finished. Only visible text is kept; thinking blocks are dropped.
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and block.get("type") == "text":
                parts.append(block.get("text") or "")
        return "\n".join(p for p in parts if p).strip()
    return "" if content is None else str(content)


def run_agent(question, conversation_id="default"):
    """Run one turn of the agent; return answer, raw tool output, and UI payload."""
    config = {
        "configurable": {"thread_id": conversation_id},
        # Fail fast if the model gets stuck re-calling a tool instead of looping
        # forever; a normal turn needs only a few steps.
        "recursion_limit": 15,
    }
    try:
        response = agent.invoke(
            {"messages": [{"role": "user", "content": question}]},
            config=config,
        )
    except GraphRecursionError:
        return {
            "answer": "I couldn't settle on an answer — the tool kept being retried. "
                      "Please rephrase the question or try again.",
            "tool_output": "",
            "ui": dict(_EMPTY_UI),
        }

    messages = response["messages"]

    # Only include tool output produced after the latest user message (this turn).
    last_user = max(
        (i for i, m in enumerate(messages) if getattr(m, "type", None) == "human"),
        default=-1,
    )

    ui = {"stats": [], "findings": [], "charts": [], "tables": []}
    tool_blocks = []
    for m in messages[last_user + 1:]:
        if getattr(m, "type", None) != "tool":
            continue
        tool_blocks.append(f"{m.name}:\n{m.content}")
        # Structured UI rides on the ToolMessage.artifact (content_and_artifact).
        payload = getattr(m, "artifact", None)
        if isinstance(payload, dict):
            for key in ("stats", "findings", "charts", "tables"):
                ui[key].extend(payload.get(key, []))

    answer = _answer_text(messages[-1].content)
    if not answer:
        # A turn that ends with only thinking/tool blocks would otherwise render blank.
        answer = ("I ran the checks but didn't produce a written answer. "
                  "Please ask again, or rephrase the question.")

    return {
        "answer": answer,
        "tool_output": "\n\n".join(tool_blocks),
        "ui": ui,
    }
