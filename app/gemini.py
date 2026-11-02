# app/gemini.py
"""Reading Gemini replies (DEC-042).

The Vertex AI SDK's `response.text` raises "Multiple content parts are not supported" as soon as
a reply has more than one part. Gemini 3 replies carry a thought signature on their parts and
may split an answer over several parts or add a part that holds only a signature; thought
summaries (parts marked `thought`) are not part of the answer. `reply_text` joins the answer
parts of the first candidate instead, the way the newer google-genai SDK reads a reply.

Errors never include the reply itself: a statement or receipt reply holds financial data, and
the callers log exception messages.

The model names live in config.py (`MODEL_NAME`, `MODEL_LITE`, `MODEL_PRO`).
"""


def reply_text(response) -> str:
    """The answer text of a Gemini reply: its non-thought text parts, joined in order."""
    candidates = getattr(response, "candidates", None)
    if candidates is None:  # not an SDK reply (a test double): its text is the answer
        return response.text
    if not candidates:
        raise ValueError("Gemini returned no answer; the request may have been blocked.")
    candidate = candidates[0]
    texts = []
    for part in candidate.content.parts:
        data = part.to_dict()
        if data.get("thought"):
            continue
        if data.get("text"):
            texts.append(data["text"])
    if not texts:
        reason = getattr(getattr(candidate, "finish_reason", None), "name", "unknown")
        raise ValueError(f"Gemini's reply has no answer text (finish reason: {reason}).")
    return "".join(texts)
