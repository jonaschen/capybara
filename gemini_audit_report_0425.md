# Gemini Audit Report - 2026/04/25

## Executive Summary
This report provides an audit of the current `capybara-coach` codebase against the structural and architectural directives outlined in `CLAUDE.md` and `GEMINI.md`. Overall, the core infrastructure (webhook, GCS profile persistence, LLM abstractions, and state machine routing) is well-established. However, several critical risks and technical debts exist, primarily concerning cloud-native scalability and tight coupling.

---

## 🚨 Risk Assessment

### 1. High Risk: Stateful Webhook on Cloud Run
**Observation:** In `tools/line_webhook.py`, the routing logic uses in-memory dictionaries (`_user_states` and `_conversation_history`) to track the onboarding progress.
**Impact:** Cloud Run is a stateless, automatically scaling environment. If a user is in the `ONBOARDING` state and their next message hits a different Cloud Run instance (or the container restarts), their conversation history and state will be lost, breaking the onboarding flow.
**Suggestion:** 
- Persist `_conversation_history` and active `STATE` to a fast-access remote store (e.g., Firestore or Redis). 
- Alternatively, as a temporary mitigation, persist temporary state to GCS or pass conversation state through LINE's postback/session data if feasible, though a database is preferable for chat history.

### 2. Medium Risk: Missing RAG (Knowledge Base) Implementation
**Observation:** `tools/coach_reply.py` successfully implements domain detection (e.g., `triathlon`, `injury`) via keyword matching. However, it only prepends the `MANDATORY_INJURY_DISCLAIMER`. It does not yet dynamically load markdown context from the `knowledge_base/` directory as mandated in `GEMINI.md` (Section 4).
**Impact:** The bot relies entirely on the LLM's intrinsic knowledge rather than the curated, domain-specific Slim RAG knowledge base.
**Suggestion:** 
- Implement a `tools/rag_retriever.py` to map the detected domain to the corresponding markdown files in `knowledge_base/` and inject their contents into the `COACH_SYSTEM_PROMPT` before calling the LLM.

### 3. Low Risk: Hardcoded LLM Model Strings
**Observation:** Throughout the tools (`coach_reply.py`, `daily_push.py`, `plan_generator.py`, etc.), the LLM call explicitly requests `model="claude-sonnet-4-6"`.
**Impact:** While `tools/gemini_client.py` gracefully intercepts this string and overrides it with `GEMINI_MODEL_ID` when `LLM_PROVIDER=gemini`, the hardcoded values create a false impression of tight coupling to Anthropic.
**Suggestion:** 
- Remove the hardcoded model strings from business logic and let the underlying client factories (`get_llm_client`) inject the appropriate default model based on the active provider.

### 4. Low Risk: Incomplete GCS Profile Artifacts
**Observation:** `tools/gcs_profile.py` effectively handles `athlete_profile.md` and `training_plan.md`. However, `progress_log.json` and `preferences.json` (as outlined in `GEMINI.md` Section 3) are not yet implemented.
**Impact:** Missing feature parity with the design spec.
**Suggestion:** 
- Extend `gcs_profile.py` or introduce new modules to handle rolling JSON logs for user check-ins (`progress_log.json`) and preferences.

---

## 🛠️ Collaborative Role Adherence
As defined in `AGENTS.md`:
- **Claude** remains the primary driver of implementation.
- **Gemini** (this agent) has monitored the codebase and identified architectural gaps (specifically Cloud Run statefulness and RAG injection) without mutating the core files.

**Next Steps for Claude:** Prioritize resolving the in-memory state issue in `tools/line_webhook.py` before finalizing the production deployment of the onboarding flow.