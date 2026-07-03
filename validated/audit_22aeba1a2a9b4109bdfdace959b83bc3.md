Audit Report

## Title
`LRTOracle._updateRsETHPrice()` Writes Arbitrarily Low `rsETHPrice` When `pricePercentageLimit` Is Zero (Default) — (File: `contracts/LRTOracle.sol`)

## Summary

`pricePercentageLimit` is declared as a plain `uint256` storage variable that defaults to `0` and is never initialized in `initialize()`. The sole downside-protection guard in `_updateRsETHPrice()` is short-circuited by `pricePercentageLimit > 0`, meaning it is permanently disabled in the default deployment state. Any temporary TVL underreport — e.g., a supported-asset oracle returning a depressed price — allows any caller to crystallize a near-zero `rsETHPrice` into storage, after which a depositor can mint a disproportionate rsETH share that dilutes all existing holders once the price recovers.

## Finding Description

`pricePercentageLimit` is declared at line 29 and is never assigned in `initialize()` (lines 64–68), so it is `0` on every fresh deployment. [1](#0-0) [2](#0-1) 

The only downside guard in `_updateRsETHPrice()` is:

```solidity
bool isPriceDecreaseOffLimit =
    pricePercentageLimit > 0 && diff > pricePercentageLimit.mulWad(highestRsethPrice);
``` [3](#0-2) 

When `pricePercentageLimit == 0` the left operand of `&&` is `false`, so `isPriceDecreaseOffLimit` is always `false` regardless of how far the price has fallen. Execution falls through and `rsETHPrice = newRsETHPrice` is unconditionally written. [4](#0-3) 

The entry point is the **public, permissionless** `updateRSETHPrice()`: [5](#0-4) 

The stored `rsETHPrice` is then used as the denominator for every rsETH mint:

```solidity
rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
``` [6](#0-5) 

**Exploit flow (oracle-driven temporary TVL underreport):**

1. A supported-asset oracle temporarily returns a depressed price (e.g., a stale feed, a brief oracle manipulation, or a known LST de-peg event).
2. `_getTotalEthInProtocol()` sums asset balances × oracle prices, so the reported TVL is artificially low.
3. Any caller invokes `updateRSETHPrice()`. Because `pricePercentageLimit == 0`, no pause is triggered and `rsETHPrice` is written to the depressed value (e.g., `0.1e18` instead of `1.05e18`).
4. An attacker calls `depositETH{value: 1 ether}(0, "")`. With `rsETHPrice = 0.1e18`, `rsethAmountToMint = 1e18 / 0.1e18 = 10 rsETH`.
5. The oracle recovers. `updateRSETHPrice()` is called again; the new price reflects the true TVL divided by the now-inflated rsETH supply. The attacker's 10 rsETH represents a claim on far more ETH than they deposited, at the direct expense of pre-existing holders.

The `updatePriceOracleForValidated` path does enforce a price sanity range (1e16–1e19), but this only applies to oracle registration, not to the live price-update path. [7](#0-6) 

## Impact Explanation

**Low — Contract fails to deliver promised returns to existing rsETH holders.**

Existing rsETH holders suffer dilution: their proportional claim on the protocol's underlying ETH is reduced by the attacker's inflated mint. No funds are directly stolen in a single atomic transaction, but the attacker's rsETH balance represents a claim on assets they did not contribute, extracted from existing holders when the price recovers. This maps exactly to the allowed Low impact class: "Contract fails to deliver promised returns, but doesn't lose value."

## Likelihood Explanation

`pricePercentageLimit` is `0` in every deployment until an admin explicitly calls `setPricePercentageLimit()`. The trigger (a supported-asset oracle returning a temporarily low price) is a realistic, recurring event for LST-backed protocols — stale Chainlink rounds, brief de-peg events, and oracle upgrades all qualify. `updateRSETHPrice()` is public, so no privileged actor is needed to crystallize the low price. Likelihood is **Low-to-Medium**: the default misconfiguration is universal, but the oracle-depression window must coincide with an attacker deposit.

## Recommendation

1. **Set a non-zero default for `pricePercentageLimit` in `initialize()`** so downside protection is active from deployment without a separate admin transaction.
2. **Add an absolute minimum price floor inside `_updateRsETHPrice()`** before writing `rsETHPrice`:

```solidity
uint256 constant MIN_RSETH_PRICE = 0.5 ether; // protocol-defined floor

if (newRsETHPrice < MIN_RSETH_PRICE) {
    if (!lrtDepositPool.paused()) lrtDepositPool.pause();
    if (!withdrawalManager.paused()) withdrawalManager.pause();
    _pause();
    return;
}
```

This ensures that even with `pricePercentageLimit == 0`, a catastrophic price drop cannot be written to storage.

## Proof of Concept

**Minimal call sequence (no fork required):**

1. Deploy protocol. Confirm `pricePercentageLimit == 0` (storage default, never set in `initialize()`).
2. Simulate a supported-asset oracle returning `0.095e18` instead of `1.0e18` for stETH.
3. Call `LRTOracle.updateRSETHPrice()` from any EOA. Observe: `isPriceDecreaseOffLimit = false`; `rsETHPrice` is written to `~0.095e18`.
4. Call `LRTDepositPool.depositETH{value: 1 ether}(0, "")` from attacker EOA. Observe: `rsethAmountToMint = 1e18 / 0.095e18 ≈ 10.5 rsETH`.
5. Restore oracle to `1.0e18`. Call `updateRSETHPrice()` again. Observe: new `rsETHPrice = totalETH / (originalSupply + 10.5)` — lower than before the attack.
6. Attacker redeems 10.5 rsETH for significantly more than 1 ETH; existing holders' redemption value is reduced proportionally.

**Foundry invariant test sketch:**

```solidity
function invariant_rsETHPriceNeverBelowFloor() public {
    assertGe(lrtOracle.rsETHPrice(), MIN_RSETH_PRICE);
}
```

Run with a fuzz campaign that randomly calls `updateRSETHPrice()` after injecting depressed mock oracle prices; the invariant will break under the current code when `pricePercentageLimit == 0`.

### Citations

**File:** contracts/LRTOracle.sol (L29-29)
```text
    uint256 public pricePercentageLimit;
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

**File:** contracts/LRTOracle.sol (L101-108)
```text
    function updatePriceOracleForValidated(address asset, address priceOracle) external onlyLRTAdmin {
        // Sanity check: oracle price must have precision between 1e16 and 1e19
        uint256 price = IPriceFetcher(priceOracle).getAssetPrice(asset);
        if (price > 1e19 || price < 1e16) {
            revert InvalidPriceOracle();
        }
        updatePriceOracleFor(asset, priceOracle);
    }
```

**File:** contracts/LRTOracle.sol (L273-274)
```text
            bool isPriceDecreaseOffLimit =
                pricePercentageLimit > 0 && diff > pricePercentageLimit.mulWad(highestRsethPrice);
```

**File:** contracts/LRTOracle.sol (L313-313)
```text
        rsETHPrice = newRsETHPrice;
```

**File:** contracts/LRTDepositPool.sol (L519-520)
```text
        // calculate rseth amount to mint based on asset amount and asset exchange rate
        rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
```
