# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Support for `LANGCLAW__AGENT_NAME` environment variable to customize agent name
  used in message bus queue/topic names, deepagents, and display output
- Support for `LANGCLAW__CONFIG_DIR` environment variable to customize config
  directory location (defaults to `~/.langclaw`)
- `config.agent_name` and `config.config_dir` read-only properties for introspection
- Config directory validation in `load_config()` ensures directory is writable

### Changed
- Message bus queue/topic names now use configured agent name instead of hardcoded "langclaw"
- Deepagents agent name now uses configured value from `config.agent_name`
- CLI `status` command now displays agent name and config directory

### Migration Notes
- No breaking changes — defaults preserve existing behavior
- To move existing `~/.langclaw` directory: `mv ~/.langclaw ~/.newpath` and set `LANGCLAW__CONFIG_DIR=~/.newpath`
- Multi-instance deployments sharing message bus infrastructure should use unique `LANGCLAW__AGENT_NAME` values
