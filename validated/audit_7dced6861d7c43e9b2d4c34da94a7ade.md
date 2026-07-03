### Title
ETH Deposit Limit Check Omits `amount`, Allowing Unlimited ETH Deposits Beyond Protocol Cap - (File: `contracts/LRTDepositPool.sol`)

### Summary
The `_checkIfDepositAmountExceedesCurrentLimit` function in `LRTDepositPool` applies an incomplete comparison for ETH deposits: it checks only whether the current total already exceeds the limit, without including the incoming `amount`. Any unprivileged depositor can therefore deposit an arbitrarily large ETH amount in a single call as long as the running total has not yet crossed the cap, bypassing the deposit-limit invariant entirely.

### Finding Description
`_checkIfDepositAmountExceedesCurrentLimit` branches on asset type:

```solidity
// contracts/LRTDepositPool.sol  lines 676-682
function _checkIfDepositAmountExceedesCurrentLimit(address asset, uint256 amount) internal view returns (bool) {
    uint256 totalAssetDeposits = getTotalAssetDeposits(asset);
    if (asset == LRTConstants.ETH_TOKEN) {
        return (totalAssetDeposits > lrtConfig.depositLimitByAsset(asset));   // ← amount absent
    }
    return (totalAssetDeposits + amount > lrtConfig.depositLimitByAsset(asset)); // ← correct
}
```

For every ERC-20 asset the prospective deposit `amount` is added to `totalAssetDeposits` before comparing against the cap. For ETH the `amount` is silently dropped. The result is that the ETH branch returns `false` (i.e. "not exceeded") whenever `totalAssetDeposits <= depositLimit`, regardless of how large `amount` is. A single `depositETH` call can therefore push the protocol's ETH holdings arbitrarily far above the configured limit.

The companion view `getAssetCurrentLimit` correctly computes remaining capacity (lines 402-409), so it will already show `0` at the limit — yet the actual guard in `_beforeDeposit` (line 661) will still pass for ETH.

### Impact Explanation
The deposit limit is the protocol's primary mechanism for capping total ETH exposure (EigenLayer strategy capacity, risk management). Bypassing it allows over-minting of rsETH relative to the intended ceiling. Excess ETH that cannot be deployed into EigenLayer strategies sits idle, earning no restaking yield, so rsETH holders receive lower returns than the protocol promises. This maps to **Low — Contract fails to deliver promised returns, but doesn't lose value**.

### Likelihood Explanation
The entry path is the public, permissionless `depositETH()` function. No special role or precondition is required beyond having ETH. The condition is met whenever `totalAssetDeposits <= depositLimit`, which is the normal operating state of the protocol. Likelihood is **High**.

### Recommendation
Apply the same `+ amount` pattern used for ERC-20 assets:

```solidity
if (asset == LRTConstants.ETH_TOKEN) {
    return (totalAssetDeposits + amount > lrtConfig.depositLimitByAsset(asset));
}
```

### Proof of Concept
1. Admin sets `depositLimitByAsset[ETH_TOKEN] = 100_000 ether`.
2. Protocol accumulates exactly `100_000 ether` in ETH deposits; `getAssetCurrentLimit(ETH)` returns `0`.
3. Attacker calls `depositETH{value: 50_000 ether}(0, "")`.
4. Inside `_beforeDeposit` → `_checkIfDepositAmountExceedesCurrentLimit(ETH, 50_000 ether)`:
   - `totalAssetDeposits = 100_000 ether`
   - Check: `100_000 ether > 100_000 ether` → `false` → limit not exceeded.
5. `_mintRsETH` mints rsETH for `50_000 ether` beyond the cap.
6. Protocol now holds `150_000 ether` against a `100_000 ether` limit; the excess cannot be deployed into EigenLayer, suppressing yield for all rsETH holders. [1](#0-0) [2](#0-1) [3](#0-2)

### Citations

**File:** contracts/LRTDepositPool.sol (L402-409)
```text
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
