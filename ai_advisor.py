"""
ai_advisor.py — RAG-based pet care advisor for PawPal+.

Architecture:
  User question
    → Guardrails (validate_question)
    → KnowledgeBase.retrieve()   ← TF-IDF over knowledge_base/*.md
    → Gemini (gemini-2.5-flash)  ← answer grounded in retrieved context
    → confidence_from_response() → structured result dict
"""
import logging
import os
from pathlib import Path
from typing import Optional

from google import genai
from google.genai import types

from guardrails import confidence_from_response, validate_question

logger = logging.getLogger(__name__)


class KnowledgeBase:
    """
    Loads pet care documents from a local directory and retrieves
    the most relevant chunks for a given query using TF-IDF.
    Falls back to keyword overlap scoring if scikit-learn is absent.
    """

    def __init__(self, kb_dir: Optional[str] = None) -> None:
        if kb_dir is None:
            kb_dir = str(Path(__file__).parent / "knowledge_base")
        self.chunks: list[dict] = []
        self._load(kb_dir)

    def _load(self, kb_dir: str) -> None:
        kb_path = Path(kb_dir)
        if not kb_path.exists():
            logger.warning("Knowledge base directory '%s' not found.", kb_dir)
            return
        for file in sorted(kb_path.glob("*.md")):
            text = file.read_text(encoding="utf-8")
            paragraphs = [p.strip() for p in text.split("\n\n") if len(p.strip()) > 60]
            title = file.stem.replace("_", " ").title()
            for para in paragraphs:
                self.chunks.append({"text": para, "source": file.name, "title": title})
        logger.info("Loaded %d knowledge chunks from %s", len(self.chunks), kb_dir)

    def retrieve(self, query: str, top_k: int = 3) -> list[dict]:
        """Returns top-k chunks most semantically relevant to the query."""
        if not self.chunks:
            return []
        try:
            from sklearn.feature_extraction.text import TfidfVectorizer
            from sklearn.metrics.pairwise import cosine_similarity
            import numpy as np

            texts = [c["text"] for c in self.chunks]
            vec = TfidfVectorizer(stop_words="english", max_features=8000)
            matrix = vec.fit_transform(texts)
            q_vec = vec.transform([query])
            scores = cosine_similarity(q_vec, matrix)[0]
            top_idx = np.argsort(scores)[::-1][:top_k]
            return [
                {**self.chunks[i], "score": float(scores[i])}
                for i in top_idx
                if scores[i] > 0.01
            ]
        except ImportError:
            return self._keyword_retrieve(query, top_k)

    def _keyword_retrieve(self, query: str, top_k: int) -> list[dict]:
        """Fallback: simple keyword-overlap scoring (no external dependencies)."""
        query_words = set(query.lower().split())
        scored = []
        for chunk in self.chunks:
            overlap = len(query_words & set(chunk["text"].lower().split()))
            if overlap:
                scored.append({**chunk, "score": overlap / max(len(query_words), 1)})
        scored.sort(key=lambda x: x["score"], reverse=True)
        return scored[:top_k]


class PetCareAdvisor:
    """
    RAG-based pet care advisor using Google Gemini (free tier).
    Retrieves relevant knowledge base passages, then calls Gemini to
    produce a grounded, cited answer with a computed confidence score.
    """

    _SYSTEM_PROMPT = (
        "You are PawPal+, an expert pet care assistant. "
        "Help owners with scheduling, health, nutrition, and activity guidance. "
        "Keep answers concise and practical. "
        "When you draw on the provided context passages, mention the source title. "
        "If you are uncertain about something, say so and recommend the owner "
        "consult a veterinarian. Always prioritize pet safety over completeness."
    )

    def __init__(self, kb_dir: Optional[str] = None) -> None:
        self.kb = KnowledgeBase(kb_dir)
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            raise ValueError(
                "GEMINI_API_KEY environment variable is not set. "
                "Get a free key at https://aistudio.google.com/app/apikey "
                "then add it to your .env file: GEMINI_API_KEY=AIza..."
            )
        self.client = genai.Client(api_key=api_key)

    def ask(self, question: str, pet_context: Optional[str] = None) -> dict:
        """
        Answers a pet care question using RAG.

        Args:
            question:    The user's question.
            pet_context: Optional string describing the specific pet.

        Returns a dict:
            answer           – The AI's response text.
            sources          – List of knowledge base source titles used.
            confidence       – Float 0.0–1.0 confidence estimate.
            retrieved_chunks – Raw retrieval results for transparency.
        """
        is_valid, err_msg = validate_question(question)
        if not is_valid:
            logger.info("Question blocked by guardrails: %s", err_msg)
            return {
                "answer": err_msg,
                "sources": [],
                "confidence": 0.0,
                "retrieved_chunks": [],
            }

        chunks = self.kb.retrieve(question, top_k=3)
        context_block = self._format_context(chunks)

        user_msg = f"Question: {question}"
        if pet_context:
            user_msg += f"\n\nPet Context:\n{pet_context}"
        if context_block:
            user_msg += f"\n\nRelevant Knowledge Base Passages:\n{context_block}"
        user_msg += "\n\nPlease provide a helpful, accurate answer."

        logger.info("RAG query: '%s...' | %d chunks retrieved", question[:60], len(chunks))

        response = self.client.models.generate_content(
            model="gemini-2.5-flash",
            contents=user_msg,
            config=types.GenerateContentConfig(
                system_instruction=self._SYSTEM_PROMPT,
            ),
        )
        answer = str(response.text)
        confidence = confidence_from_response(answer, chunks)
        sources = list({c["title"] for c in chunks}) if chunks else []

        logger.info(
            "Answer generated | confidence=%.2f | sources=%s", confidence, sources
        )

        return {
            "answer": answer,
            "sources": sources,
            "confidence": confidence,
            "retrieved_chunks": chunks,
        }

    @staticmethod
    def _format_context(chunks: list[dict]) -> str:
        if not chunks:
            return ""
        parts = [f"[{c['title']}]\n{c['text']}" for c in chunks]
        return "\n\n---\n\n".join(parts)
