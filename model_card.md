# Model Card — PawPal+ Applied AI System

## Base Model

**Gemini 2.5 Flash-Lite** (Google) — used for both the RAG advisor and the agentic schedule optimizer.
Accessed via the `google-generativeai` Python SDK (free tier: 1,500 requests/day). No fine-tuning was
performed; the model is used through the API with custom system instructions, retrieved context (RAG),
and Python function callables for the agentic tool-use workflow.

---

## Intended Use

PawPal+ is designed to help individual pet owners:

- Get evidence-grounded answers to pet care questions (feeding, health, exercise, grooming).
- Receive AI-generated schedule improvements for their specific pets and routines.

The system is intended for personal, non-medical use. All recommendations should be validated
by a licensed veterinarian for health decisions.

---

## AI Collaboration During Development

### How AI Was Used

AI (Gemini and GitHub Copilot) was used in three phases:

1. **Design** — Brainstorming the RAG architecture, tool schemas for the agent, and the guardrail strategy.
2. **Code generation** — Scaffolding TF-IDF retrieval logic, the agentic tool-use loop, and Streamlit UI components.
3. **Debugging** — Diagnosing session-state issues in Streamlit and fixing type annotations in the tool schemas.

### One Helpful AI Suggestion

When implementing the agentic loop, I initially wrote a loop that only processed one tool call per
response. The AI suggested iterating over `response.content` and collecting _all_ tool calls before
sending all function responses in one turn — which is the correct pattern for Gemini's API. This was both
architecturally correct and reduced unnecessary round-trips.

### One Flawed AI Suggestion

The AI suggested using `sentence-transformers` for the RAG retrieval step, generating dense embeddings
locally. While semantically superior, this would have required PyTorch (~1.5 GB) as a dependency —
completely impractical for a student demo environment. The correct decision was to use scikit-learn's
TF-IDF vectorizer (a 20 MB dependency) with a keyword-overlap fallback, which is fast, lightweight,
and still meaningfully improves retrieval over random selection.

---

## Limitations and Biases

### Knowledge Base Scope

The knowledge base covers dogs, cats, birds, and rabbits at a general level. Exotic species (reptiles,
ferrets, hedgehogs) and breed-specific guidance (e.g., brachycephalic dog health) are largely absent.
The retriever may return low-relevance passages for niche queries.

### Not a Substitute for Veterinary Advice

The system cannot diagnose illness, assess medication dosages, or account for individual medical
history. It explicitly recommends consulting a veterinarian for health concerns, but users may
over-rely on AI responses.

### Retrieval Quality

TF-IDF retrieval is lexical (keyword-based), not semantic. It can miss conceptually relevant passages
that use different vocabulary (e.g., "hydration" vs "water intake"). A production system would benefit
from dense retrieval (embedding-based similarity search).

### No Persistent Memory

Session data is lost on browser refresh. There is no user authentication, pet history database, or
multi-session memory. The system cannot learn from past interactions or track long-term health trends.

### Confidence Score is Approximate

The confidence score is a heuristic based on retrieval quality and uncertainty markers in the response.
It is not a calibrated probability and should be treated as a rough guide, not a precise measurement.

---

## Potential Misuse and Mitigation

| Risk                                     | Mitigation                                                                     |
| ---------------------------------------- | ------------------------------------------------------------------------------ |
| Prompt injection                         | Input validation layer (`guardrails.py`) blocks known injection patterns       |
| Medical over-reliance                    | System prompt instructs Gemini to recommend vets for health questions          |
| Harmful content                          | Gemini's built-in safety filters apply in addition to local guardrails         |
| Feeding misinformation to the RAG system | Knowledge base is read-only local files; no user-controlled document ingestion |

---

## Testing Results Summary

**Section 1 — Guardrail Validation (eval_harness.py)**

- All prompt-injection patterns correctly blocked
- All valid questions correctly allowed
- Time and description validators behave as expected

**Section 2 — Scheduler Reliability**

- Chronological sorting, conflict detection, filtering, and recurrence all pass
- One-time vs recurring task handling verified
- Edge cases (empty owner, no-conflict baseline) pass cleanly

**Section 3 — AI Advisor (requires API key)**

- Pet care questions (feeding frequency, dehydration signs, toxic foods) return substantive,
  keyword-relevant answers with confidence scores 0.70–0.85
- Prompt injection inputs are blocked before reaching the API (confidence = 0.0)
- Average confidence for valid questions: ~0.78

### Surprises During Testing

1. **Confidence score variation**: Questions about species not well-represented in the knowledge base
   (e.g., fish, exotic birds) received lower TF-IDF retrieval scores, which correctly lowered
   confidence — the system was self-aware about its own coverage gaps.

2. **Agent tool-call order**: Gemini occasionally called `suggest_task_improvements` before
   `detect_schedule_issues`, reversing the intended order. The final recommendations were still
   accurate — the agent adapted well — but it revealed that step ordering is not guaranteed without
   explicit sequencing instructions in the system prompt.

3. **Guardrail false positives**: The initial regex patterns flagged the phrase "act as if my dog is
   sick" as suspicious (matching "act as if"). The pattern was refined to require additional context
   ("act as if you have no" / "act as if you are not") to reduce false positives on legitimate
   pet-health hypotheticals.

---

## Reflection: What This Project Taught Me

This project reinforced that AI engineering is not just about calling an LLM API — it is about
designing a system where the AI's capabilities are bounded, tested, and explainable:

- **RAG adds grounding**: Without the knowledge base, Gemini's answers are plausible but
  unverifiable. With retrieval, responses cite specific sources and the confidence score
  gives users a signal about answer quality.
- **Agentic transparency matters**: The step-by-step tool call log makes the agent's reasoning
  auditable. Users can see _why_ a recommendation was made, not just what it is.
- **Guardrails are not optional**: Prompt injection is trivially easy to attempt but easy to
  partially mitigate with a lightweight validation layer. Shipping an AI feature without input
  sanitation is an avoidable risk.
- **Evaluation is ongoing**: The eval harness revealed edge cases (fish/exotic species, agent
  step ordering) that weren't apparent from manual testing. Automated evaluation is essential
  for maintaining reliability as the system evolves.
