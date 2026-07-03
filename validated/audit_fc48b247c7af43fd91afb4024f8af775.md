Audit Report

## Title
ETH Deposit Limit Check Omits Deposit Amount, Allowing Limit Bypass - (File: contracts/LRTDepositPool.sol)

## Summary
`_checkIfDepositAmountExceedesCurrentLimit` applies an asymmetric comparison for ETH versus ERC20 assets. The ETH branch tests only whether the current total already exceeds the cap (`totalAssetDeposits > depositLimit`), omitting the incoming `amount`. A single `depositETH` call can therefore push the protocol's ETH holdings above the admin-configured cap without triggering a revert.

## Finding Description
At [1](#0-0)  the ETH branch on line 679 evaluates `totalAssetDeposits > lrtConfig.depositLimitByAsset(asset)`, while the ERC20 branch on line 681 correctly evaluates `totalAssetDeposits + amount > lrtConfig.depositLimitByAsset(asset)`. The function returns `true` (triggering a revert) only when the limit is already breached before the deposit, not when the deposit would breach it. The result is consumed at [2](#0-1)  inside `_beforeDeposit`, which is called unconditionally by the public `depositETH` entry point at [3](#0-2) . No other guard enforces the ETH deposit cap. The ERC20 path through `depositAsset` is unaffected.

## Impact Explanation
The deposit limit is the protocol's primary mechanism for capping ETH exposure. Because the ETH branch never includes the incoming deposit amount, any deposit that straddles the cap boundary succeeds when it should revert. The protocol will hold and restake more ETH than the admin configured, violating the deposit ceiling guarantee. This maps to **Low — contract fails to deliver promised returns**. If the cap was set to bound EigenLayer strategy capacity, excess deposits could cause downstream operational failures, elevating the impact to **Medium — temporary freezing of funds**.

## Likelihood Explanation
`depositETH` is fully permissionless: any address can call it with `msg.value > 0`. The vulnerable condition (`totalAssetDeposits ≤ depositLimit` and `totalAssetDeposits + amount > depositLimit`) is the normal boundary condition the limit is designed to enforce and will be reached in ordinary protocol operation. No special role, front-running, or external dependency is required. Likelihood is **High**.

## Recommendation
Add `amount` to the ETH branch to match the ERC20 branch:

```solidity
if (asset == LRTConstants.ETH_TOKEN) {
    return (totalAssetDeposits + amount > lrtConfig.depositLimitByAsset(asset));
}
```

## Proof of Concept
1. Admin sets `depositLimitByAsset[ETH_TOKEN] = 100 ether`.
2. `getTotalAssetDeposits(ETH_TOKEN)` returns `99 ether`.
3. Attacker calls `depositETH{value: 10 ether}(0, "")`.
4. Inside `_checkIfDepositAmountExceedesCurrentLimit`: `99 ether > 100 ether` → `false` → no revert.
5. `_mintRsETH` executes; total ETH in protocol becomes `109 ether`, exceeding the cap by `9 ether`.
6. For comparison, an ERC20 deposit of the same size at the same state correctly reverts: `99 ether + 10 ether > 100 ether` → `true` → `MaximumDepositLimitReached`.

**Foundry test plan:** Deploy `LRTDepositPool` with a mock `LRTConfig` returning `depositLimit = 100 ether`. Seed the pool with `99 ether` of recorded ETH deposits. Call `depositETH{value: 10 ether}` and assert it does not revert and that `getTotalAssetDeposits(ETH_TOKEN)` returns `109 ether`. Then assert that an equivalent ERC20 deposit reverts with `MaximumDepositLimitReached`.

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
