### Title
Blacklisted EVM Recipient Permanently Locks NEAR-Side Funds in `finTransfer` — (File: evm/src/omni-bridge/contracts/OmniBridge.sol)

### Summary
When a user initiates a NEAR→EVM bridge transfer of a blacklist-capable token (e.g., USDC), and the recipient EVM address is subsequently blacklisted, every call to `finTransfer` will revert. Because the NEAR-side tokens are already locked or burned and the signed MPC payload hard-codes the blacklisted recipient, there is no protocol path to complete or refund the transfer. The funds are permanently unclaimable.

### Finding Description
`OmniBridge.finTransfer` handles the EVM leg of an inbound cross-chain transfer. For non-bridge, non-custom-minter ERC-20 tokens it executes:

```solidity
IERC20(payload.tokenAddress).safeTransfer(
    payload.recipient,
    payload.amount
);
``` [1](#0-0) 

`SafeERC20.safeTransfer` propagates any revert from the token contract. USDC (and other tokens with blacklists) revert when the `to` address is blacklisted. Because the entire `finTransfer` transaction reverts atomically, `completedTransfers[payload.destinationNonce]` is also rolled back: [2](#0-1) 

So the nonce is not permanently consumed, but the transfer can never succeed either, because:

1. The recipient address is embedded in the MPC-signed `TransferMessagePayload`. No one can change it without a new MPC signature.
2. There is no alternative delivery address, no pull-pattern fallback, and no admin rescue function in `OmniBridge`.
3. On the NEAR side, `init_transfer_internal` already burned or locked the tokens before the EVM leg was attempted: [3](#0-2) 

The NEAR-side state is committed and irreversible. The EVM-side transfer will revert on every retry. The user's funds are permanently stranded.

### Impact Explanation
**Critical — Irreversible fund lock / permanently unclaimable user value.**

The NEAR-side tokens (locked or burned) can never be recovered. The EVM-side transfer can never complete. The user loses the full bridged amount with no protocol-level remedy.

### Likelihood Explanation
USDC is one of the most commonly bridged stablecoins and Circle actively maintains a blacklist. A user can be blacklisted at any time after initiating the transfer (e.g., due to regulatory action, address compromise, or mistaken flagging). The window between `init_transfer` on NEAR and `finTransfer` on EVM can span multiple blocks or even hours if the relayer is slow, giving ample time for a blacklisting event to occur.

### Recommendation
Replace the direct `safeTransfer` to `payload.recipient` with a pull-payment (escrow) pattern for non-bridge ERC-20 tokens: credit the amount to an internal mapping keyed by `(token, recipient, nonce)` and let the recipient (or any address they designate) withdraw it in a separate transaction. This decouples settlement finality from the recipient's token-transfer eligibility and eliminates the permanent-lock risk.

Alternatively, wrap the transfer in a low-level try/catch and, on failure, escrow the funds so they can be claimed by the recipient or redirected via a governance action.

### Proof of Concept
1. Alice holds USDC on NEAR and calls `ft_on_transfer` → `init_transfer`. NEAR locks/burns her USDC. [3](#0-2) 
2. The MPC signs a `TransferMessagePayload` with `recipient = Alice_EVM`, `tokenAddress = USDC_EVM`, `amount = X`.
3. Before the relayer submits `finTransfer`, Circle blacklists `Alice_EVM`.
4. Relayer calls `OmniBridge.finTransfer(sig, payload)`.
5. Execution reaches `IERC20(USDC).safeTransfer(Alice_EVM, X)` — USDC reverts because `Alice_EVM` is blacklisted. [1](#0-0) 
6. The entire transaction reverts. `completedTransfers[nonce]` is rolled back.
7. Every subsequent retry of step 4 reverts identically.
8. Alice's NEAR-side USDC is permanently locked/burned. Her EVM USDC is permanently undeliverable.

### Citations

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L287-287)
```text
        completedTransfers[payload.destinationNonce] = true;
```

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L351-354)
```text
            IERC20(payload.tokenAddress).safeTransfer(
                payload.recipient,
                payload.amount
            );
```

**File:** near/omni-bridge/src/lib.rs (L1855-1862)
```rust
        if let OmniAddress::Near(token_id) = transfer_message.token.clone() {
            self.burn_tokens_if_needed(token_id.clone(), transfer_message.amount);

            self.lock_tokens_if_needed(
                transfer_message.get_destination_chain(),
                &token_id,
                transfer_message.amount.0,
            );
```
