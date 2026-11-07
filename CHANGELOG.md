# Changelog

All notable changes to this project are documented in this file.

## [Unreleased]

## [0.3.0] - 2026-11-08

### Added
- Backslash line continuation for multi-line input, with a `   ... ` prompt
- ANSI colours with TTY detection, Windows virtual-terminal setup and forced UTF-8 output
- Width-aware banner box that stays aligned for CJK persona names
- Retry for network errors, rate limits and 5xx (2 attempts, 1s/2s backoff)
- Ctrl+C during generation interrupts only that reply and keeps the session alive
- Optional `farewell` persona field for the goodbye line

### Changed
- Request failures now return to the prompt instead of ending the session
- `ChatAgentError` carries a `retryable` flag

## [0.2.0] - 2026-11-07

### Added
- Multi-turn conversation history
- Streaming assistant replies
- Token-budget based context trimming

### Changed
- `llm.chat_once` replaced by streaming `chat_stream`

## [0.1.0] - 2026-11-07

### Added
- Initial project skeleton: `src/` layout, uv lockfile, ruff/mypy/pytest setup
- Configuration and persona loading with field-level validation errors
- Structured persona rendered into a fixed system prompt
- Single-turn chat against `deepseek-flash` with thinking mode disabled
- Terminal REPL with startup banner, exit words and Ctrl+C/Ctrl+D handling
