Audit Report

## Title
ETH Donation via Open `receive()` Inflates `address(this).balance`, Temporarily Freezing All `depositETH` Calls — (`contracts/LRTDepositPool.sol`)

## Summary
The `LRTDepositPool` contract exposes an unrestricted `receive()` fallback that allows any address to send ETH directly. Because `getETHDistributionData()` uses `address(this).balance` as the canonical ETH-in-pool measure, and `_checkIfDepositAmountExceedesCurrentLimit` for ETH checks only `totalAssetDeposits > limit` (not `totalAssetDeposits + amount > limit`), a direct ETH donation permanently inflates the deposit total and causes every subsequent `depositETH` call to revert with `MaximumDepositLimitReached` until an admin raises the limit.

## Finding Description

**Step 1 — Open `receive()` fallback** [1](#0-0) 

Any EOA or contract can send ETH here with no access control, no accounting, and no rsETH minted in return.

**Step 2 — `getETHDistributionData()` uses raw `address(this).balance`** [2](#0-1) 

`ethLyingInDepositPool` is set to `address(this).balance`, which includes any ETH sent via `receive()`. This value flows directly into `getTotalAssetDeposits(ETH)`. [3](#0-2) 

**Step 3 — ETH limit check omits `amount`, creating a permanent inflation** [4](#0-3) 

For ERC-20 assets the check is `totalAssetDeposits + amount > limit`. For ETH it is only `totalAssetDeposits > limit`, because `msg.value` is already reflected in `address(this).balance` at call time. This asymmetry means donated ETH is permanently counted in `totalAssetDeposits` for every future call, not just the current one.

**Step 4 — `_beforeDeposit` reverts on every subsequent `depositETH`** [5](#0-4) 

Once `totalAssetDeposits > depositLimitByAsset(ETH)`, every `depositETH` call reverts. Moving ETH to NodeDelegators or the UnstakingVault does not help because those balances are also summed in `getTotalAssetDeposits`.

## Impact Explanation

**Medium — Temporary freezing of funds.**

All user ETH deposits are frozen until an admin raises `depositLimitByAsset` for ETH. The freeze is temporary (not permanent) because an admin can raise the limit without a protocol upgrade, but no user action can resolve it. The donated ETH is permanently counted in `totalAssetDeposits` regardless of where it is moved within the protocol.

## Likelihood Explanation

- No privilege required; any address can trigger `receive()`.
- Cost to attacker equals the ETH needed to push `totalAssetDeposits` above the configured limit. As the protocol approaches its limit organically, this cost approaches zero.
- The attacker receives nothing in return (no rsETH is minted), making it a pure griefing attack, but one that is cheap near the limit and repeatable after each admin limit increase.
- The attack is unconditional and requires no victim interaction.

## Recommendation

1. **Track deposited ETH in a storage variable** incremented only inside `depositETH` and decremented on withdrawals/transfers, rather than relying on `address(this).balance`.
2. Alternatively, **restrict `receive()`** to known senders (NodeDelegators, RewardReceiver, LRTConverter) and remove the open fallback, forcing all ETH entry through the named functions (`receiveFromNodeDelegator`, `receiveFromRewardReceiver`, `receiveFromLRTConverter`) that already exist. [6](#0-5) 

## Proof of Concept

```solidity
// Precondition: totalAssetDeposits(ETH) == depositLimitByAsset(ETH) - 1 wei

// Attacker sends 2 wei directly — no function call needed
(bool ok,) = address(lrtDepositPool).call{value: 2}("");
require(ok);

// address(this).balance increased by 2 wei
// totalAssetDeposits(ETH) = depositLimitByAsset(ETH) + 1 > limit → true

// Any user attempting a legitimate deposit now reverts:
vm.expectRevert(ILRTDepositPool.MaximumDepositLimitReached.selector);
lrtDepositPool.depositETH{value: 1 ether}(0, "");
```

All cited line references are confirmed present in the repository:
- `receive()` at L58 [1](#0-0) 
- `ethLyingInDepositPool = address(this).balance` at L480 [2](#0-1) 
- ETH-only limit check at L678-679 [7](#0-6) 
- Revert path at L661-662 [8](#0-7)

### Citations

**File:** contracts/LRTDepositPool.sol (L58-58)
```text
    receive() external payable { }
```

**File:** contracts/LRTDepositPool.sol (L60-67)
```text
    /// @dev receive from RewardReceiver
    function receiveFromRewardReceiver() external payable { }

    /// @dev receive from LRTConverter
    function receiveFromLRTConverter() external payable { }

    /// @dev receive from NodeDelegator
    function receiveFromNodeDelegator() external payable { }
```

**File:** contracts/LRTDepositPool.sol (L440-441)
```text
        if (asset == LRTConstants.ETH_TOKEN) {
            return getETHDistributionData();
```

**File:** contracts/LRTDepositPool.sol (L480-480)
```text
        ethLyingInDepositPool = address(this).balance;
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
