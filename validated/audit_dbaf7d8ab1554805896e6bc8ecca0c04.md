The code exactly matches the claim. The ETH branch at line 679 uses `totalAssetDeposits > limit` while the ERC20 branch at line 681 correctly uses `totalAssetDeposits + amount > limit`. The vulnerability is real and reachable by any unprivileged depositor.

Audit Report

## Title
ETH Deposit Limit Check Omits Deposit Amount, Allowing Limit Bypass - (File: contracts/LRTDepositPool.sol)

## Summary
`_checkIfDepositAmountExceedesCurrentLimit` applies an asymmetric comparison for ETH vs ERC20 assets: the ETH branch checks only whether existing deposits already exceed the limit, without adding the incoming `amount`. This allows any depositor to push total ETH holdings past the admin-configured ceiling by exactly one deposit when the running total sits at the limit.

## Finding Description
In `contracts/LRTDepositPool.sol` at lines 676–682, the function branches on `asset == LRTConstants.ETH_TOKEN`:

```solidity
// L679 — ETH branch: amount is never added
return (totalAssetDeposits > lrtConfig.depositLimitByAsset(asset));

// L681 — ERC20 branch: correct
return (totalAssetDeposits + amount > lrtConfig.depositLimitByAsset(asset));
``` [1](#0-0) 

When `totalAssetDeposits == depositLimit`, the ETH branch evaluates `limit > limit` → `false`, so `_beforeDeposit` does not revert and the deposit proceeds. [2](#0-1) 

`depositETH` is the sole public entry point for ETH deposits and passes `msg.value` directly to `_beforeDeposit`, which is the only guard. [3](#0-2) 

No other check in the call chain compensates for the missing `amount` term.

## Impact Explanation
The deposit limit is a protocol-level safety cap on ETH concentration. When the running total reaches exactly the configured ceiling, any subsequent ETH deposit bypasses the guard and is accepted, pushing total ETH above the limit by the deposited amount. rsETH is minted proportionally, so no direct fund theft occurs, but the protocol holds more ETH than the admin-configured ceiling allows. This matches **Low: Contract fails to deliver promised returns, but doesn't lose value** — the deposit limit invariant is broken without direct asset loss.

## Likelihood Explanation
No special permissions are required. As the protocol accumulates ETH deposits, `totalAssetDeposits` naturally approaches `depositLimitByAsset(ETH_TOKEN)`. Any unprivileged depositor calling `depositETH` at the boundary condition (`totalAssetDeposits == limit`) triggers the bypass. The condition is reachable in normal operation and repeatable until the admin manually adjusts the limit.

## Recommendation
Apply the same formula used for ERC20 assets to the ETH branch:

```solidity
function _checkIfDepositAmountExceedesCurrentLimit(address asset, uint256 amount) internal view returns (bool) {
    uint256 totalAssetDeposits = getTotalAssetDeposits(asset);
    if (asset == LRTConstants.ETH_TOKEN) {
        return (totalAssetDeposits + amount > lrtConfig.depositLimitByAsset(asset));
    }
    return (totalAssetDeposits + amount > lrtConfig.depositLimitByAsset(asset));
}
```

## Proof of Concept
1. Admin sets ETH deposit limit to 1 000 ETH via `lrtConfig.depositLimitByAsset(ETH_TOKEN)`.
2. Cumulative ETH deposits reach exactly 1 000 ETH (`totalAssetDeposits == 1000e18`).
3. Depositor calls `depositETH{value: 10 ether}(0, "")`.
4. `_checkIfDepositAmountExceedesCurrentLimit(ETH_TOKEN, 10e18)` evaluates `1000e18 > 1000e18` → `false`.
5. `_beforeDeposit` does not revert; rsETH is minted for the depositor.
6. Total ETH in protocol is now 1 010 ETH — 10 ETH above the configured limit.
7. Every subsequent depositor repeats steps 3–6 until the admin raises or resets the limit.

**Foundry test plan:** Deploy `LRTDepositPool` with a mock `lrtConfig` returning `depositLimitByAsset = 1000e18`. Seed `getTotalAssetDeposits(ETH_TOKEN)` to return `1000e18`. Call `depositETH{value: 1 ether}`. Assert the call succeeds (no revert) and that `getTotalAssetDeposits` now exceeds the limit, confirming the bypass.

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
