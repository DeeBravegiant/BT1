Audit Report

## Title
Division-by-Zero Panic Revert in `getRsETHAmountToMint` When `rsETHPrice` Is Zero — (File: contracts/LRTDepositPool.sol)

## Summary
`LRTDepositPool.getRsETHAmountToMint` divides by `lrtOracle.rsETHPrice()` at line 520 with no zero guard. `LRTOracle.rsETHPrice` is a `uint256` storage variable that defaults to `0` and is not set in `initialize`; it is only populated by a call to `updateRSETHPrice()`. Any deposit attempted before that call executes will trigger a Solidity panic revert (0x12 — division by zero), making both public deposit entry-points completely unusable during that window.

## Finding Description
`LRTDepositPool.getRsETHAmountToMint` computes:

```solidity
rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
``` [1](#0-0) 

`lrtOracle.rsETHPrice()` reads the storage variable declared as:

```solidity
uint256 public override rsETHPrice;
``` [2](#0-1) 

`LRTOracle.initialize` sets only `lrtConfig` and emits an event — it never assigns `rsETHPrice`, so it remains `0` after deployment: [3](#0-2) 

`rsETHPrice` is only written inside `_updateRsETHPrice()`, which is reached via the public `updateRSETHPrice()`:

```solidity
function updateRSETHPrice() public whenNotPaused {
    _updateRsETHPrice();
}
``` [4](#0-3) 

Inside `_updateRsETHPrice`, the first assignment is:

```solidity
if (rsethSupply == 0) {
    rsETHPrice = 1 ether;
    ...
    return;
}
``` [5](#0-4) 

Until this runs, `rsETHPrice == 0`. Both public deposit entry-points call `_beforeDeposit` → `getRsETHAmountToMint` unconditionally: [6](#0-5) [7](#0-6) 

There is no existing guard in `getRsETHAmountToMint` that checks for a zero price before performing the division. [8](#0-7) 

## Impact Explanation
Any depositor calling `depositETH` or `depositAsset` while `rsETHPrice == 0` receives a panic revert. No ETH or LST is transferred (the revert occurs before any state change), so no funds are lost. The protocol's core deposit functionality is completely unavailable during this window. This maps to **Low — Contract fails to deliver promised returns, but doesn't lose value**.

## Likelihood Explanation
`rsETHPrice` is `0` only in the initial deployment window before the first successful `updateRSETHPrice()` call. Because `updateRSETHPrice()` is `public` and callable by any account (subject only to `whenNotPaused`), any actor — including the deployer or any user — can unblock deposits by calling it first. There is no on-chain enforcement requiring `updateRSETHPrice()` to be called before the deposit pool is unpaused and opened to users, so the condition is reachable by an unprivileged external caller. The window is narrow and self-correcting, making likelihood **Low**.

## Recommendation
Add an explicit zero-value guard in `getRsETHAmountToMint` before performing the division:

```solidity
uint256 currentRsETHPrice = lrtOracle.rsETHPrice();
if (currentRsETHPrice == 0) revert RsETHPriceNotInitialized();
rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / currentRsETHPrice;
```

Alternatively, initialize `rsETHPrice` to `1 ether` inside `LRTOracle.initialize`, mirroring the behaviour already present in `_updateRsETHPrice` when `rsethSupply == 0`.

## Proof of Concept
1. Deploy `LRTOracle` and `LRTDepositPool` in a fresh state (`rsETHPrice == 0`).
2. Do **not** call `updateRSETHPrice()`.
3. Unpause the deposit pool (or deploy with it unpaused).
4. Call `depositETH{value: 1 ether}(0, "")` as any EOA.
5. The call reverts with Solidity panic code `0x12` (division or modulo by zero) at `LRTDepositPool.sol` line 520, because `lrtOracle.rsETHPrice()` returns `0`.

### Citations

**File:** contracts/LRTDepositPool.sol (L86-88)
```text
        // checks
        uint256 rsethAmountToMint = _beforeDeposit(LRTConstants.ETH_TOKEN, msg.value, minRSETHAmountExpected);

```

**File:** contracts/LRTDepositPool.sol (L110-112)
```text
        // checks
        uint256 rsethAmountToMint = _beforeDeposit(asset, depositAmount, minRSETHAmountExpected);

```

**File:** contracts/LRTDepositPool.sol (L506-521)
```text
    function getRsETHAmountToMint(
        address asset,
        uint256 amount
    )
        public
        view
        override
        returns (uint256 rsethAmountToMint)
    {
        // setup oracle contract
        address lrtOracleAddress = lrtConfig.getContract(LRTConstants.LRT_ORACLE);
        ILRTOracle lrtOracle = ILRTOracle(lrtOracleAddress);

        // calculate rseth amount to mint based on asset amount and asset exchange rate
        rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
    }
```

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

**File:** contracts/LRTOracle.sol (L87-89)
```text
    function updateRSETHPrice() public whenNotPaused {
        _updateRsETHPrice();
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
