# Q1002: get code len or default receipt ordering invariant

## Question

What can an unprivileged user do by submitting transactions, deploying contracts, calling methods, and creating promise receipts so that `get_code_len_or_default` in `runtime/runtime/src/actions.rs` processes a contract-created promise graph, receiver account IDs, callback dependencies, and attached gas along the runtime state transition path? User controls a contract-created promise graph, receiver account IDs, callback dependencies, and attached gas -> `get_code_len_or_default` processes that value during receipt creation, local execution, incoming receipt application, and delayed queue draining -> the receipt ordering preserves dependency edges, shard routing, and exactly-once execution invariant might break -> potential in-scope impact is transaction manipulation, balance manipulation, or contract execution flow corruption under the NEAR HackenProof scope. Exploit hypothesis: a user-shaped promise DAG can make this code accept or execute receipts in an order that breaks dependency preservation and changes balances or callback results, violating the actual protocol invariant that receipt ordering preserves dependency edges, shard routing, and exactly-once execution.

## Target

- File/function: runtime/runtime/src/actions.rs:381::get_code_len_or_default
- Entrypoint: signed transaction submitted through public RPC and applied by runtime/runtime/src/lib.rs::Runtime::apply
- User-controlled input: a contract-created promise graph, receiver account IDs, callback dependencies, and attached gas
- Attack path: User controls a contract-created promise graph, receiver account IDs, callback dependencies, and attached gas -> public entrypoint reaches `get_code_len_or_default` -> receipt creation, local execution, incoming receipt application, and delayed queue draining handles the value -> invariant failure could produce transaction manipulation, balance manipulation, or contract execution flow corruption
- Security invariant: receipt ordering preserves dependency edges, shard routing, and exactly-once execution
- Expected bounty impact: transaction manipulation, balance manipulation, or contract execution flow corruption
- Fast validation approach: build a test-loop scenario with cross-shard promises, callbacks, refunds, and delayed receipts, then compare outcomes, receipt IDs, and final state roots across nodes
