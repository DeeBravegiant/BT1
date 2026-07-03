The bug is real and concrete. Here is the full analysis:

---

### Title
`_emitMultipleDepositEvents` Hardcodes `shareReceived=0`, Enabling Off-Chain Yield Accounting Desync and Undetected KING Withdrawal - (File: `contracts/king-protocol/TokenSwap.sol`)

### Summary
`depositMultipleToKingProtocol` correctly computes the total shares received from King Protocol but delegates event emission to `_emitMultipleDepositEvents`, which unconditionally emits `shareReceived = 0` for every asset. Off-chain systems that rely on `TokensDeposited` events to track share entitlements will record zero shares for every multi-asset deposit, while the contract silently accumulates real KING tokens. A manager can then call `withdrawKing` to drain those tokens without the off-chain accounting system flagging the withdrawal as unauthorized.

### Finding Description

`depositMultipleToKingProtocol` computes `shareReceived` correctly: [1](#0-0) 

But then delegates event emission to `_emitMultipleDepositEvents`: [2](#0-1) 

Inside that helper, every event is emitted with a hardcoded `0` for `shareReceived`: [3](#0-2) 

Compare with the single-asset path, which correctly passes the actual value: [4](#0-3) 

`withdrawKing` has **no on-chain share-accounting guard** — it only checks the contract's raw KING token balance: [5](#0-4) 

This means the only mechanism that could prevent a manager from withdrawing more KING than legitimately earned is off-chain event monitoring. With `shareReceived = 0` emitted for all multi-asset deposits, that mechanism is blind to those deposits.

### Impact Explanation
A manager deposits real assets via `depositMultipleToKingProtocol`. King Protocol mints real KING shares to `TokenSwap`. The emitted `TokensDeposited` events report `shareReceived = 0`. Any off-chain yield-attribution system consuming these events credits zero shares to the deposit. The manager then calls `withdrawKing` to extract the KING tokens. The off-chain system sees no shares were ever received and does not flag the withdrawal, allowing the manager to withdraw yield that should be attributed to other parties or held for protocol purposes — **theft of unclaimed yield**.

### Likelihood Explanation
The `MANAGER_ROLE` is a privileged but not admin-level role. The multi-asset deposit path is a normal operational flow. No additional compromise is required beyond holding the manager role. The bug is triggered by the standard use of `depositMultipleToKingProtocol` with any two or more assets.

### Recommendation
Pass the computed `shareReceived` (or a per-asset breakdown) into `_emitMultipleDepositEvents` instead of hardcoding `0`. The simplest fix mirrors the single-asset path: emit the total shares once, or distribute shares proportionally per asset. At minimum:

```solidity
// In depositMultipleToKingProtocol, replace:
_emitMultipleDepositEvents(assets, amounts);

// With something that passes shareReceived:
_emitMultipleDepositEvents(assets, amounts, shareReceived);
```

And update `_emitMultipleDepositEvents` to accept and use the share value rather than hardcoding `0`.

### Proof of Concept
```solidity
// 1. Manager calls depositMultipleToKingProtocol([tokenA, tokenB], [amtA, amtB])
// 2. King Protocol mints N KING shares to TokenSwap
// 3. Parse emitted TokensDeposited events:
//    event[0]: shareReceived == 0  (should be > 0)
//    event[1]: shareReceived == 0  (should be > 0)
// 4. Off-chain accounting records 0 shares received for this deposit
// 5. Manager calls withdrawKing(recipient, N) — succeeds because KING balance >= N
// 6. Off-chain system does not flag the withdrawal (it recorded 0 shares received)
// Assert: all TokensDeposited.shareReceived == 0 regardless of actual KING minted
```

### Citations

**File:** contracts/king-protocol/TokenSwap.sol (L189-189)
```text
        emit TokensDeposited(asset, amount, shareReceived, msg.sender);
```

**File:** contracts/king-protocol/TokenSwap.sol (L209-211)
```text
        // Preview the deposit to get expected shares
        (uint256 expectedShares,) = kingProtocol.previewDeposit(assets, amounts);
        shareReceived = expectedShares;
```

**File:** contracts/king-protocol/TokenSwap.sol (L216-216)
```text
        _emitMultipleDepositEvents(assets, amounts);
```

**File:** contracts/king-protocol/TokenSwap.sol (L273-277)
```text
    function _emitMultipleDepositEvents(address[] memory assets, uint256[] memory amounts) internal {
        for (uint256 i = 0; i < assets.length; i++) {
            emit TokensDeposited(assets[i], amounts[i], 0, msg.sender);
        }
    }
```

**File:** contracts/king-protocol/TokenSwap.sol (L282-296)
```text
    function withdrawKing(address recipient, uint256 amount) external nonReentrant whenNotPaused onlyAdminOrManager {
        if (amount == 0) {
            revert ZeroAmount();
        }

        UtilLib.checkNonZeroAddress(recipient);

        uint256 contractBalance = kingToken.balanceOf(address(this));
        if (contractBalance < amount) {
            revert InsufficientBalance();
        }

        kingToken.safeTransfer(recipient, amount);

        emit KingWithdrawn(recipient, amount, msg.sender);
```
