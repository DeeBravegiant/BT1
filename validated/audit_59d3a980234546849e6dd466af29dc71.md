### Title
Manager Can Front-Run `instantWithdrawal` by Atomically Raising the Fee — (`File: contracts/LRTWithdrawalManager.sol`)

---

### Summary

`LRTWithdrawalManager.setInstantWithdrawalFee` has no timelock or delay. A manager can atomically raise the fee to 10% in the same block as a user's `instantWithdrawal` call, causing the user to receive up to 10% less than expected. The protocol's own NatSpec explicitly acknowledges this attack path.

---

### Finding Description

`instantWithdrawal` reads `instantWithdrawalFee` at execution time to compute the fee deducted from the user's withdrawal proceeds:

```solidity
// LRTWithdrawalManager.sol:237-238
uint256 fee = (assetAmountUnlocked * instantWithdrawalFee) / 10_000;
uint256 userAmount = assetAmountUnlocked - fee;
```

The fee is set by `setInstantWithdrawalFee`, which is callable by any address holding `MANAGER_ROLE` with no delay, no timelock, and no minimum notice period:

```solidity
// LRTWithdrawalManager.sol:372-376
function setInstantWithdrawalFee(uint256 feeBasisPoints) external onlyLRTManager {
    if (feeBasisPoints > 1000) revert FeeTooHigh(); // Max 10%
    instantWithdrawalFee = feeBasisPoints;
    emit InstantWithdrawalFeeUpdated(feeBasisPoints);
}
```

The protocol's own NatSpec on `instantWithdrawal` documents the attack:

> *"Uses the fee set at execution time. Managers can raise it right before this call, making withdrawals cost more than expected."* [1](#0-0) 

A compounding vector exists via `setInstantWithdrawalFeeRecipient`, which lets the manager redirect the fee to an arbitrary address (including their own) with the same zero-delay pattern: [2](#0-1) 

The structural parallel to the MetaSwap bug is exact: just as MetaSwap owners could swap an adapter implementation between a user's submission and execution, an LRT manager can swap the fee parameter between a user's submission and execution — both with no on-chain protection for the user.

---

### Impact Explanation

A user submitting `instantWithdrawal` for `N` rsETH expects to receive `assetAmountUnlocked` (the full ETH/LST equivalent). If the manager front-runs with `setInstantWithdrawalFee(1000)`, the user receives only `0.9 × assetAmountUnlocked`. The 10% difference is transferred to the fee recipient. Because the rsETH is **burned before the fee is computed**, the user cannot abort — the loss is irreversible. [3](#0-2) 

**Impact: High — Theft of user withdrawal proceeds (up to 10% of principal per transaction).**

---

### Likelihood Explanation

- The attack requires only a single `MANAGER_ROLE` holder to act maliciously or be compromised.
- No special conditions are needed: `instantWithdrawal` is a public, permissionless function available whenever `isInstantWithdrawalEnabled[asset]` is true.
- The fee change is a single, cheap transaction. A manager can monitor the mempool and front-run any pending `instantWithdrawal` call.
- The protocol's own comment acknowledges this is a known, reachable scenario.

**Likelihood: Medium** — requires a malicious or compromised manager, but the attack path is trivial once that condition holds, and the code itself flags it as a known risk.

---

### Recommendation

1. **Introduce a time-delayed fee update**: require a minimum notice period (e.g., 24–48 hours) between announcing a fee change and it taking effect, so users can observe the pending change and choose not to transact.
2. **Allow users to specify a maximum acceptable fee**: add a `maxFeeBasisPoints` parameter to `instantWithdrawal` and revert if `instantWithdrawalFee > maxFeeBasisPoints` at execution time, giving users a slippage-style protection.
3. **Gate fee changes behind the existing `TIMELOCK_ROLE`** (already used in `L1Vault`) rather than `MANAGER_ROLE`.

---

### Proof of Concept

1. Protocol has `instantWithdrawalFee = 0` and `isInstantWithdrawalEnabled[ETH] = true`.
2. Alice submits `instantWithdrawal(ETH, 10 ether_rsETH, "")` expecting to receive `~10 ETH`.
3. Manager observes Alice's pending transaction in the mempool and front-runs with `setInstantWithdrawalFee(1000)` (10%) and `setInstantWithdrawalFeeRecipient(manager_address)`.
4. Alice's transaction executes:
   - `assetAmountUnlocked ≈ 10 ETH`
   - `fee = 10 ETH × 1000 / 10000 = 1 ETH` → sent to manager's address
   - `userAmount = 9 ETH` → sent to Alice
5. Alice's rsETH is burned; she receives 9 ETH instead of 10 ETH. The 1 ETH is irrecoverably transferred to the manager. [4](#0-3) [5](#0-4)

### Citations

**File:** contracts/LRTWithdrawalManager.sol (L210-211)
```text
    /// @dev Uses the fee set at execution time. Managers can raise it right before this call, making withdrawals cost
    /// more than expected.
```

**File:** contracts/LRTWithdrawalManager.sol (L229-250)
```text
        IRSETH(lrtConfig.rsETH()).burnFrom(address(msg.sender), rsETHUnstaked);
        ILRTUnstakingVault unstakingVault = ILRTUnstakingVault(lrtConfig.getContract(LRTConstants.LRT_UNSTAKING_VAULT));
        if (assetAmountUnlocked > unstakingVault.getAssetsAvailableForInstantWithdrawal(asset)) {
            revert CantInstantWithdrawMoreThanAvailable();
        }

        unstakingVault.redeem(asset, assetAmountUnlocked);

        uint256 fee = (assetAmountUnlocked * instantWithdrawalFee) / 10_000;
        uint256 userAmount = assetAmountUnlocked - fee;

        address feeRecipient = instantWithdrawalFeeRecipient;
        if (feeRecipient == address(0)) {
            // Backwards-compatible default: send fees to the protocol treasury
            feeRecipient = lrtConfig.getContract(LRTConstants.PROTOCOL_TREASURY);
        }
        if (fee > 0) {
            _transferAsset(asset, feeRecipient, fee);
            emit InstantWithdrawalFeeCollected(msg.sender, asset, fee);
        }

        _transferAsset(asset, msg.sender, userAmount);
```

**File:** contracts/LRTWithdrawalManager.sol (L372-376)
```text
    function setInstantWithdrawalFee(uint256 feeBasisPoints) external onlyLRTManager {
        if (feeBasisPoints > 1000) revert FeeTooHigh(); // Max 10%
        instantWithdrawalFee = feeBasisPoints;
        emit InstantWithdrawalFeeUpdated(feeBasisPoints);
    }
```

**File:** contracts/LRTWithdrawalManager.sol (L384-388)
```text
    function setInstantWithdrawalFeeRecipient(address feeRecipient) external onlyLRTManager {
        UtilLib.checkNonZeroAddress(feeRecipient);
        instantWithdrawalFeeRecipient = feeRecipient;
        emit InstantWithdrawalFeeRecipientUpdated(feeRecipient);
    }
```
