"""Regression tests for the features_sec heading detector.

Real portfolio repos documented a Features section under a heading the old
detector missed: `**Features**` (bold), `🚀 Language Features` and
`Advanced Features` (a qualifier word other than key/core/main). These pin the
fix while keeping the original false-positive guard intact.
"""
import readme_forge as rf


def _has_features(*headings):
    md = "# Title\n\n" + "".join(f"## {h}\n\nbody\n\n" for h in headings)
    return rf.detect(md)["features_sec"]


def test_plain_and_qualified_features_headings_are_detected():
    for h in ("Features", "Key Features", "Core Features", "Main Features",
              "Advanced Features", "Notable Features"):
        assert _has_features(h), h


def test_bold_features_heading_is_detected():
    # `## **Features**` — markdown emphasis around the word.
    assert _has_features("**Features**")
    assert _has_features("**Features of the demo application**")


def test_emoji_prefixed_language_features_heading_is_detected():
    assert _has_features("🚀 Language Features")
    assert _has_features("✨ Features")


def test_numbered_features_heading_is_detected():
    assert _has_features("2) Features")


def test_heading_that_merely_mentions_features_is_rejected():
    # The original guard: a mid-phrase mention must not count.
    assert not _has_features("AI content pass (Features / Usage)")


def test_featured_is_not_features():
    assert not _has_features("Projects Featured")


def test_unrelated_headings_do_not_trigger():
    assert not _has_features("What's Inside", "Installation", "Table of Contents")
