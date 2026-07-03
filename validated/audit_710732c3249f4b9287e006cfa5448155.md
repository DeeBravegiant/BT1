Now I have all the code I need. Let me trace the exact execution path carefully.

### Title
`_checkAndUpdateDailyFeeMintLimit` Revert Permanently Stales `rsETHPrice`, Enabling Depositors to Mint rsETH at Below-Fair-Value Price — (`contracts/LRTOracle.sol`)

---

### Summary

When `maxFeeMintAmountPerDay` is set to a non-zero value and accumulated yield causes `rsethAmountToMintAsProtocolFee` to exceed that limit, `_checkAndUpdateDailyFeeMintLimit` reverts inside `_updateRsETHPrice()` **before** `rsETHPrice` is written. Because the revert propagates through both `updateRSETHPrice()` (public) and `updateRSETHPriceAsManager()`, the price is permanently frozen at its stale value. Any depositor can then call `depositAsset` / `depositETH` and receive more rsETH than the current fair value warrants, diluting existing holders of their accrued yield.

---

### Finding Description

**Execution path:**

1. Anyone calls `LRTOracle.updateRSETHPrice()` (public, `whenNotPaused` only). [1](#0-0) 

2. Inside `_updateRsETHPrice()`, when `totalETHInProtocol > previousTVL` and the protocol is not paused, `protocolFeeInETH` is computed from the accumulated reward. [2](#0-1) 

3. `rsethAmountToMintAsProtocolFee` is derived and passed to `_checkAndUpdateDailyFeeMintLimit`. [3](#0-2) 

4. `_checkAndUpdateDailyFeeMintLimit` reverts unconditionally when the fee amount exceeds `maxFeeMintAmountPerDay`. [4](#0-3) 

5. The revert unwinds the entire call. The assignment `rsETHPrice = newRsETHPrice` at line 313 is **never reached**. [5](#0-4) 

6. `updateRSETHPriceAsManager()` calls the same `_updateRsETHPrice()` and hits the same revert — there is no privileged bypass. [6](#0-5) 

**Why the condition is self-reinforcing:**

`previousTVL` is computed as `rsethSupply * rsETHPrice` using the **stale** stored price. Each subsequent call to `updateRSETHPrice()` sees an even larger gap between `totalETHInProtocol` and `previousTVL`, producing an even larger fee, making the revert permanent until an admin raises `maxFeeMintAmountPerDay`. [7](#0-6) 

**How depositors exploit the stale price:**

`getRsETHAmountToMint` divides by `lrtOracle.rsETHPrice()`. With a stale (lower) price, the quotient is larger — depositors receive more rsETH per unit of asset than the true exchange rate warrants. [8](#0-7) 

---

### Impact Explanation

**High — Theft of unclaimed yield.**

Existing rsETH holders have accrued yield embedded in the protocol's TVL. When `rsETHPrice` is frozen below fair value, new depositors are minted rsETH at the stale rate, diluting the share of every existing holder. The yield that should have been reflected in a higher `rsETHPrice` is effectively transferred to new depositors. The protocol fee is also never collected for the blocked period.

---

### Likelihood Explanation

- `maxFeeMintAmountPerDay` is a live, settable parameter (`setMaxFeeMintAmountPerDay` is callable by any LRT manager). A conservative value (e.g., 1 rsETH = 1e18) is a natural choice.
- Multi-day accumulation is routine: if `updateRSETHPrice()` is not called for several days (e.g., due to gas costs, keeper downtime, or the `pricePercentageLimit` guard blocking non-manager callers), the accumulated fee for a large TVL easily exceeds a per-day cap.
- No attacker action is required beyond calling the already-public `updateRSETHPrice()` at the right moment, or simply waiting while the price remains stale and depositing.

---

### Recommendation

Decouple fee minting from the price update. When the fee exceeds `maxFeeMintAmountPerDay`, **cap the minted fee at the daily limit** (or skip fee minting entirely for that call) rather than reverting, and still write `rsETHPrice = newRsETHPrice`. For example:

```solidity
if (protocolFeeInETH > 0) {
    uint256 rsethAmountToMintAsProtocolFee = protocolFeeInETH.divWad(newRsETHPrice);
    // Cap at remaining daily limit instead of reverting
    uint256 mintable = _capAndUpdateDailyFeeMintLimit(rsethAmountToMintAsProtocolFee);
    if (mintable > 0) {
        IRSETH(rsETHTokenAddress).mint(treasury, mintable);
        emit FeeMinted(treasury, mintable);
    }
}
rsETHPrice = newRsETHPrice; // always update price
```

This preserves the rate-limiting intent while ensuring `rsETHPrice` is always kept current.

---

### Proof of Concept

```solidity
// SPDX-License-Identifier: UNLICENSED
pragma solidity 0.8.27;

import "forge-std/Test.sol";

// Fork test outline (local fork, no public mainnet)
contract DailyFeeLimitDoSTest is Test {
    ILRTOracle oracle = ILRTOracle(ORACLE_ADDR);
    ILRTDepositPool pool = ILRTDepositPool(POOL_ADDR);

    function setUp() public {
        // 1. Fork at a block where protocol has significant TVL
        // 2. As LRT manager, set maxFeeMintAmountPerDay = 1e18 (1 rsETH)
        vm.prank(manager);
        oracle.setMaxFeeMintAmountPerDay(1e18);

        // 3. Warp forward 3 days to simulate multi-day rETH rate accumulation
        //    (rETH.getExchangeRate() increases ~0.01%/day on mainnet)
        vm.warp(block.timestamp + 3 days);
    }

    function testPriceUpdateReverts() public {
        // 4. Unprivileged caller attempts price update
        vm.expectRevert(ILRTOracle.DailyFeeMintLimitExceeded.selector);
        oracle.updateRSETHPrice();

        // 5. rsETHPrice is still the stale value
        uint256 stalePrice = oracle.rsETHPrice();

        // 6. Depositor mints rsETH at stale (lower) price
        uint256 rethAmount = 10 ether;
        deal(RETH_ADDR, attacker, rethAmount);
        vm.startPrank(attacker);
        IERC20(RETH_ADDR).approve(address(pool), rethAmount);
        uint256 rsethMinted = pool.depositAsset(RETH_ADDR, rethAmount, 0, "");
        vm.stopPrank();

        // 7. Assert rsETH minted exceeds fair value
        //    fair = rethAmount * rethRate / trueRsETHPrice
        //    actual = rethAmount * rethRate / stalePrice  (stalePrice < trueRsETHPrice)
        uint256 fairRseth = (rethAmount * IrETH(RETH_ADDR).getExchangeRate()) / trueRsETHPrice();
        assertGt(rsethMinted, fairRseth, "attacker received excess rsETH at stale price");
    }
}
```

### Citations

**File:** contracts/LRTOracle.sol (L87-89)
```text
    function updateRSETHPrice() public whenNotPaused {
        _updateRsETHPrice();
    }
```

**File:** contracts/LRTOracle.sol (L94-96)
```text
    function updateRSETHPriceAsManager() external onlyLRTManager {
        _updateRsETHPrice();
    }
```

**File:** contracts/LRTOracle.sol (L205-207)
```text
        if (currentPeriodMintedFeeAmount + feeAmount > maxFeeMintAmountPerDay) {
            revert DailyFeeMintLimitExceeded(currentPeriodMintedFeeAmount + feeAmount, maxFeeMintAmountPerDay);
        }
```

**File:** contracts/LRTOracle.sol (L233-234)
```text
        // calculate previousTVL using rsethSupply multiplied by rsETHPrice
        uint256 previousTVL = rsethSupply.mulWad(rsETHPrice);
```

**File:** contracts/LRTOracle.sol (L244-247)
```text
        if (!protocolPaused && totalETHInProtocol > previousTVL) {
            uint256 rewardAmount = totalETHInProtocol - previousTVL;
            protocolFeeInETH = (rewardAmount * lrtConfig.protocolFeeInBPS()) / 10_000;
        }
```

**File:** contracts/LRTOracle.sol (L299-303)
```text
        if (protocolFeeInETH > 0) {
            // Calculate rsETH amount to mint as protocol fee
            uint256 rsethAmountToMintAsProtocolFee = protocolFeeInETH.divWad(newRsETHPrice);

            _checkAndUpdateDailyFeeMintLimit(rsethAmountToMintAsProtocolFee);
```

**File:** contracts/LRTOracle.sol (L313-313)
```text
        rsETHPrice = newRsETHPrice;
```

**File:** contracts/LRTDepositPool.sol (L519-521)
```text
        // calculate rseth amount to mint based on asset amount and asset exchange rate
        rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
    }
```
