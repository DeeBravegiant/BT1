### Title
Unguarded Detached Parallel Burn-and-Mint in `swap_migrated_token` Causes Permanent Token Loss on Mint Failure — (`near/omni-bridge/src/lib.rs`)

### Summary

`swap_migrated_token` issues a parallel `burn.and(mint)` promise that is immediately `.detach()`ed, while `ft_on_transfer` simultaneously returns `U128(0)` to the NEP-141 token contract, permanently consuming the user's old tokens before the mint is confirmed. If the mint sub-promise fails for any reason, the user's old tokens are irreversibly destroyed with no new tokens issued and no recovery path.

### Finding Description

When a user calls `ft_transfer_call` on the old (migrated) token with the `SwapMigratedToken` message, the bridge's `ft_on_transfer` handler dispatches `swap_migrated_token` and immediately returns `U128(0)`: [1](#0-0) 

Returning `U128(0)` to the NEP-141 token contract means **zero tokens are refunded** — the user's old tokens are fully transferred to the bridge at this point, before any async work completes.

`swap_migrated_token` then constructs two **parallel** sub-promises via `Promise::and()` and detaches the combined promise with no callback: [2](#0-1) 

In NEAR's async model, `Promise::and()` schedules both sub-promises to execute independently. Neither sub-promise's success is a precondition for the other. Because the combined promise is `.detach()`ed, there is no callback to observe failure, no rollback, and no refund path. If `mint` fails (gas exhaustion, new token contract paused, insufficient minting rights, or any other runtime error), the `burn` sub-promise may have already executed — destroying the old tokens — while the user receives nothing.

### Impact Explanation

This is an **irreversible fund lock**: the user's old tokens are consumed by the `ft_transfer_call` mechanism (via the `U128(0)` return), then burned by the detached promise, while the new tokens are never minted. There is no on-chain recovery function. The lost amount equals the full `amount` the user sent. This matches the allowed impact: *"Irreversible fund lock, frozen redemption path, or permanently unclaimable user or protocol value in bridge, token, fee, vault, fast-transfer, or UTXO flows."*

### Likelihood Explanation

Any user invoking `SwapMigratedToken` is exposed. Failure conditions include:

- **Gas exhaustion**: The detached `burn.and(mint)` has no explicit `with_static_gas()` allocation. If the remaining prepaid gas after `ft_on_transfer` returns is insufficient for both cross-contract calls, one or both sub-promises silently fail.
- **New token contract unavailability**: If the new token contract is paused, upgraded to a broken state, or has revoked the bridge's minting rights, every `SwapMigratedToken` call will burn old tokens and mint nothing.

No privileged role is required to trigger the loss — any user calling `ft_transfer_call` with `SwapMigratedToken` is at risk.

### Recommendation

Replace the detached parallel promise with a sequential, callback-guarded chain:

1. First burn the old tokens (awaited).
2. Only on confirmed burn success, mint the new tokens.
3. Add a callback that, on any failure, refunds the user's old tokens (or reverts the burn if possible).

Alternatively, return the full `amount` from `ft_on_transfer` (refunding the user's old tokens) and only proceed with the swap inside a callback that has confirmed the burn succeeded.

### Proof of Concept

1. DAO calls `migrate_deployed_token(Eth, old_token, new_token)` — bridge state now maps `old_token → new_token`.
2. Alice holds 1000 `old_token`. She calls:
   ```
   old_token.ft_transfer_call(bridge, 1000, '{"SwapMigratedToken": null}')
   ```
3. Bridge's `ft_on_transfer` is invoked. It calls `swap_migrated_token(alice, old_token, 1000).detach()` and immediately returns `U128(0)`.
4. The NEP-141 token contract sees `U128(0)` returned — Alice's 1000 `old_token` are now permanently held by the bridge.
5. The detached `burn.and(mint)` fires. `burn(1000)` succeeds (bridge's old_token balance is destroyed). `mint(alice, 1000)` fails (e.g., out of gas, or new_token contract is paused).
6. Alice has lost 1000 `old_token` and received 0 `new_token`. No recovery function exists. [1](#0-0) [2](#0-1)

### Citations

**File:** near/omni-bridge/src/lib.rs (L279-283)
```rust
            BridgeOnTransferMsg::SwapMigratedToken => {
                self.swap_migrated_token(sender_id, token_id, amount)
                    .detach();
                PromiseOrPromiseIndexOrValue::Value(U128(0))
            }
```

**File:** near/omni-bridge/src/lib.rs (L2743-2758)
```rust
    fn swap_migrated_token(
        &mut self,
        sender_id: AccountId,
        old_token: AccountId,
        amount: U128,
    ) -> Promise {
        let new_token = self
            .migrated_tokens
            .get(&old_token)
            .near_expect(BridgeError::TokenNotMigrated);

        let burn = ext_token::ext(old_token).burn(amount);
        let mint = ext_token::ext(new_token).mint(sender_id, amount, None);

        burn.and(mint)
    }
```
