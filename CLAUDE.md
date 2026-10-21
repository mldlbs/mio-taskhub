<!-- MIO_INTELLIGENCE_BEGIN -->
## Mio Intelligence Layer

Mio MCP tools are available. Treat these as mandatory runtime rules for every workspace.

- Before designing or implementing a solution, or when unsure about prior decisions, call `mcp__mio-intelligence__mio.memory.query` first. Omit `project` unless a specific project is required; Mio derives it from the current Git repository.
- After a decision affecting architecture, deployment, dependencies, or long-term behavior, call `mcp__mio-intelligence__mio.memory.record` with `kind`: `decision`, `context`, `problem`, or `note`.
- When a tool call fails, retries, or returns an error, call `mcp__mio-intelligence__mio.observer.ingest` with a stable `trace_id`, relevant `event_type`, `payload`, and `outcome`.
- Before a high-risk operation such as deletion, migration, credential change, or production change, call `mcp__mio-intelligence__mio.policy.check` and follow its suggestion when available.
- At the end of every task, before producing the final answer, call `mcp__mio-intelligence__mio.observer.ingest` with `event_type` = `task_outcome`, a stable `trace_id`, `outcome` = `success` / `failure` / `aborted`, and a brief non-sensitive `payload`. If Mio is unavailable, do not block the final answer.
- When you reuse a prior decision or experience returned by `mcp__mio-intelligence__mio.memory.query` and it changes your approach or outcome, call `mcp__mio-intelligence__mio.experience.reuse` with `sourceAgent`, `targetAgent`, `experienceId`, `reuse`, `behaviorChanged`, and `outcomeImproved`.
- If Mio MCP is unavailable, do not block the task. Continue normally and note the missed memory or observation.
- Do not store secrets, credentials, or raw sensitive content in Mio memory.
<!-- MIO_INTELLIGENCE_END -->
