### Title
Stale Cross-Chain Rate Used for wrsETH Minting With No Staleness Guard - (File: contracts/cross-chain/CrossChainRateReceiver.sol)

### Summary
`CrossChainRateReceiver.getRate()` returns the last stored `rate` with no check against `lastUpdated`. This rate is the sole pricing input for wrsETH minting across all L2 pool contracts. Because the rate is only refreshed when an off-chain keeper pays LayerZero fees to call `updateRate()`, the on-chain value can silently lag the true rsETH/ETH exchange rate for an unbounded period. Since rsETH accrues staking rewards monotonically, a stale (lower) rate causes every depositor to receive more wrsETH than the ETH they supplied is worth, draining protocol yield.

### Finding Description
`CrossChainRateReceiver` stores the rsETH/ETH exchange rate in the state variable `rate` and records the update time in `lastUpdated`. The `getRate()` view function returns `rate` unconditionally:

```solidity
// contracts/cross-chain/CrossChainRateReceiver.sol L103-105
function getRate() external view returns (uint256) {
    return rate;
}
```

`lastUpdated` is written on every `lzReceive()` call but is never read inside `getRate()`. There is no maximum-age threshold, no revert on staleness, and no fallback.

Every L2 pool contract (`RSETHPoolV3ExternalBridge`, `RSETHPoolV3WithNativeChainBridge`, `RSETHPoolV2ExternalBridge`, `RSETHPoolNoWrapper`) resolves the minting rate through the same `IOracle(rsETHOracle).getRate()` call:

```solidity
// contracts/pools/RSETHPoolV3ExternalBridge.sol L422-426
uint256 rsETHToETHrate = getRate();
rsETHAmount = amountAfterFee * 1e18 / rsETHToETHrate;
```

Because rsETH/ETH is monotonically increasing (staking rewards accrue continuously), any stale snapshot is always lower than the true rate. Dividing by a lower rate yields a larger `rsETHAmount`, so every depositor receives excess wrsETH proportional to the staleness gap.

### Impact Explanation
A depositor calling `deposit()` on any L2 pool while the oracle is stale receives wrsETH whose face value (at the true rate) exceeds the ETH they supplied. When they later unwrap or bridge back, they extract more ETH than they deposited. The surplus comes from the pool's pre-minted rsETH reserve or from diluting other holders. Accumulated over many deposits during a prolonged stale window, this constitutes theft of unclaimed yield and, at sufficient scale, protocol insolvency.

**Impact: High — theft of unclaimed yield / protocol insolvency.**

### Likelihood Explanation
`updateRate()` on `RSETHMultiChainRateProvider` is a permissionless but fee-bearing call; the caller must supply native ETH to cover LayerZero messaging costs. During periods of high gas prices, network congestion, or keeper inactivity, updates can be delayed for hours. The `lastUpdated` field confirms the design anticipates gaps between updates, yet no on-chain guard enforces a maximum gap. Any depositor can observe `lastUpdated` off-chain and time a deposit to exploit the stale window.

**Likelihood: Medium.**

### Recommendation
Add a configurable `maxStaleness` parameter to `CrossChainRateReceiver` and revert in `getRate()` if `block.timestamp - lastUpdated > maxStaleness`:

```solidity
uint256 public maxStaleness; // e.g. 24 hours

function getRate() external view returns (uint256) {
    require(block.timestamp - lastUpdated <= maxStaleness, "Rate is stale");
    return rate;
}
```

This mirrors the approach taken in `ChainlinkOracleForRSETHPoolCollateral`, which already enforces `answeredInRound < roundID` and `timestamp == 0` guards for Chainlink feeds.

### Proof of Concept

1. At time T, `CrossChainRateReceiver.rate = 1.05e18` (rsETH/ETH) and `lastUpdated = T`.
2. Staking rewards accrue; the true rate rises to `1.06e18` by T+24h, but no keeper calls `updateRate()`.
3. At T+24h, Alice calls `deposit{value: 1 ether}()` on `RSETHPoolV3ExternalBridge`.
4. `viewSwapRsETHAmountAndFee(1e18)` calls `getRate()` → returns stale `1.05e18`.
5. `rsETHAmount = 1e18 * 1e18 / 1.05e18 ≈ 0.9524 wrsETH` (correct at true rate `1.06e18` would be `≈ 0.9434 wrsETH`).
6. Alice receives ~0.009 excess wrsETH (~0.95% of deposit) at the protocol's expense.
7. Alice redeems wrsETH at the true rate, extracting more ETH than she deposited. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

**File:** contracts/cross-chain/CrossChainRateReceiver.sol (L93-97)
```text
        uint256 _rate = abi.decode(_payload, (uint256));

        rate = _rate;

        lastUpdated = block.timestamp;
```

**File:** contracts/cross-chain/CrossChainRateReceiver.sol (L102-105)
```text
    /// @notice Gets the last stored rate in the contract
    function getRate() external view returns (uint256) {
        return rate;
    }
```

**File:** contracts/pools/RSETHPoolV3ExternalBridge.sol (L421-427)
```text

        // rate of rsETH in ETH
        uint256 rsETHToETHrate = getRate();

        // Calculate the final rsETH amount
        rsETHAmount = amountAfterFee * 1e18 / rsETHToETHrate;
    }
```

**File:** contracts/pools/RSETHPoolV3WithNativeChainBridge.sol (L339-344)
```text
        // rate of rsETH in ETH
        uint256 rsETHToETHrate = getRate();

        // Calculate the final rsETH amount
        rsETHAmount = amountAfterFee * 1e18 / rsETHToETHrate;
    }
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
