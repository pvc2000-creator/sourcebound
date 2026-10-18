# Changelog

All notable changes to this project are documented here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses [Semantic Versioning](https://semver.org/).

## [0.1.0] — 2026-10-18

### Added
- Bounded research agent: planning, BM25 search, evidence evaluation, query revision with retries, conflict detection, writing and citation verification.
- Free extractive mode that needs no API key, and an optional OpenAI-compatible model mode with automatic fallback.
- Sources from PDF, TXT, Markdown and HTML uploads, pasted text and public URLs (SSRF-protected, robots.txt respected).
- Web interface with live agent log, highlighted citations, evidence drawer, conflicts view, report history and Markdown/JSON export.
- Fictional sample library for trying the workflow.
- Security headers, rate limits, execution budgets and workspace data deletion.
- 90 unit and integration tests, 6 Playwright browser tests, strict mypy and TypeScript checks, GitHub Actions CI.
