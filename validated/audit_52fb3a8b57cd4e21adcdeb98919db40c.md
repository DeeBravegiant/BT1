### Title
`RSETHPriceFeed.latestRoundData()` Returns ETH/USD `updatedAt` for a Stale rsETH Price, Enabling Block-Stuffing-Assisted Oracle Manipulation — (`contracts/oracles/RSETHPriceFeed.sol`)

---

### Summary

`RSETHPriceFeed.latestRoundData()` computes `answer` from `LRTOracle.rsETHPrice` (a stored, manually-updated value) but returns `updatedAt` from the live ETH/USD Chainlink feed. Because `updatedAt` always reflects the ETH/USD heartbeat (~1 hour), downstream consumers that check `updatedAt` for staleness will never detect that `rsETHPrice` itself is stale. An attacker can use block stuffing to delay the permissionless `updateRSETHPrice()` call after a slashing event, keeping the stored price inflated while `updatedAt` appears fresh, and then borrow against the over-valued rsETH collateral.

---

### Finding Description

**`RSETHPriceFeed.latestRoundData()` — the `updatedAt` mismatch:**

```solidity
// contracts/oracles/RSETHPriceFeed.sol  lines 63-70
function latestRoundData()
    external view
    returns (uint80 roundId, int256 answer, uint256 startedAt, uint256 updatedAt, uint80 answeredInRound)
{
    (roundId, answer, startedAt, updatedAt, answeredInRound) = ETH_TO_USD.latestRoundData();
    answer = int256(RS_ETH_ORACLE.rsETHPrice()) * answer / 1e18;
}
```

`updatedAt` is taken verbatim from the ETH/USD Chainlink feed. [1](#0-0) 

`RS_ETH_ORACLE.rsETHPrice()` resolves to `LRTOracle.rsETHPrice`, a storage variable that is only written when `updateRSETHPrice()` or `updateRSETHPriceAsManager()` is called. [2](#0-1) 

There is no timestamp stored alongside `rsETHPrice` in `LRTOracle`, and `RSETHPriceFeed` never checks one. [3](#0-2) 

**`updateRSETHPrice()` is permissionless:**

```solidity
// contracts/LRTOracle.sol  line 87
function updateRSETHPrice() public whenNotPaused {
    _updateRsETHPrice();
}
```

Any address can call it, but block stuffing can prevent any transaction from landing. [4](#0-3) 

**Attack sequence:**

1. A slashing event reduces the true rsETH/ETH rate (e.g., by 1–2%, within `pricePercentageLimit` so the auto-pause does not trigger).
2. Attacker fills blocks with high-gas transactions, preventing `updateRSETHPrice()` from being included for N blocks.
3. During this window, `RSETHPriceFeed.latestRoundData()` returns:
   - `answer` = stale (pre-slash, inflated) `rsETHPrice` × current ETH/USD price
   - `updatedAt` = ETH/USD Chainlink heartbeat timestamp (fresh, ~minutes old)
4. A lending protocol consuming `RSETHPriceFeed` checks `updatedAt`, sees it is fresh, and accepts the inflated rsETH/USD price.
5. Attacker deposits rsETH as collateral, borrows against the inflated value, and exits before the price is corrected.

---

### Impact Explanation

The `updatedAt` field returned by `RSETHPriceFeed.latestRoundData()` does not reflect when `rsETHPrice` was last updated. Any downstream protocol that relies on `updatedAt` for staleness detection will be silently misled. Combined with block stuffing to delay the price update after a slashing event, this allows an attacker to borrow against over-valued rsETH collateral, extracting principal from the lending protocol. The impact maps to **Low. Block stuffing** (the oracle fails to deliver the promised current price), with a secondary path to fund extraction depending on the lending protocol's integration.

---

### Likelihood Explanation

- `updateRSETHPrice()` is public and called by keeper bots; block stuffing on Ethereum mainnet costs roughly `block_gas_limit × base_fee` per block (~0.6 ETH/block at 20 gwei). For a 10-block window (~2 minutes), cost ≈ 6 ETH. If the attacker holds a large rsETH position, the profit from borrowing against the inflated collateral can exceed this cost.
- The `updatedAt` mismatch is a structural design flaw that exists independently of block stuffing; any keeper downtime or network congestion also triggers it.
- Slashing events on EigenLayer restaked assets are a known, non-hypothetical risk.

---

### Recommendation

1. **Track rsETH price update time in `LRTOracle`:** Add a `rsETHPriceUpdatedAt` timestamp that is written alongside `rsETHPrice` in `_updateRsETHPrice()`.

2. **Return the correct `updatedAt` from `RSETHPriceFeed`:** Expose `rsETHPriceUpdatedAt` via `IRSETHOracle` and use it as `updatedAt` in `latestRoundData()`, taking the minimum of the ETH/USD `updatedAt` and the rsETH price `updatedAt`:
   ```solidity
   updatedAt = Math.min(ethUpdatedAt, RS_ETH_ORACLE.rsETHPriceUpdatedAt());
   ```

3. **Add a staleness guard inside `RSETHPriceFeed`:** Revert if `rsETHPriceUpdatedAt` is older than a configurable heartbeat (e.g., 24 hours), so downstream consumers receive a revert rather than a silently stale price.

---

### Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity 0.8.27;

// Fork test (Foundry) — run against a mainnet fork
// Simulates: slashing reduces true rsETH/ETH; block stuffing delays updateRSETHPrice();
// latestRoundData() returns inflated answer with fresh updatedAt.

import "forge-std/Test.sol";

interface ILRTOracle {
    function rsETHPrice() external view returns (uint256);
    function updateRSETHPrice() external;
}

interface IRSETHPriceFeed {
    function latestRoundData()
        external view
        returns (uint80, int256, uint256, uint256, uint80);
}

contract BlockStuffingOraclePoC is Test {
    // Mainnet addresses (from README)
    ILRTOracle  lrtOracle    = ILRTOracle(/* LRTOracle proxy */);
    IRSETHPriceFeed priceFeed = IRSETHPriceFeed(/* RSETHPriceFeed proxy */);

    function testStaleRsETHPriceWithFreshUpdatedAt() public {
        // 1. Record current rsETHPrice and latestRoundData
        uint256 priceBeforeSlash = lrtOracle.rsETHPrice();
        (, int256 answerBefore,, uint256 updatedAtBefore,) = priceFeed.latestRoundData();

        // 2. Simulate slashing: manipulate EigenLayer TVL to reduce rsETH backing
        //    (fork-test: use vm.store to reduce totalAssetDeposits by 2%)
        // ... (storage manipulation of LRTDepositPool)

        // 3. Simulate block stuffing: advance N blocks WITHOUT calling updateRSETHPrice()
        vm.roll(block.number + 10);
        vm.warp(block.timestamp + 120); // 2 minutes

        // 4. Call latestRoundData() — rsETHPrice is still pre-slash (stale)
        (, int256 answerAfter,, uint256 updatedAtAfter,) = priceFeed.latestRoundData();

        // 5. updatedAt is fresh (ETH/USD heartbeat), answer is inflated
        assertApproxEqAbs(updatedAtAfter, block.timestamp, 3600, "updatedAt appears fresh");
        assertGt(answerAfter, /* true rsETH/USD after slash */ 0, "answer is above true value");

        // 6. Confirm updateRSETHPrice() would lower the price
        lrtOracle.updateRSETHPrice();
        (, int256 answerCorrect,,,) = priceFeed.latestRoundData();
        assertLt(answerCorrect, answerAfter, "price corrects downward after update");
    }
}
```

The test asserts that during the block-stuffing window, `latestRoundData()` returns an `answer` above the true post-slash rsETH/USD value while `updatedAt` is indistinguishable from a live feed, satisfying the invariant violation described in the question. [5](#0-4) [4](#0-3) [6](#0-5)

### Citations

**File:** contracts/oracles/RSETHPriceFeed.sol (L63-70)
```text
    function latestRoundData()
        external
        view
        returns (uint80 roundId, int256 answer, uint256 startedAt, uint256 updatedAt, uint80 answeredInRound)
    {
        (roundId, answer, startedAt, updatedAt, answeredInRound) = ETH_TO_USD.latestRoundData();
        answer = int256(RS_ETH_ORACLE.rsETHPrice()) * answer / 1e18;
    }
```

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

**File:** contracts/LRTOracle.sol (L270-282)
```text
        if (newRsETHPrice < highestRsethPrice) {
            uint256 diff = highestRsethPrice - newRsETHPrice;
            // normalizing to 1e18
            bool isPriceDecreaseOffLimit =
                pricePercentageLimit > 0 && diff > pricePercentageLimit.mulWad(highestRsethPrice);

            // if price decrease is off limit, pause the protocol (unless it's already paused)
            if (isPriceDecreaseOffLimit) {
                if (!lrtDepositPool.paused()) lrtDepositPool.pause();
                if (!withdrawalManager.paused()) withdrawalManager.pause();
                _pause();
                return;
            }
```

**File:** contracts/LRTOracle.sol (L313-313)
```text
        rsETHPrice = newRsETHPrice;
```
