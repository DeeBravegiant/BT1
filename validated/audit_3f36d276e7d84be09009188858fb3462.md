### Title
ETH Deposit Limit Check Omits Deposit Amount, Allowing Limit Bypass - (File: contracts/LRTDepositPool.sol)

### Summary
`LRTDepositPool._checkIfDepositAmountExceedesCurrentLimit()` applies an incomplete check for ETH deposits. Unlike ERC20 tokens, the ETH branch does not add the incoming deposit amount to `totalAssetDeposits` before comparing against the configured limit. Any single ETH deposit can therefore push the protocol far beyond its configured cap.

### Finding Description
In `_checkIfDepositAmountExceedesCurrentLimit()`, the two branches diverge:

```solidity
// contracts/LRTDepositPool.sol L676-L682
function _checkIfDepositAmountExceedesCurrentLimit(address asset, uint256 amount) internal view returns (bool) {
    uint256 totalAssetDeposits = getTotalAssetDeposits(asset);
    if (asset == LRTConstants.ETH_TOKEN) {
        return (totalAssetDeposits > lrtConfig.depositLimitByAsset(asset)); // ← amount NOT included
    }
    return (totalAssetDeposits + amount > lrtConfig.depositLimitByAsset(asset)); // ← amount included
}
``` [1](#0-0) 

For ETH the function only asks "is the current total already over the limit?" — it never asks "would this deposit push the total over the limit?" For ERC20 tokens the incoming `amount` is correctly added before the comparison.

This function is the sole gate in `_beforeDeposit()`:

```solidity
// contracts/LRTDepositPool.sol L661-L663
if (_checkIfDepositAmountExceedesCurrentLimit(asset, depositAmount)) {
    revert MaximumDepositLimitReached();
}
``` [2](#0-1) 

which is called by both `depositETH()` and `depositAsset()`: [3](#0-2) 

The analog to the reported vulnerability is exact: the report's `solverMetaTryCatch()` skips important checks when `needsPreSolver()` or `needsSolverPostCall()` is false; here the deposit-amount inclusion is skipped whenever `asset == ETH_TOKEN`, leaving the ETH deposit limit unenforced for the size of any individual deposit.

### Impact Explanation
The deposit limit is a protocol safety cap — it bounds EigenLayer exposure and total rsETH issuance. Because the ETH branch omits `amount`, the check only blocks deposits when `totalAssetDeposits` is **already** above the limit. While `totalAssetDeposits ≤ depositLimit`, a depositor can supply an arbitrarily large ETH amount in a single call, minting rsETH far beyond the intended ceiling. This causes the protocol to take on more EigenLayer restaking exposure than configured and inflates rsETH supply beyond the intended cap — the contract fails to deliver its promised deposit-limit protection.

**Impact: Low — contract fails to deliver promised returns (deposit cap), but no direct value loss.**

### Likelihood Explanation
Any unprivileged depositor can trigger this via the public `depositETH()` function with no preconditions beyond the protocol not being paused and the ETH token being supported. No role, key, or governance action is required.

### Recommendation
Include the deposit amount in the ETH branch, matching the ERC20 logic:

```solidity
if (asset == LRTConstants.ETH_TOKEN) {
    return (totalAssetDeposits + amount > lrtConfig.depositLimitByAsset(asset));
}
```

### Proof of Concept
1. Admin sets ETH deposit limit to 1 000 ETH via `lrtConfig`.
2. `totalAssetDeposits` for ETH is currently 0.
3. Depositor calls `depositETH{value: 10_000 ether}(0, "")`.
4. `_checkIfDepositAmountExceedesCurrentLimit(ETH_TOKEN, 10_000 ether)` evaluates `0 > 1_000 ether` → `false` → no revert.
5. `_mintRsETH` mints rsETH for 10 000 ETH; total ETH deposits become 10 000 ETH — 10× the configured limit.
6. The same depositor (or any other) can repeat until the limit is already exceeded, at which point the check finally triggers. [1](#0-0) [4](#0-3)

### Citations

**File:** contracts/LRTDepositPool.sol (L86-92)
```text
        // checks
        uint256 rsethAmountToMint = _beforeDeposit(LRTConstants.ETH_TOKEN, msg.value, minRSETHAmountExpected);

        // interactions
        _mintRsETH(rsethAmountToMint);

        emit ETHDeposit(msg.sender, msg.value, rsethAmountToMint, referralId);
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
