### Title
First Depositor rsETH Price Inflation via ETH Donation to LRTDepositPool - (File: contracts/LRTDepositPool.sol, contracts/LRTOracle.sol)

### Summary
An early depositor can manipulate the rsETH exchange rate by depositing a minimal amount of ETH to mint a tiny rsETH supply, then donating ETH directly to `LRTDepositPool` via its open `receive()` function. Because `getETHDistributionData()` counts `address(this).balance` as protocol ETH, and `_updateRsETHPrice()` computes `rsETHPrice = totalETHInProtocol / rsethSupply`, the attacker can inflate the rsETH price arbitrarily. Subsequent depositors receive zero or near-zero rsETH due to precision loss, while the attacker—holding the entire rsETH supply—claims all deposited ETH on withdrawal.

### Finding Description

**Step 1 — rsETH price initialization.**
`LRTOracle._updateRsETHPrice()` sets `rsETHPrice = 1 ether` when `rsethSupply == 0`: [1](#0-0) 

**Step 2 — Attacker mints minimal rsETH.**
The attacker calls `depositETH` with 1 wei. `getRsETHAmountToMint` computes:

```
rsethAmountToMint = (1 * 1e18) / 1e18 = 1 wei rsETH
``` [2](#0-1) 

**Step 3 — Attacker donates ETH directly to the pool.**
`LRTDepositPool` has an open `receive()` function: [3](#0-2) 

`getETHDistributionData()` counts `address(this).balance` as protocol ETH, so the donation is immediately reflected in `totalETHInProtocol`: [4](#0-3) 

**Step 4 — Attacker calls `updateRSETHPrice()` (public).**
`_updateRsETHPrice()` recomputes:

```
newRsETHPrice = (1 + donation) * 1e18 / 1 = (1 + donation) * 1e18
``` [5](#0-4) 

The price-increase guard is bypassed because `pricePercentageLimit` is **not set in `initialize`** and defaults to `0`: [6](#0-5) 

**Step 5 — Victim deposits.**
With `rsETHPrice = (1 + D) * 1e18` and victim depositing `V` wei ETH:

```
rsethAmountToMint = (V * 1e18) / ((1 + D) * 1e18) = V / (1 + D)
```

If `D >= V`, the victim receives **0 rsETH** (integer division truncates to zero). The victim's ETH is absorbed into the pool with no shares issued. [2](#0-1) 

**Step 6 — Attacker redeems.**
The attacker holds 100% of rsETH supply (1 wei). After the victim's deposit, the pool contains `1 + D + V` wei ETH. The attacker's 1 rsETH entitles them to all of it. Net attacker profit = `V` (the victim's full deposit), at a cost of `D ≈ V`.

### Impact Explanation

**Critical — Direct theft of user funds.** A victim depositing `V` ETH receives 0 rsETH and permanently loses their entire deposit to the attacker. The attacker recovers both their donation and the victim's deposit upon withdrawal. This is not a theoretical rounding loss; with `D = V`, the victim's deposit is completely stolen.

### Likelihood Explanation

**High.** The attack requires only:
1. Being the first (or very early) depositor — realistic at protocol launch.
2. Sending ETH to `LRTDepositPool.receive()` — permissionless.
3. Calling `updateRSETHPrice()` — permissionless public function.
4. `pricePercentageLimit == 0` — the **default state** since `initialize` does not set it.

No privileged access, oracle compromise, or governance capture is required. The attacker can execute this atomically or across a few transactions.

### Recommendation

1. **Seed the pool at initialization** with a non-trivial ETH deposit minted to a dead address (e.g., `1e15` wei), analogous to the original report's recommendation, to make price manipulation economically infeasible.
2. **Reject unaccounted ETH donations** by tracking expected ETH balance and excluding surplus from `totalETHInProtocol`, or by removing the open `receive()` fallback and only accepting ETH through named entry points.
3. **Set `pricePercentageLimit` in `initialize`** to a non-zero value so the price-increase guard is active from deployment.
4. **Enforce a minimum rsETH output** at the protocol level (not just via the caller-supplied `minRSETHAmountExpected`) to revert deposits that would yield 0 rsETH.

### Proof of Concept

```
// Setup: rsETHPrice = 1e18 (supply == 0)
// updateRSETHPrice() called → rsETHPrice = 1e18

// Step 1: Attacker deposits 1 wei ETH
lrtDepositPool.depositETH{value: 1}(0, "");
// rsETH supply = 1 wei, pool ETH = 1 wei

// Step 2: Attacker donates D = 1 ETH directly
(bool ok,) = address(lrtDepositPool).call{value: 1 ether}("");

// Step 3: Attacker updates price (public, pricePercentageLimit == 0)
lrtOracle.updateRSETHPrice();
// totalETHInProtocol = 1 + 1e18 ≈ 1e18
// newRsETHPrice = 1e18 * 1e18 / 1 = 1e36 (massively inflated)

// Step 4: Victim deposits 1 ETH
lrtDepositPool.depositETH{value: 1 ether}(0, "");
// rsethAmountToMint = (1e18 * 1e18) / 1e36 = 1 → rounds to 0 if donation is larger
// Victim receives 0 rsETH, loses 1 ETH

// Step 5: Attacker withdraws via withdrawal manager
// Attacker holds 100% of rsETH supply → redeems all ETH in pool
// Pool contains: 1 + 1e18 + 1e18 ≈ 2e18 wei
// Attacker profit ≈ 1 ETH (victim's deposit)
```

### Citations

**File:** contracts/LRTOracle.sol (L218-222)
```text
        if (rsethSupply == 0) {
            rsETHPrice = 1 ether;
            highestRsethPrice = 1 ether;
            return;
        }
```

**File:** contracts/LRTOracle.sol (L250-250)
```text
        uint256 newRsETHPrice = (totalETHInProtocol - protocolFeeInETH).divWad(rsethSupply);
```

**File:** contracts/LRTOracle.sol (L256-257)
```text
            bool isPriceIncreaseOffLimit =
                pricePercentageLimit > 0 && priceDifference > pricePercentageLimit.mulWad(highestRsethPrice);
```

**File:** contracts/LRTDepositPool.sol (L58-58)
```text
    receive() external payable { }
```

**File:** contracts/LRTDepositPool.sol (L480-480)
```text
        ethLyingInDepositPool = address(this).balance;
```

**File:** contracts/LRTDepositPool.sol (L519-521)
```text
        // calculate rseth amount to mint based on asset amount and asset exchange rate
        rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
    }
```
