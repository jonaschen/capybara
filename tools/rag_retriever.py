"""
tools/rag_retriever.py

Slim RAG: direct file lookup against knowledge_base/, no embeddings.

Two public surfaces:
  - search_fitness_knowledge(query, domain, max_tokens=800): per-domain KB
    content for system-prompt injection.
  - should_inject_disclaimer(text): whether the medical disclaimer should be
    appended to a coach reply, based on the trigger word list parsed from
    boundaries/bnd_002_medical_disclaimer.md.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)


KB_ROOT = Path(__file__).resolve().parent.parent / "knowledge_base"

_INJURY_REFERRAL_PATH = KB_ROOT / "boundaries" / "bnd_001_injury_referral.md"
_DISCLAIMER_RULES_PATH = KB_ROOT / "boundaries" / "bnd_002_medical_disclaimer.md"

# Domains with full KB directories. nutrition/general_fitness/unknown → "".
_DOMAIN_TO_DIR: dict[str, str] = {
    "triathlon": "triathlon",
    "strength": "strength",
    "fat_loss": "fat_loss",
    "recovery": "recovery",
}


# ─── Token budget ──────────────────────────────────────────────────────────


def estimate_tokens(text: str) -> int:
    """CJK-friendly cheap proxy: len // 2. Pure-Chinese ≈ 1 char/token,
    English ≈ 0.25 tokens/char; halving the char count is a conservative
    upper bound for prompt budgeting. Not a billing instrument."""
    return len(text) // 2


# ─── Knowledge lookup ─────────────────────────────────────────────────────


def _read_files(domain_dir: Path) -> list[tuple[Path, str]]:
    """Return [(path, content), …] for all .md files in domain_dir, sorted by name."""
    if not domain_dir.exists():
        return []
    files = sorted(domain_dir.glob("*.md"))
    return [(p, p.read_text(encoding="utf-8")) for p in files]


def _score_relevance(content: str, query_tokens: list[str]) -> int:
    """Count how many distinct query unigrams appear in the content. Cheap
    BM25-less lexical overlap; good enough for picking 1 of 5 KB files."""
    if not query_tokens:
        return 0
    return sum(1 for tok in set(query_tokens) if tok and tok in content)


def _tokenize_query(query: str) -> list[str]:
    """Coarse splitter: Chinese characters become individual tokens, and
    contiguous ASCII runs (English words, numbers) become whole tokens."""
    if not query:
        return []
    tokens: list[str] = []
    for chunk in re.findall(r"[A-Za-z0-9]+|[一-鿿]", query):
        tokens.append(chunk.lower())
    return tokens


def search_fitness_knowledge(query: str, domain: str, max_tokens: int = 800) -> str:
    """Return KB content for a domain, capped at max_tokens.

    - injury → boundaries/bnd_001_injury_referral.md (verbatim)
    - triathlon / strength / fat_loss / recovery → all files in that dir
      concatenated; if total exceeds max_tokens, return the single file most
      relevant to query (lexical overlap score, ties broken by filename order).
    - nutrition / general_fitness / unknown → ""
    """
    if domain == "injury":
        if _INJURY_REFERRAL_PATH.exists():
            return _INJURY_REFERRAL_PATH.read_text(encoding="utf-8")
        logger.warning(f"Missing {_INJURY_REFERRAL_PATH}")
        return ""

    sub = _DOMAIN_TO_DIR.get(domain)
    if not sub:
        return ""

    files = _read_files(KB_ROOT / sub)
    if not files:
        return ""

    joined = "\n\n---\n\n".join(content for _, content in files)
    if estimate_tokens(joined) <= max_tokens:
        return joined

    # Over budget — pick the single most relevant file.
    query_tokens = _tokenize_query(query)
    best_path, best_content = max(
        files,
        key=lambda pc: (_score_relevance(pc[1], query_tokens), -files.index(pc)),
    )
    logger.info(
        f"KB overflow for domain={domain} ({estimate_tokens(joined)} > {max_tokens} tokens); "
        f"picking single best file: {best_path.name}"
    )
    return best_content


# ─── Disclaimer trigger ───────────────────────────────────────────────────


_TRIGGER_SECTION_RE = re.compile(
    r"##\s*觸發詞[^\n]*\n(.+?)(?=\n##|\Z)", re.DOTALL
)


def _parse_trigger_words(rules_path: Path = _DISCLAIMER_RULES_PATH) -> set[str]:
    """Extract the 觸發詞 list from bnd_002_medical_disclaimer.md."""
    if not rules_path.exists():
        logger.warning(f"Missing {rules_path}; disclaimer triggers will be empty")
        return set()
    text = rules_path.read_text(encoding="utf-8")
    m = _TRIGGER_SECTION_RE.search(text)
    if not m:
        logger.warning(f"Could not parse 觸發詞 section from {rules_path}")
        return set()
    raw = m.group(1)
    # Split on Chinese / ASCII commas and whitespace; strip parenthetical
    # qualifiers like 「噁心（訓練中）」 → 「噁心」 so plain user text matches.
    words = set()
    for part in re.split(r"[、，,\s\n]+", raw):
        word = re.sub(r"[（(].*?[)）]", "", part).strip()
        if word and not word.startswith("#"):
            words.add(word)
    return words


DISCLAIMER_TRIGGERS: frozenset[str] = frozenset(_parse_trigger_words())


def should_inject_disclaimer(text: str) -> bool:
    """True iff any disclaimer trigger word appears in `text`. Checked
    independently of domain detection so symptoms like 胸悶 / 頭暈 /
    阿基里斯腱 also fire (they're not in coach_reply._DOMAIN_KEYWORDS["injury"])."""
    if not text:
        return False
    return any(trigger in text for trigger in DISCLAIMER_TRIGGERS)
