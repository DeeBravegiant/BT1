Audit Report

## Title
Division by Zero in `getRsETHAmountToMint()` When `rsETHPrice` Is Uninitialized Blocks All Deposits - (File: contracts/LRTDepositPool.sol)

## Summary
`LRTOracle.rsETHPrice` defaults to `0` at deployment and is never set during `initialize()`. `LRTDepositPool.getRsETHAmountToMint()` divides by `lrtOracle.rsETHPrice()` without a zero-check, causing every deposit to revert with a division-by-zero panic until `updateRSETHPrice()` is explicitly called. This structurally guarantees a window in which all deposit functionality is non-functional.

## Finding Description
`LRTOracle` declares `rsETHPrice` as a plain storage variable with no initializer: [1](#0-0) 

`LRTOracle.initialize()` sets only `lrtConfig` and emits an event — it does not set `rsETHPrice`: [2](#0-1) 

`_updateRsETHPrice()` does handle the zero-supply bootstrap case by setting `rsETHPrice = 1 ether`, but this only executes after an explicit call to `updateRSETHPrice()` or `updateRSETHPriceAsManager()`: [3](#0-2) 

`getRsETHAmountToMint()` performs an unchecked division by `lrtOracle.rsETHPrice()`: [4](#0-3) 

The full call chain from a user deposit is:

1. `depositETH()` → `_beforeDeposit()` (line 87)
2. `_beforeDeposit()` → `getRsETHAmountToMint()` (line 665)
3. `getRsETHAmountToMint()` → `/ lrtOracle.rsETHPrice()` → division by zero → revert [5](#0-4) 

No existing guard in `_beforeDeposit()` or `getRsETHAmountToMint()` checks for `rsETHPrice == 0` before the division.

## Impact Explanation
All user deposits (`depositETH`, `depositAsset`) revert for the entire period during which `rsETHPrice == 0`. This constitutes **temporary freezing of funds** (Medium): the core deposit function of the protocol is completely non-functional, blocking users from depositing. No funds already in the protocol are lost, but the deposit entry point is fully disabled.

## Likelihood Explanation
The condition is structurally guaranteed at every fresh deployment of `LRTOracle`. No attacker action is required — any user who attempts a deposit before `updateRSETHPrice()` is called triggers the revert. `updateRSETHPrice()` is public and permissionless, so the window closes as soon as anyone calls it, but the window is guaranteed to exist and requires no special conditions to reproduce.

## Recommendation
Add a zero-check guard in `getRsETHAmountToMint()`:

```solidity
uint256 currentRsETHPrice = lrtOracle.rsETHPrice();
if (currentRsETHPrice == 0) revert RsETHPriceNotInitialized();
rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / currentRsETHPrice;
```

Alternatively, set `rsETHPrice = 1 ether` inside `LRTOracle.initialize()`, mirroring the zero-supply bootstrap logic already present in `_updateRsETHPrice()`.

## Proof of Concept
1. Deploy `LRTOracle` fresh. `rsETHPrice` storage slot = `0` (confirmed: no initializer sets it).
2. Deploy `LRTDepositPool` pointing to the above oracle.
3. Do **not** call `updateRSETHPrice()`.
4. Call `depositETH{value: 1 ether}(0, "")` as any EOA.
5. Execution reaches `getRsETHAmountToMint()` → `(1e18 * assetPrice) / 0` → EVM division-by-zero panic (Solidity 0.8.x) → transaction reverts.
6. All deposits are blocked until a separate `updateRSETHPrice()` transaction is mined.

Foundry test sketch:
```solidity
function test_depositRevertsWhenRsETHPriceZero() public {
    // rsETHPrice not yet set (updateRSETHPrice never called)
    assertEq(lrtOracle.rsETHPrice(), 0);
    vm.expectRevert(); // division by zero panic
    lrtDepositPool.depositETH{value: 1 ether}(0, "");
}
```

### Citations

**File:** contracts/LRTOracle.sol (L28-28)
```text
    uint256 public override rsETHPrice;
```

**File:** contracts/LRTOracle.sol (L64-68)
```text
    function initialize(address lrtConfigAddr) external initializer {
        UtilLib.checkNonZeroAddress(lrtConfigAddr);
        lrtConfig = ILRTConfig(lrtConfigAddr);
        emit UpdatedLRTConfig(lrtConfigAddr);
    }
```

**File:** contracts/LRTOracle.sol (L218-222)
```text
        if (rsethSupply == 0) {
            rsETHPrice = 1 ether;
            highestRsethPrice = 1 ether;
            return;
        }
```

**File:** contracts/LRTDepositPool.sol (L519-520)
```text
        // calculate rseth amount to mint based on asset amount and asset exchange rate
        rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
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
