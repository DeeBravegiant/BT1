Audit Report

## Title
Stale `rsETHPrice` Used in Deposit Minting Allows Yield Extraction from Existing Holders — (`contracts/LRTDepositPool.sol`)

## Summary
`LRTDepositPool.getRsETHAmountToMint()` divides by `LRTOracle.rsETHPrice`, a state variable updated only when `updateRSETHPrice()` is explicitly called. Neither `depositETH()` nor `depositAsset()` calls `updateRSETHPrice()` before minting, so any deposit made while the stored price lags behind the true TVL-derived price causes the depositor to receive excess rsETH, diluting the accrued yield of existing holders.

## Finding Description
`rsETHPrice` is a plain storage variable in `LRTOracle`: [1](#0-0) 

It is written only inside `_updateRsETHPrice()`, which is triggered by the public `updateRSETHPrice()` or the manager-gated `updateRSETHPriceAsManager()`: [2](#0-1) 

The live price is computed from actual TVL inside `_updateRsETHPrice()`: [3](#0-2) 

The deposit path (`depositETH` → `_beforeDeposit` → `getRsETHAmountToMint`) never calls `updateRSETHPrice()`. It reads the stored price directly: [4](#0-3) 

Between keeper calls, staking rewards (beacon-chain ETH, stETH rebases, rETH/ETHx appreciation) continuously increase the true TVL while `rsETHPrice` remains frozen. A depositor who deposits during this window receives `depositValue / stalePrice` rsETH, which is more than `depositValue / truePrice`. After the next `updateRSETHPrice()` call, the attacker's rsETH is worth more than they paid, at the direct expense of pre-existing holders whose share of TVL is diluted.

An additional aggravating factor: `_updateRsETHPrice()` contains a `pricePercentageLimit` guard that causes the public `updateRSETHPrice()` call to revert if the price drift exceeds the configured threshold (only a manager can bypass it): [5](#0-4) 

This means that during large reward accrual periods, the public cannot force a price update, extending the staleness window and the exploitable gap.

## Impact Explanation
**High — Theft of unclaimed yield.**

Concrete example (from the report, verified against code):

| State | TVL (ETH) | rsETH supply | True price | Stored price |
|---|---|---|---|---|
| Before update | 1,050 | 1,000 | 1.05 | 1.00 (stale) |
| Attacker deposits 100 ETH | 1,150 | 1,100 | — | — |
| After update | 1,150 | 1,100 | 1.0455 | 1.0455 |

- Attacker paid 100 ETH, received 100 rsETH (correct: ≈95.24 rsETH). Post-update, 100 rsETH is worth **104.55 ETH** — a **4.55 ETH profit**.
- Original 1,000 rsETH holders' TVL share drops from 1,050 ETH to **1,045.5 ETH** — a **4.5 ETH loss of accrued yield**.

This matches the allowed impact "High. Theft of unclaimed yield." The magnitude scales linearly with deposit size and the staleness window.

## Likelihood Explanation
Medium-to-high. `updateRSETHPrice()` is not called atomically inside `depositETH()` or `depositAsset()`. Any deposit made between keeper calls exploits this automatically — no special permissions, no front-running required. The staleness window is determined by keeper cadence (typically hours to days). The `pricePercentageLimit` guard can extend the window further during high-reward periods. The attack is repeatable every reward cycle.

## Recommendation
Call `updateRSETHPrice()` (or an internal equivalent) at the start of `depositETH()` and `depositAsset()` before computing `rsethAmountToMint`, ensuring the price used for minting always reflects the current TVL. Alternatively, compute the live price inline within `getRsETHAmountToMint()` by calling `_getTotalEthInProtocol()` divided by `rsethSupply`, analogous to Compound's `exchangeRateCurrent()` pattern. The inline approach avoids the reentrancy and gas concerns of calling the full `_updateRsETHPrice()` (which mints fee rsETH as a side effect).

## Proof of Concept
1. Confirm staking rewards have accrued since the last `updateRSETHPrice()` call (e.g., stETH rebase occurred, beacon-chain rewards swept). The stored `rsETHPrice` is now lower than `_getTotalEthInProtocol() / rsethSupply`.
2. Call `LRTDepositPool.depositETH{value: X}(0, "")` without calling `updateRSETHPrice()` first.
3. `getRsETHAmountToMint()` computes `rsethAmountToMint = X * 1e18 / rsETHPrice` using the stale (understated) price, minting excess rsETH.
4. Call `LRTOracle.updateRSETHPrice()`. The new price reflects the diluted TVL.
5. The attacker's rsETH balance is now worth more than the deposited ETH; pre-existing holders' rsETH is worth less.

**Foundry fork test plan**: Fork mainnet at a block after a stETH rebase but before the next keeper `updateRSETHPrice()` tx. Record `rsETHPrice` and `_getTotalEthInProtocol() / rsethSupply`. Assert the gap. Call `depositETH` with a large value. Call `updateRSETHPrice`. Assert attacker's rsETH value in ETH exceeds deposit amount, and existing holders' ETH-denominated balance decreased.

### Citations

**File:** contracts/LRTOracle.sol (L28-28)
```text
    uint256 public override rsETHPrice;
```

**File:** contracts/LRTOracle.sol (L87-89)
```text
    function updateRSETHPrice() public whenNotPaused {
        _updateRsETHPrice();
    }
```

**File:** contracts/LRTOracle.sol (L249-250)
```text
        // compute new rsETH price based on total ETH minus fee
        uint256 newRsETHPrice = (totalETHInProtocol - protocolFeeInETH).divWad(rsethSupply);
```

**File:** contracts/LRTOracle.sol (L260-265)
```text
            if (isPriceIncreaseOffLimit) {
                // if sender has a manager role, this doesn't revert.
                // if not, it reverts as price went above the threshold
                if (!IAccessControl(address(lrtConfig)).hasRole(LRTConstants.MANAGER, msg.sender)) {
                    revert PriceAboveDailyThreshold();
                }
```

**File:** contracts/LRTDepositPool.sol (L519-520)
```text
        // calculate rseth amount to mint based on asset amount and asset exchange rate
        rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
```
