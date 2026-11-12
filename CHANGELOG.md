# Changelog

All notable changes to this project are documented in this file.

## [Unreleased]

## [0.5.0] - 2026-11-12

### Added
- `chatagent-robot` command: streaming tool calling against the `agent_control` robot HTTP API
- Robot tools for status, move, turn, posture, stop, emergency stop and clear-estop, with Chinese schemas and hard range validation
- Optional `[robot]` config section (`base_url`, `timeout_seconds`, `max_tool_rounds`) with defaults; existing configs without it keep working
- Environment overrides `AGENT_CONTROL_BASE_URL` and `AGENT_CONTROL_TOKEN`
- Tool-aware context trimming that never separates an assistant `tool_calls` message from its `tool` results
- Ctrl+C during tool execution calls `/api/stop` and writes an `interrupted` tool result back into history
- Interactive `y/N` confirmation before clearing an emergency stop
- Local mock OpenAI SSE + mock robot API end-to-end test; no network or real robot is used

### Changed
- Version bumped to 0.5.0; the original `chatagent` CLI, session, model and context behaviour is unchanged

## [0.4.0] - 2026-11-08

### Added
- `config.toml` and `persona.toml` ship inside the package, so an installed `chatagent` starts from any directory
- MIT licence

### Changed
- `api.max_retries` now decides how many times a failed request is retried
- The readme is written for people who just want to chat, not for developers

### Fixed
- Documentation matches the shipped behaviour again: file lookup order, retry count, input parsing, module list

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
