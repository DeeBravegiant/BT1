### Title
Missing `amount` in ETH Deposit Limit Check Allows Bypassing the Deposit Cap - (File: contracts/LRTDepositPool.sol)

### Summary
`LRTDepositPool._checkIfDepositAmountExceedesCurrentLimit` applies an incomplete limit check for ETH deposits. The ERC20 branch correctly adds the incoming `amount` to `totalAssetDeposits` before comparing against the cap, but the ETH branch omits this addition, allowing any depositor to push the protocol's ETH holdings arbitrarily beyond the configured deposit limit.

### Finding Description
`_checkIfDepositAmountExceedesCurrentLimit` is the sole guard that enforces the per-asset deposit cap set in `LRTConfig.depositLimitByAsset`. For ERC20 assets the check is:

```solidity
return (totalAssetDeposits + amount > lrtConfig.depositLimitByAsset(asset));
```

For ETH the check is:

```solidity
if (asset == LRTConstants.ETH_TOKEN) {
    return (totalAssetDeposits > lrtConfig.depositLimitByAsset(asset));
}
```

The `amount` being deposited is never added to `totalAssetDeposits` in the ETH branch. As a result, the check only asks "has the limit already been exceeded before this deposit?" rather than "will this deposit exceed the limit?" A depositor can therefore send an arbitrarily large ETH amount in a single call and the check will pass as long as the pre-deposit total is at or below the cap.

The vulnerable path is fully public:

1. `depositETH(minRSETHAmountExpected, referralId)` — no role restriction, `payable`, `nonReentrant`, `whenNotPaused`.
2. Calls `_beforeDeposit(LRTConstants.ETH_TOKEN, msg.value, minRSETHAmountExpected)`.
3. `_beforeDeposit` calls `_checkIfDepositAmountExceedesCurrentLimit(ETH_TOKEN, msg.value)`.
4. The ETH branch returns `false` (limit not exceeded) even when `totalAssetDeposits + msg.value >> depositLimit`.
5. `_mintRsETH` mints rsETH proportional to the full deposit. [1](#0-0) [2](#0-1) [3](#0-2) 

### Impact Explanation
The deposit limit is the protocol's primary risk-management control for capping EigenLayer exposure per asset. Bypassing it for ETH means:

- The protocol can accumulate ETH far beyond the intended ceiling, increasing EigenLayer slashing exposure beyond what governance approved.
- rsETH is minted against the excess ETH, so the token supply grows beyond the intended cap.
- The invariant "total ETH deposits ≤ `depositLimitByAsset(ETH)`" is silently violated, which the protocol's off-chain monitoring and on-chain accounting both rely on.

This maps to **Low — contract fails to deliver promised returns** (the deposit cap promise is broken without direct fund loss).

### Likelihood Explanation
The entry point is public and requires no special role. Any depositor who observes that `totalAssetDeposits` is near the limit can send a single large ETH deposit to exceed it. The condition is trivially reachable whenever the protocol is not paused and ETH deposits are enabled.

### Recommendation
Add `amount` to `totalAssetDeposits` in the ETH branch, mirroring the ERC20 branch:

```solidity
function _checkIfDepositAmountExceedesCurrentLimit(address asset, uint256 amount) internal view returns (bool) {
    uint256 totalAssetDeposits = getTotalAssetDeposits(asset);
    // Unified check: include the incoming amount for both ETH and ERC20
    return (totalAssetDeposits + amount > lrtConfig.depositLimitByAsset(asset));
}
```

### Proof of Concept
Assume `depositLimitByAsset(ETH_TOKEN) = 100 ether` and `getTotalAssetDeposits(ETH_TOKEN) = 99 ether`.

**ERC20 path** (e.g., stETH, 50 ether deposit):
- Check: `99e18 + 50e18 > 100e18` → `true` → reverts with `MaximumDepositLimitReached`. ✓

**ETH path** (50 ether deposit):
- Check: `99e18 > 100e18` → `false` → deposit succeeds, total becomes 149 ether. ✗

The depositor receives rsETH for 50 ETH that should have been rejected, and the protocol now holds 149 ETH against a 100 ETH cap. [1](#0-0)

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
