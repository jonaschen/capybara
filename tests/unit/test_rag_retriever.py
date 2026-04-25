"""Tests for tools/rag_retriever.py — Slim RAG file lookup + disclaimer trigger."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("LINE_CHANNEL_SECRET", "test_secret")
os.environ.setdefault("LINE_CHANNEL_ACCESS_TOKEN", "test_token")
os.environ.setdefault("ANTHROPIC_API_KEY", "test_key")

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools import rag_retriever  # noqa: E402


# ─── search_fitness_knowledge ───────────────────────────────────────────────


class TestSearchByDomain:
    def test_injury_returns_only_bnd_001(self):
        out = rag_retriever.search_fitness_knowledge(query="膝蓋痛", domain="injury")
        assert "卡皮教練的受傷邊界處理指南" in out
        assert "邊界模式的標準回應結構" in out
        # bnd_002 (disclaimer rules) must NOT be injected — that's used elsewhere
        assert "免責聲明自動注入規則" not in out

    def test_strength_concatenates_three_files(self):
        out = rag_retriever.search_fitness_knowledge(query="深蹲", domain="strength")
        # All three strength files should fit under the 800-token budget
        assert "漸進超負荷原則" in out
        assert "新手肌力訓練框架" in out
        assert "減量週" in out

    def test_recovery_returns_single_file(self):
        out = rag_retriever.search_fitness_knowledge(query="睡眠", domain="recovery")
        assert "睡眠與訓練超補償" in out

    def test_fat_loss_concatenates_two_files(self):
        out = rag_retriever.search_fitness_knowledge(query="TDEE", domain="fat_loss")
        assert "TDEE 計算" in out
        assert "增肌減脂同步" in out

    def test_general_fitness_returns_empty(self):
        assert rag_retriever.search_fitness_knowledge(query="你好", domain="general_fitness") == ""

    def test_nutrition_returns_empty(self):
        # Nutrition has no dedicated KB dir; race-day nutrition lives under triathlon.
        assert rag_retriever.search_fitness_knowledge(query="蛋白質", domain="nutrition") == ""

    def test_unknown_domain_returns_empty(self):
        assert rag_retriever.search_fitness_knowledge(query="x", domain="bogus") == ""


class TestTokenBudget:
    def test_triathlon_overflow_picks_single_best_file(self):
        """Triathlon's 5 files together exceed 800 tokens; retriever picks one."""
        # Query strongly hints at zone training
        out = rag_retriever.search_fitness_knowledge(
            query="什麼是 Zone 2 心率", domain="triathlon", max_tokens=800
        )
        assert "心率區間訓練" in out
        # Only one file returned: the others should NOT be present
        assert "磚塊訓練" not in out
        assert "台灣主要三鐵賽事" not in out
        assert "比賽日補給策略" not in out
        assert "初鐵完賽路線圖" not in out

    def test_triathlon_taiwan_query_picks_taiwan_races(self):
        out = rag_retriever.search_fitness_knowledge(
            query="台東 IRONMAN 報名", domain="triathlon", max_tokens=800
        )
        assert "台灣主要三鐵賽事" in out

    def test_triathlon_brick_query_picks_brick_file(self):
        out = rag_retriever.search_fitness_knowledge(
            query="brick 磚塊訓練怎麼做", domain="triathlon", max_tokens=800
        )
        assert "磚塊訓練" in out

    def test_triathlon_nutrition_query_picks_nutrition_file(self):
        out = rag_retriever.search_fitness_knowledge(
            query="比賽日補給要怎麼吃", domain="triathlon", max_tokens=800
        )
        assert "比賽日補給策略" in out

    def test_huge_budget_returns_everything(self):
        """When budget is generous, all triathlon files fit."""
        out = rag_retriever.search_fitness_knowledge(
            query="anything", domain="triathlon", max_tokens=10_000
        )
        for marker in ("初鐵完賽路線圖", "心率區間訓練", "磚塊訓練", "台灣主要三鐵賽事", "比賽日補給策略"):
            assert marker in out


class TestEstimateTokens:
    def test_chinese_text(self):
        # CJK proxy: len // 2; doesn't have to be billing-accurate, just bounded
        assert rag_retriever.estimate_tokens("一二三四") == 2

    def test_empty(self):
        assert rag_retriever.estimate_tokens("") == 0


# ─── should_inject_disclaimer ──────────────────────────────────────────────


class TestShouldInjectDisclaimer:
    @pytest.mark.parametrize(
        "text",
        [
            "膝蓋痛了三天",
            "我又拉傷了",
            "下背不舒服",
            "肩膀有點痠",
            "扭傷了腳踝",
            "胸悶要不要繼續練",       # symptom that the OLD injury domain missed
            "練到頭暈",                 # symptom that the OLD injury domain missed
            "心臟有點不舒服",           # symptom that the OLD injury domain missed
            "髖關節有感覺",             # symptom that the OLD injury domain missed
            "阿基里斯腱會酸",           # symptom that the OLD injury domain missed
        ],
    )
    def test_trigger_words_match(self, text: str):
        assert rag_retriever.should_inject_disclaimer(text) is True

    @pytest.mark.parametrize(
        "text",
        [
            "增肌怎麼開始",
            "今天 Zone 2 跑了 30 分鐘",
            "你好",
            "想了解 TDEE 計算",
            "",
        ],
    )
    def test_safe_text_does_not_trigger(self, text: str):
        assert rag_retriever.should_inject_disclaimer(text) is False

    def test_trigger_words_loaded_from_bnd_002(self):
        """The trigger list comes from boundaries/bnd_002_medical_disclaimer.md
        so updates to the policy file flow through automatically."""
        words = rag_retriever.DISCLAIMER_TRIGGERS
        for required in ("受傷", "膝蓋", "胸悶", "頭暈", "阿基里斯腱"):
            assert required in words, f"trigger {required!r} missing from parsed list"
