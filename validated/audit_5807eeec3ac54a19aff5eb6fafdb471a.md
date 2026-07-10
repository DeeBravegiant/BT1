### Title
Forged Foreign-Chain Verification via Cross-Request Signature Replay in `respond_verify_foreign_tx` - (File: crates/contract/src/lib.rs)

### Summary

`respond_verify_foreign_tx` accepts a caller-supplied `response.payload_hash` and verifies only that a valid root-key signature covers it. It never checks that `payload_hash` is the canonical `SHA-256(borsh(ForeignTxSignPayload{request: stored_request, values: observed_values}))` for the pending request being resolved. A single Byzantine leader node (strictly below the signing threshold) can replay a legitimately-produced signature from a prior request as the response for a different pending request, delivering a fraudulent `payload_hash` to the waiting bridge contract.

### Finding Description

In `respond_verify_foreign_tx` the contract performs