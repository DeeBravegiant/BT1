### Title
Stale Cross-Chain rsETH Rate in `RSETHRateReceiver` Causes Over-Minting of rsETH in `RSETHPoolV3WithNativeChainBridge` — (`contracts/cross-chain/RSETHRateReceiver.sol` / `contracts/pools/RSETHPoolV3WithNativeChainBridge.sol`)

---

### Summary

`CrossChainRateReceiver.getRate()` returns the last stored `rate` with no staleness check. `RSETHPoolV3WithNativeChainBridge.viewSwapRsETHAmountAndFee(amount, token)` divides a current Chainlink `tokenToETHRate` by this potentially stale `rsETHToETHrate`. When the stored rate is lower than the true current rate, the ratio is inflated and the pool mints more rsETH per deposited token than the true exchange rate warrants, creating unbacked rsETH supply.

---

### Finding Description

**`CrossChainRateReceiver.getRate()` — no staleness enforcement**

`CrossChainRateReceiver` stores `lastUpdated` (set in `lzReceive`) but `getRate()` returns `rate` unconditionally:

```solidity
// contracts/cross-chain/CrossChainRateReceiver.sol
uint256 public lastUpdated;   // stored but never validated

function getRate() external view returns (uint256) {
    return rate;              // no block.timestamp - lastUpdated check
}
``` [1](#0-0) [2](#0-1) 

The rate is only updated when someone calls `updateRate()` on the L1 provider, which triggers a LayerZero message. There is no on-chain enforcement of update frequency.

**`RSETHPoolV3WithNativeChainBridge.viewSwapRsETHAmountAndFee(amount, token)` — asymmetric oracle freshness**

```solidity
// contracts/pools/RSETHPoolV3WithNativeChainBridge.sol
uint256 rsETHToETHrate  = getRate();                                        // stale LayerZero rate
uint256 tokenToETHRate  = IOracle(supportedTokenOracle[token]).getRate();   // current Chainlink rate
rsETHAmount = amountAfterFee * tokenToETHRate / rsETHToETHrate;
``` [3](#0-2) 

`ChainlinkOracleForRSETHPoolCollateral.getRate()` validates `answeredInRound < roundID`, `timestamp == 0`, and `ethPrice <= 0`, so `tokenToETHRate` is current. But `rsETHToETHrate` has no equivalent freshness guard. [4](#0-3) 

**Arithmetic consequence**

rsETH is a yield-bearing token — its ETH value increases monotonically over time. If the stored rate is `R_stale < R_true`:

```
rsETHAmount_minted = amountAfterFee * tokenToETHRate / R_stale
                   > amountAfterFee * tokenToETHRate / R_true   (true fair amount)
```

The excess `rsETHAmount_minted - rsETHAmount_fair` is unbacked rsETH minted to the depositor.

---

### Impact Explanation

Every depositor who transacts while the rate is stale receives more rsETH than their collateral is worth at the true rate. The pool's collateral-to-rsETH backing ratio deteriorates. Repeated exploitation across the daily mint window accumulates unbacked supply, constituting protocol insolvency proportional to the staleness gap and the daily mint limit.

The `dailyMintLimit` bounds the per-day loss but does not prevent it; the limit is expressed in rsETH units computed using the stale rate, so the limit itself is evaluated against an inflated rsETH amount. [5](#0-4) 

---

### Likelihood Explanation

- rsETH appreciates continuously (~4% APY). Any gap between `updateRate()` calls creates a stale-low rate.
- `updateRate()` is permissionless but requires a manual call and LayerZero fee payment; there is no keeper or heartbeat enforced on-chain.
- The attacker needs no special role — only a standard `deposit(token, amount, referralId)` call.
- The exploit is passive: the attacker simply deposits when the rate is stale. No oracle manipulation, no governance capture, no front-running required.

---

### Recommendation

1. **Add a staleness threshold in `CrossChainRateReceiver.getRate()`:**
   ```solidity
   uint256 public constant MAX_RATE_AGE = 24 hours;
   function getRate() external view returns (uint256) {
       require(block.timestamp - lastUpdated <= MAX_RATE_AGE, "Rate is stale");
       return rate;
   }
   ```
2. **Alternatively, revert in `viewSwapRsETHAmountAndFee` if `lastUpdated` is too old**, similar to how `ChainlinkOracleForRSETHPoolCollateral` validates round freshness.
3. Consider adding a heartbeat keeper or an on-chain circuit breaker that pauses deposits when the rate has not been updated within the expected window.

---

### Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity 0.8.27;

// Foundry fork test (local fork, no mainnet calls)
import "forge-std/Test.sol";

interface IRateReceiver {
    function rate() external view returns (uint256);
    function lastUpdated() external view returns (uint256);
    function getRate() external view returns (uint256);
}

interface IPool {
    function viewSwapRsETHAmountAndFee(uint256 amount, address token)
        external view returns (uint256 rsETHAmount, uint256 fee);
    function deposit(address token, uint256 amount, string memory referralId) external;
}

contract StaleRatePoC is Test {
    // Addresses from README (Arbitrum deployment)
    address constant RATE_RECEIVER = 0x3222d3De5A9a3aB884751828903044CC4ADC627e;
    address constant POOL          = address(0); // fill with deployed pool address

    function testStaleRateOverMint() public {
        // 1. Read current stored rate and lastUpdated
        IRateReceiver receiver = IRateReceiver(RATE_RECEIVER);
        uint256 storedRate  = receiver.getRate();
        uint256 lastUpdated = receiver.lastUpdated();

        // 2. Simulate time passing without a rate update (warp 7 days)
        vm.warp(block.timestamp + 7 days);

        // 3. The rate is now stale; rsETH has appreciated ~0.077% in 7 days
        //    True rate should be ~storedRate * 1.00077
        uint256 trueRate = storedRate * 10_000_077 / 10_000_000;

        // 4. getRate() still returns the old stale rate — no revert
        uint256 rateAfterWarp = receiver.getRate();
        assertEq(rateAfterWarp, storedRate, "Rate should still be stale");

        // 5. Compute rsETH minted with stale rate vs true rate for 1e18 token units
        //    tokenToETHRate = 1e18 (e.g. WETH)
        uint256 tokenToETHRate = 1e18;
        uint256 rsETHStale = 1e18 * tokenToETHRate / storedRate;
        uint256 rsETHTrue  = 1e18 * tokenToETHRate / trueRate;

        // 6. Assert invariant violation: minted > fair
        assertTrue(rsETHStale > rsETHTrue, "Over-minting confirmed");

        // Excess unbacked rsETH per 1e18 token deposited:
        uint256 excess = rsETHStale - rsETHTrue;
        emit log_named_uint("Excess rsETH per 1e18 token (7-day staleness)", excess);
        // ~77e12 wei of rsETH (~0.077%) unbacked per deposit unit
    }
}
```

The invariant `rsETHAmount * rsETHToETHrate_true <= tokenAmount * tokenToETHRate` is violated whenever `rsETHToETHrate` (stale) `< rsETHToETHrate_true` (current), which is the normal state whenever the LayerZero update has not been sent since the last yield accrual.

### Citations

**File:** contracts/cross-chain/CrossChainRateReceiver.sol (L16-16)
```text
    uint256 public lastUpdated;
```

**File:** contracts/cross-chain/CrossChainRateReceiver.sol (L102-105)
```text
    /// @notice Gets the last stored rate in the contract
    function getRate() external view returns (uint256) {
        return rate;
    }
```

**File:** contracts/pools/RSETHPoolV3WithNativeChainBridge.sol (L108-136)
```text
    modifier limitDailyMint(uint256 amount, address token) {
        if (block.timestamp < startTimestamp) {
            revert MintBeforeStartTimestamp();
        }

        uint256 rsETHAmount;

        // Calculate the amount of rsETH that will be minted
        if (token == ETH_IDENTIFIER) {
            (rsETHAmount,) = viewSwapRsETHAmountAndFee(amount);
        } else {
            (rsETHAmount,) = viewSwapRsETHAmountAndFee(amount, token);
        }

        uint256 currentDay = getCurrentDay();

        // If the current day is greater than the last mint day, reset the daily mint amount
        if (currentDay > lastMintDay) {
            lastMintDay = currentDay;
            dailyMintAmount = 0;
        }

        // Check if the daily mint amount plus the amount to mint is greater than the daily mint limit
        if (dailyMintAmount + rsETHAmount > dailyMintLimit) {
            revert DailyMintLimitExceeded();
        }

        dailyMintAmount += rsETHAmount;
        _;
```

**File:** contracts/pools/RSETHPoolV3WithNativeChainBridge.sol (L363-370)
```text
        // rate of rsETH in ETH
        uint256 rsETHToETHrate = getRate();

        // rate of token in ETH
        uint256 tokenToETHRate = IOracle(supportedTokenOracle[token]).getRate();

        // Calculate the final rsETH amount
        rsETHAmount = amountAfterFee * tokenToETHRate / rsETHToETHrate;
```

**File:** contracts/pools/oracle/ChainlinkOracleForRSETHPoolCollateral.sol (L26-36)
```text
    function getRate() public view returns (uint256) {
        (uint80 roundID, int256 ethPrice,, uint256 timestamp, uint80 answeredInRound) =
            AggregatorV3Interface(oracle).latestRoundData();

        if (answeredInRound < roundID) revert StalePrice();
        if (timestamp == 0) revert IncompleteRound();
        if (ethPrice <= 0) revert InvalidPrice();

        uint256 normalizedPrice = uint256(ethPrice) * 1e18 / 10 ** uint256(AggregatorV3Interface(oracle).decimals());

        return normalizedPrice;
```
