### Title
`respond_verify_foreign_tx` Accepts Caller-Supplied `payload_hash` Without Validating It Against the Pending Request - (File: crates/contract/src/lib.rs)

### Summary

`respond_verify_foreign_tx` in the MPC smart contract verifies only that the submitted signature is cryptographically valid over `response.payload_hash`, but never checks that `response.payload_hash` is actually derived from the `ForeignChainRpcRequest` stored in the pending `VerifyForeignTransactionRequest`. Any single attested MPC participant can therefore resolve a pending foreign-tx request with a `payload_hash` that was produced for a completely different request, delivering a forged attestation to the waiting caller.

### Finding Description

When a user calls `verify_foreign_transaction`, the contract stores a `VerifyForeignTransactionRequest` (containing the `ForeignChainRpcRequest`, `domain_id`, and `payload_version`) as the key in `pending_verify_foreign_tx