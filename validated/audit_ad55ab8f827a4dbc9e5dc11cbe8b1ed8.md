Audit Report

## Title
ETH Deposit Limit Bypass Due to Missing `amount` in Cap Check - (File: contracts/LRTDepositPool.sol)

## Summary
`_checkIfDepositAmountExceedesCurrentLimit` in `LRTDepositPool` omits the incoming deposit `amount` from the ETH branch of the limit check, while correctly including it for ERC20 assets. As a result, any single ETH deposit can push total ETH holdings arbitrarily above the configured `depositLimitByAsset` cap without reverting, rendering the cap meaningless for ETH.

## Finding Description
In `contracts/LRTDepositPool.sol` at lines 676–682, the function `_checkIfDepositAmountExceedesCurrentLimit` has an asymmetric implementation:

```solidity
function _checkIfDepositAmountExceedesCurrentLimit(address asset, uint256 amount) internal view returns (bool) {
    uint256 totalAssetDeposits = getTotalAssetDeposits(asset);
    if (asset == LRTConstants.ETH_TOKEN) {
        return (totalAssetDeposits > lrtConfig.depositLimitByAsset(asset)); // amount omitted
    }
    return (totalAssetDeposits + amount > lrtConfig.depositLimitByAsset(asset)); // correct
}
```

The ETH branch compares only the pre-existing total against the limit; the incoming `amount` (`msg.value`) is never added. The function therefore returns `false` (no limit exceeded) for any ETH deposit as long as the pre-existing total has not already surpassed the limit, regardless of deposit size.

The call chain is: `depositETH` (L76–93) → `_beforeDeposit` (L648–670, passing `msg.value` as `depositAmount`) → `_checkIfDepositAmountExceedesCurrentLimit`. The flawed gate is the only deposit-limit enforcement for ETH. No other guard compensates for the missing `amount` in the ETH branch.

## Impact Explanation
**Low — Contract fails to deliver promised returns.**

The protocol configures `depositLimitByAsset[ETH_TOKEN]` (stored in `LRTConfig` at L23) as a hard cap on total ETH exposure. Because the incoming deposit amount is excluded from the ETH branch, a single depositor can push total ETH deposits arbitrarily above the configured cap in one transaction, and the protocol mints rsETH for the full over-limit amount. No funds are directly stolen, but the protocol invariant limiting ETH exposure is violated and more rsETH is minted than the cap was designed to allow.

## Likelihood Explanation
Any unprivileged user calling `depositETH` with `msg.value` large enough to exceed the remaining cap will succeed without revert, as long as `getTotalAssetDeposits(ETH_TOKEN)` has not already crossed the limit before their call. No special role, front-running, or multi-transaction setup is required. The condition is easily met in normal protocol operation.

## Recommendation
Add `amount` to the ETH branch, mirroring the ERC20 branch:

```solidity
if (asset == LRTConstants.ETH_TOKEN) {
    return (totalAssetDeposits + amount > lrtConfig.depositLimitByAsset(asset));
}
```

## Proof of Concept
1. Admin sets `depositLimitByAsset[ETH_TOKEN] = 100 ether`.
2. Protocol accumulates `getTotalAssetDeposits(ETH) = 99 ether` through normal usage.
3. Attacker calls `depositETH{value: 10_000 ether}(0, "")`.
4. Inside `_checkIfDepositAmountExceedesCurrentLimit`: `totalAssetDeposits (99e18) > depositLimit (100e18)` → `false` → no revert.
5. `_mintRsETH` mints rsETH for 10,000 ETH. Total ETH in protocol is now 10,099 ETH, far above the intended cap.

Foundry test plan: deploy `LRTDepositPool` with a mock `LRTConfig` setting `depositLimitByAsset[ETH_TOKEN] = 100 ether`, seed the pool with 99 ETH of prior deposits, call `depositETH{value: 10_000 ether}`, assert no revert and that `getTotalAssetDeposits(ETH_TOKEN)` exceeds 100 ether. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** contracts/LRTDepositPool.sol (L86-87)
```text
        // checks
        uint256 rsethAmountToMint = _beforeDeposit(LRTConstants.ETH_TOKEN, msg.value, minRSETHAmountExpected);
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

**File:** contracts/LRTConfig.sol (L23-23)
```text
    mapping(address token => uint256 amount) public depositLimitByAsset;
```
