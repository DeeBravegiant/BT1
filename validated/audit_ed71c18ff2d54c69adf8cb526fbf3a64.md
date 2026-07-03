### Title
First-Depositor Inflation Attack via Bootstrap `rsETHPrice` Bypass — (File: `contracts/LRTOracle.sol`)

---

### Summary

`LRTOracle._updateRsETHPrice()` unconditionally sets `rsETHPrice = 1 ether` and returns early when `rsethSupply == 0`, bypassing every price-validation guard. Because `pricePercentageLimit` is never initialized (defaults to `0`), the guard that would otherwise cap price increases is also permanently disabled until an admin explicitly sets it. An unprivileged attacker can chain these two gaps to inflate `rsETHPrice` to an arbitrary value immediately after deployment, causing subsequent depositors to receive 0 rsETH for real ETH — a direct theft of funds.

---

### Finding Description

**Root cause 1 — bootstrap bypass in `_updateRsETHPrice()`** [1](#0-0) 

When `rsethSupply == 0`, the function sets `rsETHPrice = 1 ether` and `highestRsethPrice = 1 ether` then returns immediately. Every downstream guard — the `pricePercentageLimit` ceiling, fee minting, and the downside auto-pause — is skipped entirely. This is the direct analog of Shardeum's `stakingEnabled = false` during the first 10 cycles: a deliberate bootstrap shortcut that disables all security checks.

**Root cause 2 — `pricePercentageLimit` defaults to `0`** [2](#0-1) 

`initialize()` never sets `pricePercentageLimit`. It therefore starts at `0`. The guard that enforces a ceiling on price increases reads: [3](#0-2) 

`pricePercentageLimit > 0` is `false` → `isPriceIncreaseOffLimit` is always `false` → **no price-increase limit is enforced** until an admin calls `setPricePercentageLimit()`.

**Root cause 3 — open ETH donation path** [4](#0-3) 

Anyone can send ETH directly to `LRTDepositPool`. That ETH is immediately counted in `getETHDistributionData()` as `address(this).balance`, inflating `totalETHInProtocol` used by the oracle.

**Root cause 4 — zero-rsETH mint is silently accepted** [5](#0-4) 

`getRsETHAmountToMint` divides by `rsETHPrice`. If `rsETHPrice` is inflated, the result rounds down to `0`. `_beforeDeposit` only reverts if `rsethAmountToMint < minRSETHAmountExpected`; a victim who passes `minRSETHAmountExpected = 0` (common default) receives 0 rsETH while their ETH is accepted.

---

### Impact Explanation

**Critical — direct theft of depositor funds.**

The victim's ETH is permanently absorbed into the protocol with no rsETH issued. The attacker holds 100 % of the rsETH supply and can redeem it for the entire TVL (their donation + the victim's ETH). The donation is fully recovered; the victim's ETH is the attacker's profit.

---

### Likelihood Explanation

The attack window opens at deployment and closes only when an admin explicitly calls `setPricePercentageLimit()`. There is no on-chain enforcement that this must happen before the first deposit. `updateRSETHPrice()` is a public, permissionless function callable by anyone. The only cost to the attacker is the ETH donation, which is recovered in full. Any depositor who omits a slippage guard (`minRSETHAmountExpected = 0`) is a viable victim.

---

### Recommendation

1. **Set a non-zero `pricePercentageLimit` in `initialize()`** (e.g., `1e16` for 1 %) so the price-increase ceiling is active from block 0.
2. **Revert in `_beforeDeposit` if `rsethAmountToMint == 0`** to prevent silent fund loss regardless of oracle state.
3. **Seed the protocol with a non-trivial initial deposit** (or use a virtual-offset pattern as described in the OpenZeppelin ERC-4626 inflation-attack documentation already present in this repo) so the first-depositor attack surface does not exist.
4. Consider restricting `receive()` to known callers (node delegators, reward receivers) to eliminate the open donation vector.

---

### Proof of Concept

```
State at deployment:
  rsETHPrice          = 0      (uninitialized)
  rsethSupply         = 0
  pricePercentageLimit = 0     (uninitialized — never set in initialize())

Step 1 — Attacker triggers bootstrap bypass:
  attacker calls updateRSETHPrice()
    → rsethSupply == 0 → early return
    → rsETHPrice = 1e18, highestRsethPrice = 1e18
    (all validation skipped)

Step 2 — Attacker acquires a foothold:
  attacker calls depositETH{value: 1 wei}(minRSETHAmountExpected=0, "")
    → rsethAmountToMint = (1 * 1e18) / 1e18 = 1 wei rsETH
    → attacker holds 1 wei rsETH (100 % of supply)

Step 3 — Attacker donates ETH to inflate TVL:
  attacker sends 1000 ETH to LRTDepositPool via receive()
    → address(this).balance = 1000e18 + 1 wei
    → totalETHInProtocol ≈ 1000e18

Step 4 — Attacker inflates rsETHPrice (no limit check):
  attacker calls updateRSETHPrice()
    → rsethSupply = 1 wei
    → newRsETHPrice = (1000e18) / 1 = 1000e18 * 1e18  (≈ 1000 ETH per wei of rsETH)
    → pricePercentageLimit == 0 → isPriceIncreaseOffLimit = false → no revert
    → rsETHPrice = ~1000e36

Step 5 — Victim deposits with no slippage guard:
  victim calls depositETH{value: 999e18}(minRSETHAmountExpected=0, "")
    → rsethAmountToMint = (999e18 * 1e18) / 1000e36 = 0  (rounds down)
    → 0 rsETH minted; 999 ETH accepted into pool
    → victim loses 999 ETH

Step 6 — Attacker redeems:
  attacker redeems 1 wei rsETH (100 % of supply)
    → entitled to entire TVL = 1000 ETH (donation) + 1 wei + 999 ETH (victim) ≈ 1999 ETH
    → net attacker profit = 1999 ETH − 1000 ETH (donation) − 1 wei ≈ 999 ETH stolen
```

### Citations

**File:** contracts/LRTOracle.sol (L64-68)
```text
    function initialize(address lrtConfigAddr) external initializer {
        UtilLib.checkNonZeroAddress(lrtConfigAddr);
        lrtConfig = ILRTConfig(lrtConfigAddr);
        emit UpdatedLRTConfig(lrtConfigAddr);
    }
```

**File:** contracts/LRTOracle.sol (L218-222)
```text
        if (rsethSupply == 0) {
            rsETHPrice = 1 ether;
            highestRsethPrice = 1 ether;
            return;
        }
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

**File:** contracts/LRTDepositPool.sol (L506-521)
```text
    function getRsETHAmountToMint(
        address asset,
        uint256 amount
    )
        public
        view
        override
        returns (uint256 rsethAmountToMint)
    {
        // setup oracle contract
        address lrtOracleAddress = lrtConfig.getContract(LRTConstants.LRT_ORACLE);
        ILRTOracle lrtOracle = ILRTOracle(lrtOracleAddress);

        // calculate rseth amount to mint based on asset amount and asset exchange rate
        rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
    }
```
