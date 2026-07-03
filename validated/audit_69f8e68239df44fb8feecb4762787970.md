Audit Report

## Title
Late Depositors Dilute Existing Holders' Yield via Stale `rsETHPrice` - (`contracts/LRTOracle.sol`, `contracts/LRTDepositPool.sol`)

## Summary

`LRTDepositPool.getRsETHAmountToMint()` prices new deposits using the stored `rsETHPrice`, which is only updated on explicit calls to `updateRSETHPrice()`. When rewards accrue between price updates, a depositor can mint rsETH at the stale (lower) price, then trigger a price update. Because `_updateRsETHPrice()` computes `previousTVL` using the **current** rsETH supply (which already includes the attacker's newly minted shares) multiplied by the **old** price, the attacker's principal is silently absorbed into the baseline, and the full reward amount is spread over a larger denominator — diluting existing holders.

## Finding Description

**Root cause — stale price used at mint time:**

`LRTDepositPool.getRsETHAmountToMint()` reads the stored `rsETHPrice` state variable directly:

```solidity
rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
``` [1](#0-0) 

Neither `depositETH()` nor `depositAsset()` calls `updateRSETHPrice()` before computing the mint amount. [2](#0-1) 

**Root cause — `previousTVL` uses post-deposit supply:**

Inside `_updateRsETHPrice()`, the baseline TVL is computed as:

```solidity
uint256 rsethSupply = IRSETH(rsETHTokenAddress).totalSupply();
// ...
uint256 previousTVL = rsethSupply.mulWad(rsETHPrice);
``` [3](#0-2) 

`rsethSupply` is the **live** total supply at the moment `updateRSETHPrice()` is called. If a deposit was made at the stale price before this call, those newly minted shares are already included in `rsethSupply`, so `previousTVL` is inflated by `newShares * oldPrice`. This makes `rewardAmount = totalETHInProtocol - previousTVL` appear identical to what it would have been without the deposit, while `newRsETHPrice = (totalETHInProtocol - fee) / rsethSupply` is computed over the larger denominator — permanently diluting existing holders. [4](#0-3) 

**`updateRSETHPrice()` is public and callable by anyone:** [5](#0-4) 

**Existing guards are insufficient:**

- The `minRSETHAmountExpected` slippage parameter in `depositETH`/`depositAsset` protects the depositor from receiving *too few* shares, but does not prevent them from receiving *too many* at a stale price.
- The `pricePercentageLimit` check at lines 252–266 can cause `updateRSETHPrice()` to revert for non-managers if the price jump is large, but the attacker's deposit at the stale price is already committed on-chain. When a manager eventually calls `updateRSETHPriceAsManager()`, the attacker still benefits. [6](#0-5) 

## Impact Explanation

**High — Theft of unclaimed yield.**

Concrete example (ignoring protocol fee):

| Step | rsethSupply | totalETH | rsETHPrice |
|---|---|---|---|
| Initial | 100 | 100 ETH | 1.00 |
| Rewards accrue | 100 | 110 ETH | 1.00 (stale) |
| Attacker deposits 10 ETH | 110 | 120 ETH | 1.00 (stale) |
| `updateRSETHPrice()` called | 110 | 120 ETH | 120/110 ≈ **1.0909** |

- Attacker's 10 rsETH → **10.909 ETH** (profit ≈ 0.909 ETH from rewards they did not earn).
- Original 100 rsETH holders → **109.09 ETH** instead of **110 ETH** (loss ≈ 0.91 ETH).
- Correct price without attacker: 110/100 = **1.10**.

This is a direct, quantifiable transfer of accrued yield from existing rsETH holders to the attacker, matching the allowed impact "Theft of unclaimed yield."

## Likelihood Explanation

The attack requires no special privileges. Any external account can:
1. Monitor on-chain state: compare `_getTotalEthInProtocol()` (reconstructible from public view functions) against `rsethSupply * rsETHPrice` to detect when rewards have accrued and the price is stale.
2. Call `depositETH()` or `depositAsset()` to mint rsETH at the stale price.
3. Call `updateRSETHPrice()` to lock in the dilution (or wait for the keeper).

The stale-price window exists continuously between keeper updates and is widened by MEV/execution-layer rewards flowing through `FeeReceiver.sendFunds()` → `receiveFromRewardReceiver()`, LST price appreciation, and EigenLayer pod share accrual. [7](#0-6) 

The attack is repeatable every reward cycle and scales with reward magnitude and deposit size.

## Recommendation

**Option A (preferred):** Call `_updateRsETHPrice()` at the start of `depositETH()` and `depositAsset()` (before `_beforeDeposit`) so every depositor pays the current fair price inclusive of all accrued rewards.

**Option B:** Snapshot `rsethSupply` at the time of the **last** price update (not the current supply) when computing `previousTVL`, so that shares minted at the stale price are not absorbed into the baseline. This prevents the attacker's deposit from inflating `previousTVL` and understating the reward amount.

## Proof of Concept

```
1. Deploy protocol; Alice deposits 100 ETH → 100 rsETH minted; rsETHPrice = 1.0.
2. 10 ETH of staking rewards flow into LRTDepositPool via FeeReceiver.sendFunds().
   totalETHInProtocol = 110 ETH; rsETHPrice still = 1.0 (not yet updated).
3. Attacker calls depositETH{value: 10 ether}(0, ""):
   getRsETHAmountToMint = 10e18 * 1e18 / 1e18 = 10 rsETH minted.
   rsethSupply = 110; totalETH = 120.
4. Attacker calls updateRSETHPrice():
   rsethSupply = 110 (includes attacker's 10 rsETH)
   previousTVL = 110 * 1.0 = 110 ETH
   rewardAmount = 120 - 110 = 10 ETH  ← same as without attacker
   newRsETHPrice = 120 / 110 ≈ 1.0909
5. Attacker holds 10 rsETH worth ≈ 10.909 ETH (profit ≈ 0.909 ETH).
6. Alice's 100 rsETH worth ≈ 109.09 ETH instead of 110 ETH (loss ≈ 0.91 ETH).

Foundry test plan:
- Deploy LRTConfig, LRTOracle, LRTDepositPool, mock rsETH, mock asset oracle.
- Mint 100 rsETH to Alice; set rsETHPrice = 1e18.
- Simulate reward accrual: send 10 ETH to LRTDepositPool.
- Assert getTotalAssetDeposits(ETH) = 110e18, rsETHPrice = 1e18.
- Attacker calls depositETH{value: 10e18}(0, ""); assert 10e18 rsETH minted.
- Attacker calls updateRSETHPrice(); assert rsETHPrice ≈ 1.0909e18.
- Assert attacker's 10 rsETH * newPrice > 10e18 (profit confirmed).
- Assert Alice's 100 rsETH * newPrice < 110e18 (loss confirmed).
- Compare to baseline (no attacker deposit): rsETHPrice = 1.10e18.
```

### Citations

**File:** contracts/LRTDepositPool.sol (L61-61)
```text
    function receiveFromRewardReceiver() external payable { }
```

**File:** contracts/LRTDepositPool.sol (L86-92)
```text
        // checks
        uint256 rsethAmountToMint = _beforeDeposit(LRTConstants.ETH_TOKEN, msg.value, minRSETHAmountExpected);

        // interactions
        _mintRsETH(rsethAmountToMint);

        emit ETHDeposit(msg.sender, msg.value, rsethAmountToMint, referralId);
```

**File:** contracts/LRTDepositPool.sol (L519-520)
```text
        // calculate rseth amount to mint based on asset amount and asset exchange rate
        rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
```

**File:** contracts/LRTOracle.sol (L87-89)
```text
    function updateRSETHPrice() public whenNotPaused {
        _updateRsETHPrice();
    }
```

**File:** contracts/LRTOracle.sol (L216-234)
```text
        uint256 rsethSupply = IRSETH(rsETHTokenAddress).totalSupply();

        if (rsethSupply == 0) {
            rsETHPrice = 1 ether;
            highestRsethPrice = 1 ether;
            return;
        }

        if (highestRsethPrice == 0) {
            highestRsethPrice = rsETHPrice;
        }

        uint256 previousPrice = rsETHPrice;

        // get total ETH in the protocol (normalized to 1e18)
        uint256 totalETHInProtocol = _getTotalEthInProtocol();

        // calculate previousTVL using rsethSupply multiplied by rsETHPrice
        uint256 previousTVL = rsethSupply.mulWad(rsETHPrice);
```

**File:** contracts/LRTOracle.sol (L244-250)
```text
        if (!protocolPaused && totalETHInProtocol > previousTVL) {
            uint256 rewardAmount = totalETHInProtocol - previousTVL;
            protocolFeeInETH = (rewardAmount * lrtConfig.protocolFeeInBPS()) / 10_000;
        }

        // compute new rsETH price based on total ETH minus fee
        uint256 newRsETHPrice = (totalETHInProtocol - protocolFeeInETH).divWad(rsethSupply);
```

**File:** contracts/LRTOracle.sol (L252-266)
```text
        if (newRsETHPrice > highestRsethPrice) {
            // check if the price is above the threshold
            uint256 priceDifference = newRsETHPrice - highestRsethPrice;
            // pricePercentageLimit is in 1e18 precision (100% = 1e18, 1% = 1e16)
            bool isPriceIncreaseOffLimit =
                pricePercentageLimit > 0 && priceDifference > pricePercentageLimit.mulWad(highestRsethPrice);

            // check if the price difference is above the threshold
            if (isPriceIncreaseOffLimit) {
                // if sender has a manager role, this doesn't revert.
                // if not, it reverts as price went above the threshold
                if (!IAccessControl(address(lrtConfig)).hasRole(LRTConstants.MANAGER, msg.sender)) {
                    revert PriceAboveDailyThreshold();
                }
            }
```
