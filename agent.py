"""
agent.py — Agentic Schedule Optimizer for PawPal+.
Multi-step reasoning agent that uses Gemini's function-calling feature to:
  1. analyze_schedule       — Summarise all pets and tasks
  2. detect_schedule_issues — Find conflicts and missing essentials
  3. suggest_task_improvements — Generate per-pet recommendations

Intermediate steps are stored in self.steps so the UI can display
the agent's reasoning chain (agentic transparency requirement).
"""
import json
import logging
import os
from typing import Any

from google import genai
from google.genai import types

from pawpal_system import Owner, Scheduler

logger = logging.getLogger(__name__)


# ── Tool Implementations (plain Python functions) ───────────────────────────────
# Gemini infers tool schemas from docstrings and type hints automatically.

def _make_tools(owner: Owner, scheduler: Scheduler):
    """
    Returns three Python callables that Gemini will use as tools.
    They close over owner/scheduler so the agent reads live schedule data.
    """

    def analyze_schedule() -> dict:
        """
        Analyzes the current pet care schedule and returns a structured summary
        including owner name, total pets, per-pet task counts (pending vs completed),
        task times, and task descriptions.
        """
        pets_summary = []
        for pet in owner.pets:
            pending = pet.get_pending_tasks()
            completed = [t for t in pet.tasks if t.is_complete]
            pets_summary.append(
                {
                    "name": pet.name,
                    "species": pet.species,
                    "age": pet.age,
                    "total_tasks": len(pet.tasks),
                    "pending_count": len(pending),
                    "completed_count": len(completed),
                    "pending_times": [t.time for t in pending],
                    "pending_descriptions": [t.description for t in pending],
                }
            )
        all_tasks = scheduler.sort_by_time()
        return {
            "owner": owner.name,
            "total_pets": len(owner.pets),
            "total_tasks": len(all_tasks),
            "pets": pets_summary,
        }

    def detect_schedule_issues() -> dict:
        """
        Detects scheduling problems: time conflicts (same date and time),
        missing feeding tasks, and missing exercise tasks for dogs.
        Returns a structured list of all issues found.
        """
        conflicts = scheduler.detect_conflicts()
        missing = []
        for pet in owner.pets:
            descs = " ".join(
                t.description.lower() for t in pet.tasks if not t.is_complete
            )
            if not any(w in descs for w in ("feed", "food", "eat", "meal", "water")):
                missing.append(f"{pet.name}: no feeding task found")
            if pet.species.lower() == "dog" and not any(
                w in descs for w in ("walk", "exercise", "run", "play")
            ):
                missing.append(f"{pet.name}: no walk or exercise task found")
        return {
            "conflicts": conflicts,
            "conflict_count": len(conflicts),
            "missing_essentials": missing,
            "total_issues": len(conflicts) + len(missing),
        }

    def suggest_task_improvements(pet_name: str, species: str) -> dict:
        """
        Generates actionable improvement suggestions for a specific pet based on
        their species, age, and current task gaps.

        Args:
            pet_name: The name of the pet to generate suggestions for.
            species: The pet species (dog, cat, bird, rabbit, fish, or other).
        """
        pet = next(
            (p for p in owner.pets if p.name.lower() == pet_name.lower()), None
        )
        if not pet:
            return {"error": f"Pet '{pet_name}' not found"}

        descs = " ".join(
            t.description.lower() for t in pet.tasks if not t.is_complete
        )
        suggestions = []

        if species.lower() == "dog":
            if "walk" not in descs and "exercise" not in descs:
                suggestions.append(
                    "Add a morning walk at 07:00 (30-60 min depending on breed)"
                )
            if "feed" not in descs and "food" not in descs:
                suggestions.append(
                    "Add twice-daily feeding: morning 07:30 and evening 17:30"
                )
            if "brush" not in descs and "groom" not in descs:
                suggestions.append("Add a weekly grooming/brushing session for coat health")
            if "teeth" not in descs and "dental" not in descs:
                suggestions.append("Add teeth brushing 3x/week to prevent dental disease")

        elif species.lower() == "cat":
            if "feed" not in descs and "food" not in descs:
                suggestions.append(
                    "Add twice-daily feeding: morning 07:00 and evening 18:00"
                )
            if "litter" not in descs and "clean" not in descs:
                suggestions.append("Add daily litter box scooping at 08:00")
            if "play" not in descs and "enrich" not in descs:
                suggestions.append("Add 15-minute evening playtime for mental enrichment")
            if "brush" not in descs and "groom" not in descs:
                suggestions.append("Add weekly brushing to reduce hairballs")

        elif species.lower() in ("bird", "parrot"):
            suggestions.append("Add daily fresh water and seed refresh at 07:30")
            suggestions.append("Add 1-hour daily out-of-cage socialization time")
            suggestions.append("Add weekly cage cleaning (thorough wipe-down)")

        elif species.lower() == "rabbit":
            if "feed" not in descs:
                suggestions.append(
                    "Add twice-daily fresh hay/pellet feeding: morning and evening"
                )
            suggestions.append("Add daily litter box cleaning")
            suggestions.append(
                "Add daily free-roam exercise time (minimum 3 hours outside cage)"
            )

        else:
            suggestions.append("Schedule regular feeding times appropriate for the species")
            suggestions.append("Schedule weekly health check-ins to monitor weight and behavior")

        times = [t.time for t in pet.tasks if not t.is_complete]
        if len(times) != len(set(times)):
            suggestions.append(
                "Resolve time conflicts: multiple tasks scheduled at the same time"
            )

        return {
            "pet": pet.name,
            "species": pet.species,
            "age": pet.age,
            "current_task_count": len(pet.tasks),
            "suggestion_count": len(suggestions),
            "suggestions": suggestions,
        }

    return [analyze_schedule, detect_schedule_issues, suggest_task_improvements]


class ScheduleAgent:
    """
    Agentic workflow that analyses and optimises a pet care schedule.

    Uses Gemini's function-calling with automatic calling disabled so every
    tool invocation is logged as an observable reasoning step in self.steps.
    """

    _SYSTEM_PROMPT = (
        "You are PawPal+'s Schedule Optimization Agent. "
        "Analyse the pet care schedule methodically: "
        "first call analyze_schedule to understand the current state, "
        "then call detect_schedule_issues to find problems, "
        "then call suggest_task_improvements for each pet. "
        "After using all relevant tools, write a clear final recommendation "
        "summarising the issues and the top 3-5 actionable improvements "
        "the owner should make."
    )

    def __init__(self, owner: Owner, max_iterations: int = 10) -> None:
        self.owner = owner
        self.scheduler = Scheduler(owner)
        self.max_iterations = max_iterations
        self.steps: list[dict] = []

        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            raise ValueError(
                "GEMINI_API_KEY environment variable is not set. "
                "Get a free key at https://aistudio.google.com/app/apikey"
            )
        self.client = genai.Client(api_key=api_key)
        self._tools = _make_tools(owner, self.scheduler)
        self._tool_map: dict[str, Any] = {fn.__name__: fn for fn in self._tools}

    def run(self) -> dict:
        """
        Executes the multi-step schedule optimisation agent.

        Returns:
            steps                – List of {step, tool, input, result} dicts.
            final_recommendation – Agent's textual summary and advice.
            issues_found         – Raw conflict warnings from the Scheduler.
        """
        self.steps = []

        pet_summary = ", ".join(
            f"{p.name} ({p.species}, {p.age} yrs)" for p in self.owner.pets
        )
        initial_prompt = (
            f"Please analyse and optimise the pet care schedule for {self.owner.name}. "
            f"They have {len(self.owner.pets)} pet(s): {pet_summary}. "
            "Use your tools to inspect the schedule, detect issues, and suggest "
            "improvements. After using the tools, provide a clear written recommendation."
        )

        logger.info(
            "Starting schedule agent for '%s' (%d pets)",
            self.owner.name,
            len(self.owner.pets),
        )

        chat = self.client.chats.create(
            model="gemini-2.5-flash",
            config=types.GenerateContentConfig(
                tools=self._tools,
                system_instruction=self._SYSTEM_PROMPT,
                automatic_function_calling=types.AutomaticFunctionCallingConfig(
                    disable=True
                ),
            ),
        )

        response = chat.send_message(initial_prompt)

        for iteration in range(self.max_iterations):
            candidate = response.candidates[0] if response.candidates else None
            content = candidate.content if candidate is not None else None
            parts = (content.parts or []) if content is not None else []

            fn_calls = [
                part.function_call
                for part in parts
                if part.function_call and part.function_call.name
            ]

            if not fn_calls:
                logger.info("Agent finished after %d iteration(s).", iteration + 1)
                break

            fn_responses = []
            for fc in fn_calls:
                tool_name = fc.name or ""
                args = dict(fc.args) if fc.args is not None else {}
                result = self._dispatch(tool_name, args)

                self.steps.append(
                    {
                        "step": len(self.steps) + 1,
                        "tool": tool_name,
                        "input": args,
                        "result": result,
                    }
                )
                logger.info("Tool '%s' called (step %d)", tool_name, len(self.steps))

                fn_responses.append(
                    types.Part(
                        function_response=types.FunctionResponse(
                            name=tool_name,
                            response={"output": json.dumps(result, default=str)},
                        )
                    )
                )

            response = chat.send_message(fn_responses)
        else:
            logger.warning("Agent reached max_iterations=%d", self.max_iterations)

        final_candidate = response.candidates[0] if response.candidates else None
        final_content = final_candidate.content if final_candidate is not None else None
        final_parts = (final_content.parts or []) if final_content is not None else []
        final_text = "".join(
            part.text for part in final_parts
            if hasattr(part, "text") and part.text
        )

        issues = self.scheduler.detect_conflicts()
        logger.info(
            "Agent complete | steps=%d | conflicts=%d", len(self.steps), len(issues)
        )

        return {
            "steps": self.steps,
            "final_recommendation": final_text,
            "issues_found": issues,
        }

    def _dispatch(self, tool_name: str, args: dict) -> Any:
        fn = self._tool_map.get(tool_name)
        if fn is None:
            return {"error": f"Unknown tool: {tool_name}"}
        try:
            return fn(**args)
        except Exception as exc:
            logger.error("Tool '%s' raised: %s", tool_name, exc)
            return {"error": str(exc)}
