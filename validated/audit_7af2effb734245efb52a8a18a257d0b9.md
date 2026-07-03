Audit Report

## Title
Stale Cross-Chain Rate in `CrossChainRateReceiver.getRate()` Enables Depositors to Mint Excess rsETH at Existing Holders' Expense — (File: `contracts/cross-chain/CrossChainRateReceiver.sol`)

## Summary
`CrossChainRateReceiver.getRate()` returns the stored `rate` unconditionally, with no check against the `lastUpdated` timestamp. All three L2 pool contracts use this rate to compute rsETH minting amounts. Because the L1 rsETH price increases monotonically as restaking yield accrues, a stale (lower) L2 rate causes depositors to receive more rsETH than their ETH contribution justifies, diluting the yield entitlement of all existing rsETH holders.

## Finding Description
`CrossChainRateReceiver` stores both `rate` and `lastUpdated` on every `lzReceive` call, but `getRate()` ignores `lastUpdated` entirely:

```solidity
// CrossChainRateReceiver.sol L95-105
rate = _rate;
lastUpdated = block.timestamp;
...
function getRate() external view returns (uint256) {
    return rate;
}
``` [1](#0-0) 

All three L2 pool contracts call `IOracle(rsETHOracle).getRate()` and divide by it to compute the rsETH mint amount:

- `RSETHPoolV3.viewSwapRsETHAmountAndFee()`: `rsETHAmount = amountAfterFee * 1e18 / rsETHToETHrate` [2](#0-1) 
- `RSETHPoolNoWrapper.viewSwapRsETHAmountAndFee()`: same formula [3](#0-2) 
- `RSETHPoolV3ExternalBridge.viewSwapRsETHAmountAndFee()`: same formula [4](#0-3) 

The L1 rate is computed as `(totalETHInProtocol - protocolFeeInETH).divWad(rsethSupply)` and increases monotonically as restaking rewards accumulate. [5](#0-4) 

The rate is pushed to L2 via `MultiChainRateProvider.updateRate()`, which is permissionless — anyone may call it, but no one is required to. [6](#0-5) 

When the L2 rate lags the L1 rate, the division `amountAfterFee * 1e18 / staleRate` yields a larger rsETH amount than the depositor's ETH is worth at the true current rate. The excess rsETH is minted against no additional ETH backing, reducing the per-token ETH value for all existing holders once the rate is corrected.

By contrast, `ChainlinkOracleForRSETHPoolCollateral.getRate()` — used for collateral token oracles in the same pool contracts — explicitly reverts on stale data, confirming the protocol is aware of the staleness risk pattern but did not apply it to the cross-chain rate. [7](#0-6) 

## Impact Explanation
**High — Theft of unclaimed yield.**

rsETH is a yield-bearing token whose ETH/rsETH exchange rate rises over time. Existing holders' accrued yield is expressed through this rising rate. When a depositor mints rsETH at a stale (lower) rate, they receive more rsETH than their ETH contribution justifies at the true current rate. The excess rsETH is unbacked, so when the rate is subsequently updated, the protocol's per-token ETH value is reduced for all existing holders. The magnitude scales with the size of the stale window and the deposit size, making systematic extraction feasible.

## Likelihood Explanation
**Medium.** Rate updates depend entirely on off-chain infrastructure calling `updateRate()` and paying LayerZero fees. There is no on-chain enforcement of update frequency. Network congestion, keeper downtime, or deliberate inaction can all create stale windows. Because `updateRate()` is permissionless and `lastUpdated` is publicly readable on-chain, a sophisticated attacker can trivially detect when the rate is stale and deposit large amounts during that window without needing any special privileges. [8](#0-7) 

## Recommendation
Add a configurable `maxStaleness` parameter to `CrossChainRateReceiver` and revert in `getRate()` if the rate is stale:

```solidity
function getRate() external view returns (uint256) {
    require(block.timestamp - lastUpdated <= maxStaleness, "Rate is stale");
    return rate;
}
```

This mirrors the staleness check pattern already used in `ChainlinkOracleForRSETHPoolCollateral`. [9](#0-8) 

Additionally, consider adding a `minRSETHAmountExpected` slippage parameter to L2 pool `deposit()` functions, analogous to `LRTDepositPool.depositETH()`, so users can protect themselves from receiving fewer tokens than expected if the rate is updated mid-block. [10](#0-9) 

## Proof of Concept
1. L1 rsETH rate is `1.05e18` (5% yield accrued). L2 `CrossChainRateReceiver.rate` is `1.00e18` (stale — not updated for several days). `lastUpdated` is publicly readable and confirms the staleness.
2. Attacker calls `RSETHPoolV3.deposit{value: 100 ether}("")` on L2.
3. `viewSwapRsETHAmountAndFee(100 ether)` computes (assuming `feeBps = 0`): `rsETHAmount = 100e18 * 1e18 / 1.00e18 = 100 rsETH`.
4. At the true L1 rate of `1.05e18`, the attacker should have received `100e18 * 1e18 / 1.05e18 ≈ 95.24 rsETH`.
5. The attacker receives `100 rsETH` — an excess of `~4.76 rsETH` minted against no additional ETH backing.
6. When `updateRate()` is subsequently called and the L2 rate is corrected to `1.05e18`, the total rsETH supply is inflated relative to TVL, reducing the per-token ETH value for all existing holders.

**Foundry fork test plan**: Fork an L2 where the pool is deployed. Warp `block.timestamp` forward by several days without calling `updateRate()`. Confirm `CrossChainRateReceiver.lastUpdated` is stale. Call `deposit{value: 100 ether}("")` and assert the minted rsETH amount exceeds `100e18 * 1e18 / trueL1Rate`. Then call `updateRate()` and assert the per-token ETH value (TVL / totalSupply) has decreased relative to pre-deposit. [11](#0-10)

### Citations

**File:** contracts/cross-chain/CrossChainRateReceiver.sol (L13-16)
```text
    uint256 public rate;

    /// @notice Last time rate was updated
    uint256 public lastUpdated;
```

**File:** contracts/cross-chain/CrossChainRateReceiver.sol (L95-105)
```text
        rate = _rate;

        lastUpdated = block.timestamp;

        emit RateUpdated(_rate);
    }

    /// @notice Gets the last stored rate in the contract
    function getRate() external view returns (uint256) {
        return rate;
    }
```

**File:** contracts/pools/RSETHPoolV3.sol (L246-265)
```text
    function deposit(string memory referralId)
        external
        payable
        nonReentrant
        whenNotPaused
        limitDailyMint(msg.value, ETH_IDENTIFIER)
    {
        if (!isEthDepositEnabled) revert EthDepositDisabled();
        uint256 amount = msg.value;

        if (amount == 0) revert InvalidAmount();

        (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount);

        feeEarnedInETH += fee;

        wrsETH.mint(msg.sender, rsETHAmount);

        emit SwapOccurred(msg.sender, rsETHAmount, fee, referralId);
    }
```

**File:** contracts/pools/RSETHPoolV3.sol (L299-308)
```text
    function viewSwapRsETHAmountAndFee(uint256 amount) public view returns (uint256 rsETHAmount, uint256 fee) {
        fee = amount * feeBps / 10_000;
        uint256 amountAfterFee = amount - fee;

        // rate of rsETH in ETH
        uint256 rsETHToETHrate = getRate();

        // Calculate the final rsETH amount
        rsETHAmount = amountAfterFee * 1e18 / rsETHToETHrate;
    }
```

**File:** contracts/pools/RSETHPoolNoWrapper.sol (L277-286)
```text
    function viewSwapRsETHAmountAndFee(uint256 amount) public view returns (uint256 rsETHAmount, uint256 fee) {
        fee = amount * feeBps / 10_000;
        uint256 amountAfterFee = amount - fee;

        // rate of rsETH in ETH
        uint256 rsETHToETHrate = getRate();

        // Calculate the final rsETH amount
        rsETHAmount = amountAfterFee * 1e18 / rsETHToETHrate;
    }
```

**File:** contracts/pools/RSETHPoolV3ExternalBridge.sol (L418-427)
```text
    function viewSwapRsETHAmountAndFee(uint256 amount) public view returns (uint256 rsETHAmount, uint256 fee) {
        fee = amount * feeBps / 10_000;
        uint256 amountAfterFee = amount - fee;

        // rate of rsETH in ETH
        uint256 rsETHToETHrate = getRate();

        // Calculate the final rsETH amount
        rsETHAmount = amountAfterFee * 1e18 / rsETHToETHrate;
    }
```

**File:** contracts/LRTOracle.sol (L250-250)
```text
        uint256 newRsETHPrice = (totalETHInProtocol - protocolFeeInETH).divWad(rsethSupply);
```

**File:** contracts/cross-chain/MultiChainRateProvider.sol (L108-113)
```text
    function updateRate() external payable nonReentrant {
        uint256 latestRate = getLatestRate();

        rate = latestRate;

        lastUpdated = block.timestamp;
```

**File:** contracts/pools/oracle/ChainlinkOracleForRSETHPoolCollateral.sol (L26-37)
```text
    function getRate() public view returns (uint256) {
        (uint80 roundID, int256 ethPrice,, uint256 timestamp, uint80 answeredInRound) =
            AggregatorV3Interface(oracle).latestRoundData();

        if (answeredInRound < roundID) revert StalePrice();
        if (timestamp == 0) revert IncompleteRound();
        if (ethPrice <= 0) revert InvalidPrice();

        uint256 normalizedPrice = uint256(ethPrice) * 1e18 / 10 ** uint256(AggregatorV3Interface(oracle).decimals());

        return normalizedPrice;
    }
```
