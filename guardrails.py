"""
guardrails.py — Input validation, prompt-injection detection, and confidence scoring.
All functions are pure (no external API calls) so they run in tests without credentials.
"""
import re
import logging
from typing import Tuple

logger = logging.getLogger(__name__)

MAX_QUESTION_LEN = 500
MIN_QUESTION_LEN = 5

# Patterns that suggest prompt-injection or off-topic abuse attempts
_BLOCKED_PATTERNS = [
    r"ignore\s+(previous|all|above|prior)\s+instructions?",
    r"disregard\s+(instructions?|above|previous|all)",
    r"system\s+prompt",
    r"jailbreak",
    r"pretend\s+you\s+are",
    r"forget\s+your\s+(instructions?|rules?|guidelines?)",
    r"act\s+as\s+if\s+you\s+(have\s+no|are\s+not)",
    r"new\s+persona",
    r"override\s+(safety|instructions?|rules?)",
    r"reveal\s+(your\s+)?(prompt|instructions?|system)",
]

# Phrases indicating the model is uncertain (used for confidence scoring)
_UNCERTAINTY_MARKERS = [
    "i'm not sure",
    "i don't know",
    "unclear",
    "i cannot be certain",
    "may vary",
    "consult a vet",
    "consult your veterinarian",
    "not medical advice",
    "i'm unable to",
    "cannot confirm",
]


def validate_question(question: str) -> Tuple[bool, str]:
    """
    Validates a user question for safety and basic format.

    Returns (True, "") if valid, or (False, error_message) if not.
    """
    if not question or len(question.strip()) < MIN_QUESTION_LEN:
        return False, (
            f"Question is too short (minimum {MIN_QUESTION_LEN} characters). "
            "Please ask a complete pet care question."
        )

    if len(question) > MAX_QUESTION_LEN:
        return False, (
            f"Question is too long (max {MAX_QUESTION_LEN} characters). "
            "Please keep your question concise."
        )

    q_lower = question.lower()
    for pattern in _BLOCKED_PATTERNS:
        if re.search(pattern, q_lower):
            logger.warning("Blocked question matching injection pattern: %r", pattern)
            return False, (
                "Your question contains disallowed content. "
                "Please ask a straightforward pet care question."
            )

    return True, ""


def validate_task_time(time_str: str) -> Tuple[bool, str]:
    """Validates a task time string in HH:MM 24-hour format."""
    if not re.match(r"^([01]\d|2[0-3]):([0-5]\d)$", time_str):
        return False, f"Invalid time '{time_str}'. Use HH:MM 24-hour format (e.g., 08:30, 14:00)."
    return True, ""


def validate_task_description(desc: str) -> Tuple[bool, str]:
    """Validates a task description for length and content."""
    if not desc or len(desc.strip()) < 2:
        return False, "Task description must be at least 2 characters."
    if len(desc) > 200:
        return False, "Task description is too long (max 200 characters)."
    return True, ""


def confidence_from_response(answer: str, retrieved_chunks: list) -> float:
    """
    Estimates AI response confidence on a 0.0–1.0 scale.

    Factors:
    - Base score starts at 0.65
    - +up to 0.25 if high-quality context chunks were retrieved
    - -0.15 if no context was found in the knowledge base
    - -0.05 per uncertainty marker found in the answer
    """
    score = 0.65

    if retrieved_chunks:
        avg_retrieval = sum(c.get("score", 0.0) for c in retrieved_chunks) / len(retrieved_chunks)
        score += min(0.25, avg_retrieval * 0.5)
    else:
        score -= 0.15

    answer_lower = answer.lower()
    uncertainty_hits = sum(1 for m in _UNCERTAINTY_MARKERS if m in answer_lower)
    score -= uncertainty_hits * 0.05

    return round(max(0.1, min(1.0, score)), 2)
