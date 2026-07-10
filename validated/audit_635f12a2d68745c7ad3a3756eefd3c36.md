### Title
Incorrect check ordering in `KeyEventInstance::vote_success()` allows a single participant to repeatedly abort key generation attempts — (File: `crates/contract/src/state/key_event.rs`)

### Summary
`KeyEventInstance::vote_success()` checks for `PublicKeyDisagreement` **before** checking whether the candidate has already voted (`VoteAlreadySubmitted`). A participant who has already cast a valid vote can call `vote_pk` a second time with a different public key, bypass the duplicate-vote guard, trigger `PublicKeyDisagreement`, and abort the active key-generation instance. A single participant below the signing threshold can repeat this indefinitely, permanently preventing new domains from being added.

### Finding Description
The root cause is in `KeyEventInstance::vote_success()`:

```rust
fn vote_success(
    &mut