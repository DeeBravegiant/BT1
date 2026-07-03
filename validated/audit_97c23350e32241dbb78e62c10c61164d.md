Audit Report

## Title
Default `pricePercentageLimit = 0` Permanently Disables Downside Auto-Pause, Enabling Deposit-at-Dip Dilution of Existing rsETH Holders — (File: `contracts/LRTOracle.sol`)

## Summary
`pricePercentageLimit` is never initialized in `initialize()` and defaults to `0`. Both the upside revert guard and the downside auto-pause in `_updateRsETHPrice()` are gated on `pricePercentageLimit > 0`, so both are silently disabled from deployment. The downside path is the concrete harm vector: during a slashing-induced price drop, the auto-pause of `LRTDepositPool` and `LRTWithdrawalManager` never fires, allowing any user to deposit at the depressed price and dilute existing rsETH holders when the price recovers.

## Finding Description
`initialize()` sets only `lrtConfig` and emits an event; `pricePercentageLimit` is left at the Solidity default of `0`: [1](#0-0) 

`setPricePercentageLimit()` writes the caller-supplied value with no lower-bound check, so an admin calling it with `0` (or never calling it at all) leaves the guards permanently disabled: [2](#0-1) 

Inside `_updateRsETHPrice()`, both guards short-circuit to `false` when `pricePercentageLimit == 0`: [3](#0-2) [4](#0-3) 

The auto-pause block that is bypassed: [5](#0-4) 

`updateRSETHPrice()` is `public` with no role restriction, so any EOA can trigger a price update: [6](#0-5) 

**Exploit path (dilution):**
1. Protocol deployed; `pricePercentageLimit == 0`.
2. EigenLayer slashing reduces TVL by 30%; `newRsETHPrice` falls from 1.0 to 0.7 ETH/rsETH.
3. Any caller invokes `updateRSETHPrice()`. `isPriceDecreaseOffLimit = (0 > 0) && ... = false`. Auto-pause is skipped; `LRTDepositPool` stays open.
4. An opportunistic depositor deposits 7 ETH, receiving 10 rsETH at the 0.7 price.
5. Slashing is resolved; TVL recovers. With the extra 10 rsETH outstanding, the recovered ETH is shared across more shares. Original holders receive less ETH per rsETH than they would have if the pool had been paused during the slashing event — the new depositor captured a portion of the recovery that belonged to pre-existing holders.

Concrete arithmetic: 100 rsETH pre-slash at 1.0 ETH → TVL = 100 ETH. After 30% slash: TVL = 70 ETH. New depositor adds 7 ETH → 110 rsETH, TVL = 77 ETH. Slashing resolved (+30 ETH) → TVL = 107 ETH, price = 107/110 ≈ 0.972. Original holders: 100 × 0.972 = 97.2 ETH (lost 2.8 ETH to dilution). Without the deposit, recovery would restore price to 1.0 for original holders.

## Impact Explanation
**Low — Contract fails to deliver promised returns, but doesn't lose value.** The auto-pause is an explicitly coded protection promising to halt deposits during large price drops. With `pricePercentageLimit == 0`, this promise is broken from day one. Existing rsETH holders receive less yield than they are entitled to when a slashing event is followed by recovery, because opportunistic depositors dilute the recovery gains. Funds are not directly stolen, but promised returns are not delivered.

## Likelihood Explanation
**Medium.** The vulnerable state (`pricePercentageLimit == 0`) is the **default from deployment** — no admin error is required. The protection is absent until an admin explicitly calls `setPricePercentageLimit` with a non-zero value. Additionally, an admin who later calls `setPricePercentageLimit(0)` silently re-enters the vulnerable state with no revert. The triggering event (a slashing-induced price drop) is a realistic, documented risk for EigenLayer-based protocols.

## Recommendation
1. Add a lower bound in `setPricePercentageLimit()`:
```solidity
function setPricePercentageLimit(uint256 _pricePercentageLimit) external onlyLRTAdmin {
    if (_pricePercentageLimit == 0 || _pricePercentageLimit > 1e18) revert InvalidPricePercentageLimit();
    pricePercentageLimit = _pricePercentageLimit;
    emit PricePercentageLimitUpdate(_pricePercentageLimit);
}
```
2. Initialize `pricePercentageLimit` to a sensible default (e.g., `5e16` = 5%) inside `initialize()` so the protection is active from the first price update.

## Proof of Concept
**Foundry fork test outline:**
```solidity
function test_downsidePauseBypassedWhenLimitIsZero() public {
    // 1. Deploy LRTOracle; assert pricePercentageLimit == 0
    assertEq(oracle.pricePercentageLimit(), 0);

    // 2. Simulate slashing: reduce mock asset balance by 30%
    mockDepositPool.setTotalAssetDeposits(asset, initialDeposit * 70 / 100);

    // 3. Any EOA calls updateRSETHPrice()
    vm.prank(attacker); // unprivileged address
    oracle.updateRSETHPrice();

    // 4. Assert depositPool and withdrawalManager are NOT paused
    assertFalse(depositPool.paused());
    assertFalse(withdrawalManager.paused());

    // 5. Attacker deposits at depressed price
    vm.prank(attacker);
    depositPool.depositAsset(asset, 7 ether);

    // 6. Simulate recovery: restore asset balance
    mockDepositPool.setTotalAssetDeposits(asset, initialDeposit);
    vm.prank(attacker);
    oracle.updateRSETHPrice();

    // 7. Assert original holders' rsETH is worth less than 1.0 ETH/rsETH
    assertLt(oracle.rsETHPrice(), 1 ether);
}
```

### Citations

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

**File:** contracts/LRTOracle.sol (L125-128)
```text
    function setPricePercentageLimit(uint256 _pricePercentageLimit) external onlyLRTAdmin {
        pricePercentageLimit = _pricePercentageLimit;
        emit PricePercentageLimitUpdate(_pricePercentageLimit);
    }
```

**File:** contracts/LRTOracle.sol (L256-257)
```text
            bool isPriceIncreaseOffLimit =
                pricePercentageLimit > 0 && priceDifference > pricePercentageLimit.mulWad(highestRsethPrice);
```

**File:** contracts/LRTOracle.sol (L273-274)
```text
            bool isPriceDecreaseOffLimit =
                pricePercentageLimit > 0 && diff > pricePercentageLimit.mulWad(highestRsethPrice);
```

**File:** contracts/LRTOracle.sol (L277-282)
```text
            if (isPriceDecreaseOffLimit) {
                if (!lrtDepositPool.paused()) lrtDepositPool.pause();
                if (!withdrawalManager.paused()) withdrawalManager.pause();
                _pause();
                return;
            }
```
