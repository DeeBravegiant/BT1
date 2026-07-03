### Title
ETH Deposit Limit Bypass Due to Missing Amount Inclusion in Limit Check - (File: contracts/LRTDepositPool.sol)

### Summary
The `_checkIfDepositAmountExceedesCurrentLimit` function applies asymmetric validation logic for ETH versus LST assets. For ETH, the incoming deposit amount is omitted from the limit comparison, allowing a single ETH deposit to push the total protocol exposure above the configured cap.

### Finding Description
In `contracts/LRTDepositPool.sol`, the internal function `_checkIfDepositAmountExceedesCurrentLimit` is responsible for enforcing the per-asset deposit cap before any deposit is accepted.

For LST assets, the check correctly includes the new deposit amount: [1](#0-0) 

```solidity
return (totalAssetDeposits + amount > lrtConfig.depositLimitByAsset(asset));
```

For ETH (`LRTConstants.ETH_TOKEN`), the incoming `amount` is silently dropped from the comparison: [2](#0-1) 

```solidity
if (asset == LRTConstants.ETH_TOKEN) {
    return (totalAssetDeposits > lrtConfig.depositLimitByAsset(asset));
}
```

When `totalAssetDeposits == depositLimit`, the ETH branch evaluates `depositLimit > depositLimit` → `false`, so `_beforeDeposit` does not revert, and the full `msg.value` is accepted: [3](#0-2) 

The deposit then executes through `depositETH`, minting rsETH and accepting the ETH: [4](#0-3) 

There is no per-deposit maximum on `msg.value`, so the overshoot is unbounded in a single transaction.

### Impact Explanation
The deposit limit is a risk management parameter that caps total ETH exposure in the protocol. Because the ETH branch omits the incoming amount, a depositor can push the total ETH held by the protocol above the configured limit by an arbitrary amount in a single call. This violates the protocol's stated risk controls. No funds are directly stolen or frozen, but the contract fails to deliver its promised deposit cap enforcement.

**Impact: Low** — Contract fails to deliver promised returns (deposit cap enforcement), but does not lose value.

### Likelihood Explanation
Any unprivileged depositor can trigger this by calling `depositETH` at the moment `totalAssetDeposits == depositLimit`. This condition arises naturally as deposits accumulate toward the cap. No special privileges, front-running, or oracle manipulation are required.

### Recommendation
Unify the ETH and LST branches to always include the incoming amount:

```solidity
function _checkIfDepositAmountExceedesCurrentLimit(
    address asset,
    uint256 amount
) internal view returns (bool) {
    uint256 totalAssetDeposits = getTotalAssetDeposits(asset);
    return (totalAssetDeposits + amount > lrtConfig.depositLimitByAsset(asset));
}
```

### Proof of Concept
1. Admin sets the ETH deposit limit to 1 000 ETH via `lrtConfig`.
2. Cumulative ETH deposits reach exactly 1 000 ETH (`totalAssetDeposits == 1 000 ETH`).
3. Any user calls `depositETH{value: 500 ether}(0, "")`.
4. `_checkIfDepositAmountExceedesCurrentLimit` evaluates `1000 ETH > 1000 ETH` → `false`.
5. `_beforeDeposit` does not revert; `_mintRsETH` mints rsETH for the caller.
6. Total ETH in the protocol becomes 1 500 ETH — 50 % above the intended cap — with no admin action required.

### Citations

**File:** contracts/LRTDepositPool.sol (L76-92)
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
```

**File:** contracts/LRTDepositPool.sol (L648-669)
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
