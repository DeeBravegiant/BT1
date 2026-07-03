Audit Report

## Title
ETH Deposit Limit Not Enforced Correctly Due to Missing Amount in Boundary Check - (File: contracts/LRTDepositPool.sol)

## Summary
`_checkIfDepositAmountExceedesCurrentLimit` applies an asymmetric comparison for ETH versus ERC-20 assets. The ETH branch omits the incoming `amount` from the limit comparison, allowing a depositor to push total ETH deposits above the configured cap in a single transaction. The ERC-20 branch correctly includes `amount`; the ETH branch does not.

## Finding Description
At [1](#0-0)  the function reads:

```solidity
function _checkIfDepositAmountExceedesCurrentLimit(address asset, uint256 amount) internal view returns (bool) {
    uint256 totalAssetDeposits = getTotalAssetDeposits(asset);
    if (asset == LRTConstants.ETH_TOKEN) {
        return (totalAssetDeposits > lrtConfig.depositLimitByAsset(asset));   // amount NOT added
    }
    return (totalAssetDeposits + amount > lrtConfig.depositLimitByAsset(asset)); // amount IS added
}
```

When `totalAssetDeposits == depositLimit`, the ETH branch evaluates `limit > limit` → `false`, so the gate does not revert. The caller path is fully unprivileged:

- `depositETH` (payable, no role check) at [2](#0-1)  calls `_beforeDeposit`.
- `_beforeDeposit` at [3](#0-2)  calls `_checkIfDepositAmountExceedesCurrentLimit` and reverts only if it returns `true`.
- Because the ETH branch returns `false` when `totalAssetDeposits == limit`, `_mintRsETH` executes and mints rsETH above the cap. [4](#0-3) 

No existing guard compensates for this omission; the check is the sole enforcement point for the per-asset deposit cap.

## Impact Explanation
The deposit cap is the protocol's primary mechanism for bounding per-asset ETH exposure. When the cap is exactly reached, any subsequent ETH deposit still passes the gate, mints rsETH, and inflates `totalAssetDeposits` beyond the governance-approved ceiling. The protocol fails to deliver the promised deposit cap constraint. This matches the allowed impact: **Low — Contract fails to deliver promised returns, but doesn't lose value.**

## Likelihood Explanation
The condition is trivially reachable by any depositor: the attacker simply needs `totalAssetDeposits == depositLimit` (either by being the depositor who tips the total to exactly the limit, or by monitoring on-chain state) and then sends any non-zero ETH via `depositETH`. No special role, front-running advantage, or external dependency is required. The step is repeatable until the cap is meaningfully exceeded.

## Recommendation
Add the incoming `amount` to the ETH branch, mirroring the ERC-20 branch:

```solidity
if (asset == LRTConstants.ETH_TOKEN) {
    return (totalAssetDeposits + amount > lrtConfig.depositLimitByAsset(asset));
}
```

## Proof of Concept
1. Set `depositLimitByAsset(ETH_TOKEN) = 1000 ether` and bring `getTotalAssetDeposits(ETH_TOKEN)` to exactly `1000 ether`.
2. Call `depositETH{value: 1 ether}(0, "")` from any EOA.
3. `_checkIfDepositAmountExceedesCurrentLimit(ETH_TOKEN, 1 ether)` evaluates `1000 ether > 1000 ether` → `false` → no revert.
4. `_mintRsETH` mints rsETH for the caller.
5. `getTotalAssetDeposits(ETH_TOKEN)` is now `1001 ether`, exceeding the cap.
6. The step is repeatable by any depositor.

**Foundry test plan:** Deploy with a mock `lrtConfig` returning `depositLimitByAsset = 1000 ether`; seed the pool to exactly `1000 ether`; call `depositETH{value: 1 ether}`; assert the call does not revert and `getTotalAssetDeposits > depositLimitByAsset`.

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

**File:** contracts/LRTDepositPool.sol (L661-663)
```text
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
