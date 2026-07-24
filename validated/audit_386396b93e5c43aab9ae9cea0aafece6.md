### Title
Pause Bypass via `storage_deposit` Resuming Yielded Transfers Without Pause Check — (`near/omni-bridge/src/storage.rs`, `near/omni-bridge/src/lib.rs`)

### Summary

The NEAR omni-bridge's pause mechanism guards all primary transfer entry points, but `storage_deposit`, `storage_withdraw`, and `storage_unregister` carry no pause guard. Most critically, `storage_deposit` unconditionally calls `resume_promise`, which invokes `env::promise_yield_resume` to wake a previously-yielded `init_transfer_resume` callback. That callback also carries no pause check and calls `init_transfer_internal`, which burns bridge tokens, locks native