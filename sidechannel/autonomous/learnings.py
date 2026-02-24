"""Learning extraction from completed tasks."""

import re
from typing import List, Optional

import structlog

from .models import (
    Task,
    TaskExecutionResult,
    Learning,
    LearningCategory,
)

logger = structlog.get_logger()


class LearningExtractor:
    """Extracts learnings from task execution results."""

    # Keywords that suggest different learning categories
    CATEGORY_KEYWORDS = {
        LearningCategory.PITFALL: [
            "error",
            "failed",
            "issue",
            "bug",
            "problem",
            "wrong",
            "mistake",
            "incorrect",
            "fix",
            "fixed",
            "broken",
            "crash",
        ],
        LearningCategory.PATTERN: [
            "pattern",
            "approach",
            "solution",
            "implementation",
            "design",
            "structure",
            "architecture",
            "method",
            "technique",
        ],
        LearningCategory.BEST_PRACTICE: [
            "best practice",
            "recommended",
            "should",
            "always",
            "convention",
            "standard",
            "prefer",
            "guideline",
        ],
        LearningCategory.DEBUGGING: [
            "debug",
            "trace",
            "log",
            "investigate",
            "diagnose",
            "inspect",
            "breakpoint",
        ],
        LearningCategory.TESTING: [
            "test",
            "spec",
            "coverage",
            "assert",
            "mock",
            "fixture",
            "unittest",
            "pytest",
        ],
        LearningCategory.ARCHITECTURE: [
            "architecture",
            "module",
            "component",
            "layer",
            "service",
            "dependency",
            "interface",
            "abstraction",
        ],
    }

    # Markers in Claude output that indicate learnings
    LEARNING_MARKERS = [
        (r"(?:Note|Important|Remember|Tip|Insight):\s*(.+?)(?:\n\n|\Z)", LearningCategory.BEST_PRACTICE),
        (r"(?:Pattern|Approach|Solution):\s*(.+?)(?:\n\n|\Z)", LearningCategory.PATTERN),
        (r"(?:Warning|Caution|Pitfall):\s*(.+?)(?:\n\n|\Z)", LearningCategory.PITFALL),
        (r"(?:Learned|Discovery|Found):\s*(.+?)(?:\n\n|\Z)", LearningCategory.PROJECT_CONTEXT),
    ]

    async def extract(
        self, task: Task, result: TaskExecutionResult
    ) -> List[Learning]:
        """Extract learnings from a completed task."""
        learnings = []

        # Extract from error messages (for pitfalls)
        if result.error_message and not result.success:
            learning = self._extract_pitfall_learning(task, result)
            if learning:
                learnings.append(learning)

        # Extract from successful patterns
        if result.success and result.claude_output:
            pattern_learnings = self._extract_pattern_learnings(task, result)
            learnings.extend(pattern_learnings)

        # Extract from quality gate failures
        if result.quality_gate and not result.quality_gate.passed:
            qg_learning = self._extract_quality_gate_learning(task, result)
            if qg_learning:
                learnings.append(qg_learning)

        logger.info(
            "learnings_extracted",
            task_id=task.id,
            count=len(learnings),
            categories=[l.category.value for l in learnings],
        )

        return learnings

    def _extract_pitfall_learning(
        self, task: Task, result: TaskExecutionResult
    ) -> Optional[Learning]:
        """Extract learning from a task failure."""
        if not result.error_message:
            return None

        # Create a learning about what went wrong
        return Learning(
            phone_number=task.phone_number,
            project_name=task.project_name,
            task_id=task.id,
            category=LearningCategory.PITFALL,
            title=f"Issue: {task.title[:50]}",
            content=(
                f"When working on '{task.title}', encountered:\n\n"
                f"{result.error_message[:500]}\n\n"
                f"Task context: {task.description[:300]}"
            ),
            relevance_keywords=self._extract_keywords(
                result.error_message + " " + task.description
            ),
            confidence=0.8,
        )

    def _extract_pattern_learnings(
        self, task: Task, result: TaskExecutionResult
    ) -> List[Learning]:
        """Extract pattern learnings from successful task."""
        learnings = []
        output = result.claude_output

        # Look for explicit learning markers in Claude's output
        for pattern, category in self.LEARNING_MARKERS:
            matches = re.findall(pattern, output, re.IGNORECASE | re.DOTALL)
            for match in matches[:3]:  # Limit per pattern
                match_text = match.strip()
                if len(match_text) > 50:  # Skip too-short matches
                    learnings.append(
                        Learning(
                            phone_number=task.phone_number,
                            project_name=task.project_name,
                            task_id=task.id,
                            category=category,
                            title=self._truncate_title(match_text),
                            content=match_text,
                            relevance_keywords=self._extract_keywords(match_text),
                            confidence=0.7,
                        )
                    )

        # If no explicit learnings found and task was significant, create general one
        if not learnings and len(output) > 500 and result.files_changed:
            learnings.append(
                Learning(
                    phone_number=task.phone_number,
                    project_name=task.project_name,
                    task_id=task.id,
                    category=LearningCategory.PATTERN,
                    title=f"Completed: {task.title[:50]}",
                    content=(
                        f"Successfully completed '{task.title}'.\n\n"
                        f"Files changed: {', '.join(result.files_changed[:5])}\n\n"
                        f"Approach: {task.description[:300]}"
                    ),
                    relevance_keywords=self._extract_keywords(task.description),
                    confidence=0.5,
                )
            )

        return learnings

    def _extract_quality_gate_learning(
        self, task: Task, result: TaskExecutionResult
    ) -> Optional[Learning]:
        """Extract learning from quality gate failure."""
        qg = result.quality_gate
        if not qg or qg.passed:
            return None

        content_parts = [f"Quality gates failed for '{task.title}'"]

        if qg.tests_failed and qg.tests_failed > 0:
            content_parts.append(f"\nTests failed: {qg.tests_failed}/{qg.tests_run}")
            if qg.test_output:
                # Extract most relevant part of test output
                content_parts.append(f"\nTest output:\n{qg.test_output[:500]}")

        if qg.typecheck_passed is False:
            content_parts.append("\nType checking failed")
            if qg.typecheck_output:
                content_parts.append(f"\nTypecheck output:\n{qg.typecheck_output[:300]}")

        if qg.lint_passed is False:
            content_parts.append("\nLinting failed")
            if qg.lint_output:
                content_parts.append(f"\nLint output:\n{qg.lint_output[:200]}")

        return Learning(
            phone_number=task.phone_number,
            project_name=task.project_name,
            task_id=task.id,
            category=LearningCategory.TESTING,
            title=f"QG failure: {task.title[:40]}",
            content="\n".join(content_parts),
            relevance_keywords=self._extract_keywords(task.description),
            confidence=0.9,
        )

    def _extract_keywords(self, text: str, max_keywords: int = 10) -> List[str]:
        """Extract relevant keywords from text."""
        # Common stop words to filter out
        stop_words = {
            "the", "a", "an", "is", "are", "was", "were", "be", "been",
            "being", "have", "has", "had", "do", "does", "did", "will",
            "would", "could", "should", "may", "might", "must", "shall",
            "can", "need", "dare", "ought", "used", "to", "of", "in",
            "for", "on", "with", "at", "by", "from", "as", "into",
            "through", "during", "before", "after", "above", "below",
            "between", "under", "again", "further", "then", "once",
            "and", "but", "if", "or", "because", "until", "while",
            "this", "that", "these", "those", "it", "its", "they",
            "them", "their", "there", "here", "when", "where", "which",
            "who", "whom", "what", "how", "all", "each", "every",
            "both", "few", "more", "most", "other", "some", "such",
            "no", "not", "only", "own", "same", "so", "than", "too",
            "very", "just", "also", "now", "new", "first", "last",
        }

        # Extract words (including underscores for code identifiers)
        words = re.findall(r"\b[a-zA-Z_][a-zA-Z0-9_]*\b", text.lower())

        # Filter and count
        word_counts = {}
        for word in words:
            if word not in stop_words and len(word) > 2:
                word_counts[word] = word_counts.get(word, 0) + 1

        # Sort by frequency and return top keywords
        sorted_words = sorted(word_counts.items(), key=lambda x: -x[1])
        return [word for word, count in sorted_words[:max_keywords]]

    def _truncate_title(self, text: str, max_len: int = 80) -> str:
        """Create a title from text, truncating if needed."""
        # Take first line or first sentence
        first_line = text.split("\n")[0].strip()
        first_sentence = first_line.split(".")[0].strip()

        title = first_sentence if len(first_sentence) < len(first_line) else first_line

        if len(title) > max_len:
            title = title[: max_len - 3] + "..."

        return title

    def categorize_text(self, text: str) -> LearningCategory:
        """Determine the most appropriate category for text."""
        text_lower = text.lower()

        # Count keyword matches per category
        scores = {}
        for category, keywords in self.CATEGORY_KEYWORDS.items():
            score = sum(1 for kw in keywords if kw in text_lower)
            if score > 0:
                scores[category] = score

        if scores:
            return max(scores, key=scores.get)

        return LearningCategory.PROJECT_CONTEXT
