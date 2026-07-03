Audit Report

## Title
ETH Deposit Freeze via Force-Sent ETH Balance Inflation — (`contracts/LRTDepositPool.sol`)

## Summary
`getETHDistributionData()` computes the deposit pool's ETH holdings as the raw `address(this).balance`, which includes ETH force-sent via `selfdestruct`. An unprivileged attacker can inflate this value above the configured deposit limit, causing every subsequent `depositETH()` call to revert with `MaximumDepositLimitReached`. The freeze can be maintained indefinitely at low cost by repeating the attack each time an admin raises the limit.

## Finding Description
`getETHDistributionData()` at L480 reads the raw contract balance:

```solidity
ethLyingInDepositPool = address(this).balance;
```

This feeds into `getTotalAssetDeposits(ETH_TOKEN)`, which is consumed by `_checkIfDepositAmountExceedesCurrentLimit()` at L676–682:

```solidity
function _checkIfDepositAmountExceedesCurrentLimit(address asset, uint256 amount) internal view returns (bool) {
    uint256 totalAssetDeposits = getTotalAssetDeposits(asset);
    if (asset == LRTConstants.ETH_TOKEN) {
        return (totalAssetDeposits > lrtConfig.depositLimitByAsset(asset));
    }
    ...
}
```

Because `depositETH` is `payable`, `msg.value` is already credited to `address(this).balance` before `_beforeDeposit` runs, so the ETH-specific branch intentionally omits adding `amount` to `totalAssetDeposits`. This means the check compares the post-deposit balance against the limit. If an attacker force-sends ETH via `selfdestruct` to push `address(this).balance` above the deposit limit, `totalAssetDeposits > depositLimit` becomes permanently true, and every `depositETH` call reverts. The `receive()` function at L58 confirms the contract is a known ETH recipient, and its address is public. Note: post-EIP-6780 (Cancun), `selfdestruct` no longer destroys the calling contract unless created in the same transaction, but the ETH transfer to the target still executes, so the force-send mechanism remains fully functional.

## Impact Explanation
All ETH deposits to the protocol are blocked until a privileged admin raises the deposit limit. Because the attacker can repeat the attack each time the limit is raised (spending only `newLimit - currentTotal + 1 wei`), the freeze can be maintained indefinitely at low cost. This matches the allowed impact class **Medium — Temporary freezing of funds**, as ETH deposits are the primary deposit path for rsETH minting.

## Likelihood Explanation
The attack requires only a contract funded with a small amount of ETH (as little as 1 wei above the remaining deposit headroom). No privileged access is needed. The `LRTDepositPool` address is publicly known, and the `receive()` function confirms it is a known ETH recipient. Any unprivileged external actor can execute this. The attack is repeatable at low cost, making the effective freeze indefinite.

## Recommendation
Replace `address(this).balance` in `getETHDistributionData()` with an internal accounting variable (e.g., `totalETHDeposited`) that is incremented only through controlled entry points: `depositETH`, `receiveFromNodeDelegator`, `receiveFromLRTConverter`, and `receiveFromRewardReceiver`. Force-sent ETH would then be silently absorbed without affecting the deposit limit check. This mirrors the standard fix pattern for balance-inflation griefing: cap or ignore untracked balance increases.

## Proof of Concept
1. Observe current deposit limit `L = lrtConfig.depositLimitByAsset(ETH_TOKEN)` and current total `T = getTotalAssetDeposits(ETH_TOKEN)`. Assume `T < L`.
2. Deploy a contract `Bomb` pre-funded with `L - T + 1 wei`.
3. Call `Bomb.explode(address(lrtDepositPool))`, which executes `selfdestruct(payable(lrtDepositPool))`. The ETH is force-transferred to `lrtDepositPool`.
4. `address(lrtDepositPool).balance` is now `≥ L + 1 wei`.
5. Any user calling `depositETH{value: X}(...)` triggers: `_beforeDeposit` → `_checkIfDepositAmountExceedesCurrentLimit(ETH_TOKEN, X)` → `getTotalAssetDeposits(ETH_TOKEN)` → `getETHDistributionData()` → `ethLyingInDepositPool = address(this).balance` (now `≥ L + 1 + X`).
6. The check `totalAssetDeposits > depositLimitByAsset(ETH_TOKEN)` returns `true`, and the call reverts with `MaximumDepositLimitReached`.
7. Admin raises limit to `L'`. Attacker repeats step 2–6 with `L' - T' + 1 wei`. The freeze is maintained indefinitely. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** contracts/LRTDepositPool.sol (L58-58)
```text
    receive() external payable { }
```

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

**File:** contracts/LRTDepositPool.sol (L480-480)
```text
        ethLyingInDepositPool = address(this).balance;
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
