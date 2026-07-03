Audit Report

## Title
Stale `rsETHPrice` in `getRsETHAmountToMint` Allows Depositors to Capture Accrued Yield from Existing Holders - (`contracts/LRTDepositPool.sol`)

## Summary

`LRTOracle` stores the rsETH/ETH exchange rate in a public state variable `rsETHPrice` that is only updated when `updateRSETHPrice()` is explicitly called by an off-chain keeper. `LRTDepositPool.getRsETHAmountToMint()` divides by this cached value without first refreshing it. When staking rewards have accrued since the last price update, the stale (lower) denominator causes depositors to receive more rsETH than they are entitled to, diluting the yield that belongs to existing holders.

## Finding Description

`LRTOracle` stores the exchange rate as a plain storage variable: [1](#0-0) 

This variable is only written inside `_updateRsETHPrice()`, which is only reachable via explicit calls to `updateRSETHPrice()` or `updateRSETHPriceAsManager()`: [2](#0-1) [3](#0-2) 

The deposit path is `depositETH()` → `_beforeDeposit()` → `getRsETHAmountToMint()`. None of these steps call `updateRSETHPrice()`: [4](#0-3) [5](#0-4) 

The mint amount is computed as `(amount * assetPrice) / lrtOracle.rsETHPrice()`, using the stale cached value: [6](#0-5) 

The true current price is computed inside `_updateRsETHPrice()` by summing all TVL across NodeDelegators (including `getEffectivePodShares()` for ETH staked in EigenLayer pods) and dividing by total rsETH supply: [7](#0-6) 

The protocol's own fee logic confirms that rewards are expected to accrue between price updates — it explicitly computes `rewardAmount = totalETHInProtocol - previousTVL` and takes a protocol fee on it: [8](#0-7) 

This confirms the design assumption that TVL grows between keeper calls. Any deposit in that window uses a denominator that is lower than the true price, minting excess rsETH.

**Why existing guards are insufficient:**

- `minRSETHAmountExpected` in `depositETH()` is a depositor-side slippage guard; it protects the depositor from receiving *less* than expected, not from receiving *more* than they are entitled to.
- `pricePercentageLimit` only throttles how much the price can jump in a single `updateRSETHPrice()` call; it does not prevent the stale-price window from being exploited during deposits.

## Impact Explanation

This is **theft of unclaimed yield** (High severity). When `rsETHPrice` is stale at `1.00 ETH/rsETH` but the true price is `1.01 ETH/rsETH` (after rewards), a depositor of 100 ETH receives `100 / 1.00 = 100 rsETH` instead of the correct `100 / 1.01 ≈ 99.01 rsETH`. The excess `~0.99 rsETH` represents a claim on TVL that was earned by prior holders. After the next `updateRSETHPrice()` call, the new price reflects the diluted supply, and existing holders permanently receive less yield than they earned. The attacker retains the excess rsETH indefinitely.

## Likelihood Explanation

`updateRSETHPrice()` is called by an off-chain keeper on a periodic schedule (confirmed by the daily fee minting period logic and `pricePercentageLimit` design). Every deposit between reward accrual and the next keeper call exploits this. A sophisticated attacker can monitor on-chain TVL via `_getTotalEthInProtocol()` (computable from public state: `getTotalAssetDeposits()` and asset oracle prices) versus `rsETHPrice * totalSupply`, and time large deposits to maximize the discrepancy. No special privileges are required — any address can call `depositETH()` or `depositAsset()`.

## Recommendation

At the start of `depositETH()` and `depositAsset()`, call `_updateRsETHPrice()` (making it `internal` rather than `private`) before computing the mint amount. This ensures the denominator in `getRsETHAmountToMint` always reflects the current TVL. Alternatively, compute the mint amount using a freshly calculated price derived directly from `_getTotalEthInProtocol() / totalSupply` rather than reading the cached `rsETHPrice` storage variable.

## Proof of Concept

1. Protocol state: 1000 ETH TVL, 1000 rsETH supply → `rsETHPrice = 1.0 ETH/rsETH`.
2. EigenLayer pod shares increase by 10 ETH (staking rewards) → true TVL = 1010 ETH, true price = `1.01 ETH/rsETH`.
3. Keeper has not yet called `updateRSETHPrice()`; `rsETHPrice` in storage remains `1.0e18`.
4. Attacker calls `depositETH{value: 100 ether}(0, "")`.
5. `getRsETHAmountToMint` computes: `(100e18 * 1e18) / 1e18 = 100e18` rsETH minted.
6. Correct amount at true price: `100e18 * 1e18 / 1.01e18 ≈ 99.0099e18` rsETH.
7. Attacker receives `≈ 0.99 rsETH` excess — a direct claim on the 10 ETH reward pool belonging to existing holders.
8. Keeper calls `updateRSETHPrice()`: new supply = 1100 rsETH, new TVL = 1110 ETH, new price = `1110/1100 ≈ 1.009 ETH/rsETH` instead of the `1.01 ETH/rsETH` existing holders were entitled to. The yield dilution is permanent.

**Foundry fork test plan:** Fork mainnet, snapshot a block where `_getTotalEthInProtocol()` > `rsETHPrice * rsETH.totalSupply()`. Call `depositETH` with a large value. Assert that the minted rsETH exceeds `depositAmount / truePrice`. Then call `updateRSETHPrice()` and assert that the resulting `rsETHPrice` is lower than it would have been without the intervening deposit.

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

**File:** contracts/LRTOracle.sol (L231-250)
```text
        uint256 totalETHInProtocol = _getTotalEthInProtocol();

        // calculate previousTVL using rsethSupply multiplied by rsETHPrice
        uint256 previousTVL = rsethSupply.mulWad(rsETHPrice);

        IPausable lrtDepositPool = IPausable(lrtConfig.getContract(LRTConstants.LRT_DEPOSIT_POOL));
        IPausable withdrawalManager = IPausable(lrtConfig.getContract(LRTConstants.LRT_WITHDRAW_MANAGER));

        // determine if the protocol is active (not paused)
        bool protocolPaused = lrtDepositPool.paused() || withdrawalManager.paused() || paused;

        // only take fee if TVL increased and protocol is not paused
        uint256 protocolFeeInETH = 0;
        if (!protocolPaused && totalETHInProtocol > previousTVL) {
            uint256 rewardAmount = totalETHInProtocol - previousTVL;
            protocolFeeInETH = (rewardAmount * lrtConfig.protocolFeeInBPS()) / 10_000;
        }

        // compute new rsETH price based on total ETH minus fee
        uint256 newRsETHPrice = (totalETHInProtocol - protocolFeeInETH).divWad(rsethSupply);
```

**File:** contracts/LRTOracle.sol (L313-313)
```text
        rsETHPrice = newRsETHPrice;
```

**File:** contracts/LRTDepositPool.sol (L86-88)
```text
        // checks
        uint256 rsethAmountToMint = _beforeDeposit(LRTConstants.ETH_TOKEN, msg.value, minRSETHAmountExpected);

```

**File:** contracts/LRTDepositPool.sol (L519-521)
```text
        // calculate rseth amount to mint based on asset amount and asset exchange rate
        rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
    }
```

**File:** contracts/LRTDepositPool.sol (L648-665)
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
```
