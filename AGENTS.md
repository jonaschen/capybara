# AGENTS.md — Agent Collaboration Guidelines

This document defines the roles and responsibilities of the AI agents assisting in the development of the **水豚教練 (Capybara Coach)** project.

## Core Principle: Lead & Assist

The collaboration between agents follows a strict hierarchy to ensure architectural consistency and clear accountability:

1.  **Claude (Main Developer):**
    *   Acts as the primary engineer and architect.
    *   Responsible for the core implementation of features, roadmap execution, and final code structure.
    *   Follows the directives in `CLAUDE.md`.

2.  **Gemini (Assistant):**
    *   Acts as a specialized assistant and code monitor.
    *   **Monitor:** Observes the codebase for potential regressions, style inconsistencies, or missed edge cases.
    *   **Suggestions:** Provides proactive suggestions for optimization, alternative implementations, or security improvements.
    *   **Alignment:** Ensures all changes remain consistent with the persona and technical constraints defined in `GEMINI.md`.

## Interaction Workflow

*   **Implementation:** Claude leads the implementation phase. Gemini should avoid overlapping implementations unless specifically tasked with a sub-module or refactoring task.
*   **Review:** When Claude proposes a change, Gemini may analyze the diff to provide a "second pair of eyes" perspective.
*   **Knowledge Sharing:** Both agents use the structured `reports/` directory to communicate findings and blocked states to human developers.
