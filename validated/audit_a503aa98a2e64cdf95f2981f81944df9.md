Audit Report

## Title
ETH Deposit Limit Check Excludes Incoming Amount, Allowing Limit Bypass - (File: contracts/LRTDepositPool.sol)

## Summary
`_checkIfDepositAmountExceedesCurrentLimit` applies an asymmetric limit check: the ERC20 branch correctly evaluates `totalAssetDeposits + amount > limit`, but the ETH branch omits `amount` and evaluates only `totalAssetDeposits > limit`. When cumulative ETH deposits equal the configured cap exactly, the guard returns `false` and any subsequent `depositETH` call is accepted, pushing the protocol past its own limit.

## Finding Description
In `contracts/LRTDepositPool.sol` at L676-682:

```solidity
function _checkIfDepositAmountExceedesCurrentLimit(address asset, uint256 amount) internal view returns (bool) {
    uint256 totalAssetDeposits = getTotalAssetDeposits(asset);
    if (asset == LRTConstants.ETH_TOKEN) {
        return (totalAssetDeposits > lrtConfig.depositLimitByAsset(asset)); // amount excluded
    }
    return (totalAssetDeposits + amount > lrtConfig.depositLimitByAsset(asset)); // amount included
}
```

The ETH branch uses a strict `>` without adding `amount`. When `totalAssetDeposits == depositLimit`, the expression is `false`, so `_beforeDeposit` (L661) does not revert and the deposit proceeds. After the call, `address(this).balance` (which backs `getTotalAssetDeposits(ETH_TOKEN)`) exceeds the limit by `msg.value`. The guard only activates on the *next* call once `totalAssetDeposits` itself exceeds the limit, meaning the protocol can absorb up to `limit + maxSingleDeposit` before blocking. The entry point `depositETH` (L76-93) is fully permissionless (`external payable`), reachable by any user.

## Impact Explanation
The ETH deposit limit is the protocol's governance-approved risk cap on native ETH exposure. The broken check means the cap is not enforced at the boundary: the protocol accepts ETH beyond the configured ceiling. This maps directly to the allowed Low impact: **"Contract fails to deliver promised returns, but doesn't lose value."** No funds are stolen or frozen; the invariant that total ETH deposits cannot exceed `depositLimitByAsset[ETH_TOKEN]` is violated.

## Likelihood Explanation
No special privileges, flash loans, or coordination are required. The condition is reached naturally as the protocol fills: once cumulative ETH deposits equal `depositLimitByAsset[ETH_TOKEN]`, the very next `depositETH` call from any unprivileged user bypasses the check. The exploit is repeatable until `totalAssetDeposits` strictly exceeds the limit (i.e., after at least one over-limit deposit has settled).

## Recommendation
Include `amount` in the ETH branch to match the ERC20 logic:

```solidity
function _checkIfDepositAmountExceedesCurrentLimit(address asset, uint256 amount) internal view returns (bool) {
    uint256 totalAssetDeposits = getTotalAssetDeposits(asset);
    return (totalAssetDeposits + amount > lrtConfig.depositLimitByAsset(asset));
}
```

## Proof of Concept
1. Admin sets `depositLimitByAsset[ETH_TOKEN] = 10_000 ether`.
2. Cumulative ETH deposits reach exactly `10_000 ether` (`getTotalAssetDeposits(ETH_TOKEN) == 10_000 ether`).
3. Alice calls `depositETH{value: 1 ether}(0, "")`.
4. Inside `_checkIfDepositAmountExceedesCurrentLimit`: ETH branch evaluates `10_000 ether > 10_000 ether` → `false` → no revert.
5. `_mintRsETH` executes; Alice receives rsETH.
6. `getTotalAssetDeposits(ETH_TOKEN)` is now `10_001 ether`, exceeding the limit.
7. Foundry test: set up a fork, seed the pool to exactly the limit via `deal`/`depositETH`, then assert that a subsequent `depositETH{value: 1 ether}` does not revert and that `getTotalAssetDeposits(ETH_TOKEN)` exceeds `depositLimitByAsset[ETH_TOKEN]` after the call. [1](#0-0) [2](#0-1) [3](#0-2)

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

**File:** contracts/LRTDepositPool.sol (L648-663)
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
