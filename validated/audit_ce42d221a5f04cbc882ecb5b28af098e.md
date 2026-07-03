The key vulnerability is in `_checkIfDepositAmountExceedesCurrentLimit` in `LRTDepositPool.sol`. For ETH, the check omits the deposit `amount` from the comparison, making the ETH deposit cap bypassable. Let me confirm the full context.### Title
ETH Deposit Limit Bypass Due to Missing `amount` in Limit Check - (File: contracts/LRTDepositPool.sol)

### Summary

`_checkIfDepositAmountExceedesCurrentLimit` applies an asymmetric check: for LST assets it correctly tests `totalAssetDeposits + amount > limit`, but for ETH it only tests `totalAssetDeposits > limit`, completely ignoring the incoming deposit amount. Any unprivileged depositor can therefore push total ETH deposits arbitrarily above the configured `depositLimitByAsset` cap.

### Finding Description

In `LRTDepositPool._checkIfDepositAmountExceedesCurrentLimit`, the ETH branch omits `amount` from the comparison:

```solidity
// contracts/LRTDepositPool.sol L676-682
function _checkIfDepositAmountExceedesCurrentLimit(address asset, uint256 amount) internal view returns (bool) {
    uint256 totalAssetDeposits = getTotalAssetDeposits(asset);
    if (asset == LRTConstants.ETH_TOKEN) {
        return (totalAssetDeposits > lrtConfig.depositLimitByAsset(asset));  // ← amount ignored
    }
    return (totalAssetDeposits + amount > lrtConfig.depositLimitByAsset(asset)); // ← correct for LSTs
}
```

The function returns `true` (reject) only when `totalAssetDeposits` **already** exceeds the limit. As long as the running total has not yet crossed the cap, the check returns `false` regardless of how large `amount` is. A single depositor can therefore send an arbitrarily large ETH value in one call to `depositETH` and the guard will not fire.

The call path is fully unprivileged and externally reachable:

```
depositETH(minRSETHAmountExpected, referralId)   [payable, nonReentrant, whenNotPaused]
  └─ _beforeDeposit(ETH_TOKEN, msg.value, ...)
       └─ _checkIfDepositAmountExceedesCurrentLimit(ETH_TOKEN, msg.value)
            └─ returns false  ← limit never triggered
  └─ _mintRsETH(rsethAmountToMint)               ← rsETH minted beyond cap
```

The analogous LST path (`depositAsset`) is not affected because it uses `totalAssetDeposits + amount > limit`.

### Impact Explanation

The `depositLimitByAsset` cap for ETH is the protocol's primary mechanism for controlling how much ETH is restaked into EigenLayer. Bypassing it allows:

- Unlimited rsETH minting beyond the intended cap, diluting the exchange rate for existing holders.
- Total ETH restaked in EigenLayer strategies to exceed the capacity the protocol was designed to handle, risking protocol insolvency.

This maps to **Low** (contract fails to deliver promised restrictions / enforce its deposit cap) with a clear path to **Medium** (temporary freezing of funds / protocol insolvency) if the cap was sized to match EigenLayer strategy capacity.

### Likelihood Explanation

The entry point is `depositETH`, which is public, payable, and requires no special role. Any depositor who holds enough ETH can trigger this in a single transaction. No front-running, governance capture, or admin compromise is required. Likelihood is **High**.

### Recommendation

Apply the same `+ amount` inclusion to the ETH branch:

```solidity
function _checkIfDepositAmountExceedesCurrentLimit(address asset, uint256 amount) internal view returns (bool) {
    uint256 totalAssetDeposits = getTotalAssetDeposits(asset);
    return (totalAssetDeposits + amount > lrtConfig.depositLimitByAsset(asset));
}
```

The ETH-specific branch is unnecessary once `amount` is included uniformly.

### Proof of Concept

Assume `depositLimitByAsset(ETH_TOKEN) = 1000 ether` and `totalAssetDeposits(ETH_TOKEN) = 999 ether`.

1. Attacker calls `depositETH{value: 10_000 ether}(0, "")`.
2. `_beforeDeposit` calls `_checkIfDepositAmountExceedesCurrentLimit(ETH_TOKEN, 10_000 ether)`.
3. `totalAssetDeposits = 999 ether`. ETH branch evaluates `999 ether > 1000 ether` → `false`.
4. Check passes; `_mintRsETH` mints rsETH for 10 000 ETH.
5. Total ETH in protocol is now 10 999 ether — 10× the intended cap — with no revert.

For LSTs, step 3 would evaluate `999 ether + 10_000 ether > 1000 ether` → `true` → `MaximumDepositLimitReached` revert. The ETH path has no equivalent protection. [1](#0-0) [2](#0-1) [3](#0-2)

### Citations

**File:** contracts/LRTDepositPool.sol (L76-93)
```text
    function depositETH(
        uint256 minRSETHAmountExpected,
        string calldata referralId
    )
        external
        payable
        nonReentrant
        whenNotPaused
        onlySupportedAsset(LRTConstants.ETH_TOKEN)
    {
        // checks
        uint256 rsethAmountToMint = _beforeDeposit(LRTConstants.ETH_TOKEN, msg.value, minRSETHAmountExpected);

        // interactions
        _mintRsETH(rsethAmountToMint);

        emit ETHDeposit(msg.sender, msg.value, rsethAmountToMint, referralId);
    }
```

**File:** contracts/LRTDepositPool.sol (L648-670)
```text
    function _beforeDeposit(
        address asset,
        uint256 depositAmount,
        uint256 minRSETHAmountExpected
    )
        private
        view
        returns (uint256 rsethAmountToMint)
    {
        if (depositAmount == 0 || depositAmount < minAmountToDeposit) {
            revert InvalidAmountToDeposit();
        }

        if (_checkIfDepositAmountExceedesCurrentLimit(asset, depositAmount)) {
            revert MaximumDepositLimitReached();
        }

        rsethAmountToMint = getRsETHAmountToMint(asset, depositAmount);

        if (rsethAmountToMint < minRSETHAmountExpected) {
            revert MinimumAmountToReceiveNotMet();
        }
    }
```

**File:** contracts/LRTDepositPool.sol (L676-682)
```text
    function _checkIfDepositAmountExceedesCurrentLimit(address asset, uint256 amount) internal view returns (bool) {
        uint256 totalAssetDeposits = getTotalAssetDeposits(asset);
        if (asset == LRTConstants.ETH_TOKEN) {
            return (totalAssetDeposits > lrtConfig.depositLimitByAsset(asset));
        }
        return (totalAssetDeposits + amount > lrtConfig.depositLimitByAsset(asset));
    }
```
