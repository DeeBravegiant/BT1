Looking at the external report's vulnerability class — **state migration without checking invariants** — I need to find an analog in NEAR MPC where a migration/transfer function combines state without checking limits.

I examined the most relevant migration path in the codebase: `start_node_migration` → `conclude_node_migration`.