I have found the key vulnerability. Let me confirm the exact lines before writing the report.### Title
ETH Deposit Limit Bypass Due to Missing Amount in Limit Check - (File: contracts/LRTDepositPool.sol)

### Summary
The `_checkIfDepositAmountExceedesCurrentLimit` function in `LRTDepositPool` enforces the deposit cap differently for ETH versus LST tokens. The ETH branch omits the incoming deposit `amount` from the comparison, meaning any depositor can exceed the configured ETH deposit limit by an arbitrary margin in a single transaction.

### Finding Description
In `_checkIfDepositAmountExceedesCurrentLimit` (lines 676–682), the limit check branches on asset type:

```solidity
function _checkIfDepositAmountExceedesCurrentLimit(address asset, uint256 amount) internal view returns (bool) {
    uint256 totalAssetDeposits = getTotalAssetDeposits(asset);
    if (asset == LRTConstants.ETH_TOKEN) {
        return (totalAssetDeposits > lrtConfig.depositLimitByAsset(asset)); // ← amount ignored
    }
    return (totalAssetDeposits + amount > lrtConfig.depositLimitByAsset(asset)); // ← correct
}
```

For LST tokens the check is `totalAssetDeposits + amount > limit` — the incoming deposit is included, so the cap is properly enforced. For ETH the check is only `totalAssetDeposits > limit` — the incoming `amount` is never added. The function therefore returns `false` (i.e., "not exceeded") whenever the running total is at or below the limit, regardless of how large the new deposit is. `_beforeDeposit` (lines 648–670) calls this function and reverts on `true`; because the ETH branch never returns `true` until the total already exceeds the limit from a prior deposit, the cap is never enforced prospectively for ETH.

The public entry point `depositETH` (lines 76–93) is callable by any unprivileged user with any `msg.value`, and routes through `_beforeDeposit` → `_checkIfDepositAmountExceedesCurrentLimit`.

The companion view `getAssetCurrentLimit` (lines 402–409) correctly computes the remaining headroom as `limit - totalAssetDeposits`, but this value is never used in the enforcement path for ETH, creating a silent inconsistency between what the protocol advertises as the remaining limit and what it actually enforces.

### Impact Explanation
The ETH deposit limit is the protocol's primary risk-management cap on native ETH exposure. Bypassing it allows any depositor to push total ETH holdings arbitrarily above the configured ceiling in a single call. This over-inflates the ETH component of the rsETH backing, dilutes the per-share value for existing holders if the oracle does not immediately reflect the excess, and forces the protocol to restake or manage more ETH than its operational parameters were designed for — all of which can lead to temporary or permanent freezing of user funds if the downstream EigenLayer delegation or unstaking queue cannot absorb the excess. Impact: **Medium — temporary freezing of funds / contract fails to deliver promised returns**.

### Likelihood Explanation
The entry point `depositETH` is public and requires no special role. Any depositor who observes that `totalAssetDeposits` is at or near the ETH limit can immediately send an arbitrarily large ETH deposit in a single transaction. No flash loan, governance capture, or multi-step setup is required. Likelihood: **High**.

### Recommendation
Add the incoming `amount` to the ETH branch of the check, mirroring the LST branch:

```solidity
if (asset == LRTConstants.ETH_TOKEN) {
    return (totalAssetDeposits + amount > lrtConfig.depositLimitByAsset(asset));
}
```

This makes both paths consistent and ensures the cap is enforced prospectively for every deposit, regardless of asset type.

### Proof of Concept
Assume `depositLimitByAsset(ETH_TOKEN) = 1000 ether` and `getTotalAssetDeposits(ETH_TOKEN) = 999 ether`.

1. Attacker calls `depositETH{value: 5000 ether}(0, "")`.
2. `_beforeDeposit` calls `_checkIfDepositAmountExceedesCurrentLimit(ETH_TOKEN, 5000 ether)`.
3. ETH branch evaluates `999 ether > 1000 ether` → `false` → no revert.
4. `_mintRsETH` mints rsETH for 5000 ETH.
5. `getTotalAssetDeposits(ETH_TOKEN)` is now `6999 ether`, nearly 7× the intended cap.

For LST tokens the same scenario would evaluate `999 ether + 5000 ether > 1000 ether` → `true` → revert with `MaximumDepositLimitReached`. The ETH path silently accepts the deposit. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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

**File:** contracts/LRTDepositPool.sol (L399-409)
```text
    /// @notice gets the current limit of asset deposit
    /// @param asset Asset address
    /// @return currentLimit Current limit of asset deposit
    function getAssetCurrentLimit(address asset) public view override returns (uint256) {
        uint256 totalAssetDeposits = getTotalAssetDeposits(asset);
        if (totalAssetDeposits > lrtConfig.depositLimitByAsset(asset)) {
            return 0;
        }

        return lrtConfig.depositLimitByAsset(asset) - totalAssetDeposits;
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
