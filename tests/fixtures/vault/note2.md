---
title: Chunking Strategies for Long Notes
tags: [pkm, chunking, vectors]
date: 2024-02-01
---

# Chunking Strategies for Long Notes

When a note exceeds the context window of an embedding model, it must be split into smaller pieces.

## Heading-Based Chunking

The simplest strategy is to split at `##` headings. Each section becomes an independent chunk that can be embedded separately.

This works well for structured notes with clear topical sections.

## Semantic Chunking

A more sophisticated approach uses an LLM to identify semantic boundaries in unstructured text.

This produces higher-quality chunks but requires an additional LLM pass per document.

## Fixed-Size Chunking

The simplest possible approach: split every N tokens regardless of content structure.

This is fast but often produces chunks that cut through sentences or ideas mid-thought.
