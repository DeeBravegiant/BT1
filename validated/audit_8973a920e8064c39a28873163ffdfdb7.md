Audit Report

## Title
Stale `rsETHPrice` Enables Depositors to Mint Excess rsETH at the Expense of Existing Yield Holders - (File: contracts/LRTOracle.sol, contracts/LRTDepositPool.sol)

## Summary
`LRTOracle.rsETHPrice` is a stored state variable updated only when `updateRSETHPrice()` is explicitly called. Between updates, any increase in `totalETHInProtocol` (from accrued rewards or LST price appreciation) is not reflected in the minting rate. A depositor who acts before the price update receives more rsETH than their deposit warrants at the true rate, permanently diluting the yield owed to pre-existing rsETH holders.

## Finding Description
`rsETHPrice` is a mutable storage variable set only inside `_updateRsETHPrice()`:

```solidity
// LRTOracle.sol L28
uint256 public override rsETHPrice;
```

`updateRSETHPrice()` is publicly callable with no access restriction:

```solidity
// LRTOracle.sol L87-89
function updateRSETHPrice() public whenNotPaused {
    _updateRsETHPrice();
}
```

Every deposit path reads this stored value directly via `getRsETHAmountToMint()`:

```solidity
// LRTDepositPool.sol L519-520
rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
```

Both `depositETH()` (L87) and `depositAsset()` (L111) route through `_beforeDeposit()` → `getRsETHAmountToMint()`, so every deposit is priced against the stale stored rate.

`_getTotalEthInProtocol()` (LRTOracle.sol L331–343) computes the live TVL by summing `getTotalAssetDeposits(asset)` across all supported assets, multiplied by their live oracle prices. When LST oracle prices rise, or when ETH rewards are swept into the deposit pool, `totalETHInProtocol` increases immediately while `rsETHPrice` remains at its last stored value.

The `pricePercentageLimit` guard (LRTOracle.sol L252–266) only reverts a public `updateRSETHPrice()` call if the price jump exceeds the configured threshold. It does not prevent deposits at the stale price, and it does not prevent the attacker from calling the update themselves when the delta is within the limit.

**Exploit path:**
1. Rewards accrue (LST price tick or ETH swept to deposit pool) → `totalETHInProtocol` rises, `rsETHPrice` is stale.
2. Attacker calls `depositETH()` or `depositAsset()` at the stale (lower) price, receiving more rsETH than the true rate warrants.
3. Attacker (or anyone) calls `updateRSETHPrice()` — if the delta is within `pricePercentageLimit`, this succeeds publicly.
4. `rsETHPrice` is updated to reflect the higher TVL, now shared across a larger rsETH supply that includes the attacker's over-minted tokens.
5. Pre-existing holders' proportional share of the TVL is permanently reduced.

## Impact Explanation
**High — Theft of unclaimed yield.**

Pre-existing rsETH holders have accrued staking rewards reflected in `totalETHInProtocol` but not yet in `rsETHPrice`. A depositor who mints at the stale price captures a proportional share of those rewards without having been staked during the accrual period. The stolen yield scales linearly with deposit size and the magnitude of the stale gap. Once `rsETHPrice` is updated, the dilution is irreversible — the original holders' share of TVL is permanently reduced. This matches the allowed impact: **High — Theft of unclaimed yield**.

## Likelihood Explanation
**Medium.** EigenLayer consensus-layer rewards and LST oracle price ticks create a continuous, non-zero staleness window. `updateRSETHPrice()` is publicly callable, so the attacker does not need to front-run a keeper — they can deposit first and then trigger the update themselves, provided the price delta is within `pricePercentageLimit`. The `pricePercentageLimit` guard caps per-transaction profit but does not eliminate the attack; repeated deposits across multiple update cycles compound the theft. No privileged access is required.

## Recommendation
1. **Dynamic price at deposit time**: Replace the stored `rsETHPrice` read in `getRsETHAmountToMint()` with an inline computation of `_getTotalEthInProtocol() / rsETH.totalSupply()`, so the minting rate always reflects the current TVL.
2. **Atomic price update on deposit**: Call `_updateRsETHPrice()` at the start of every `depositETH()` / `depositAsset()` execution to ensure the price is fresh before minting.
3. **Minimum holding period**: Require rsETH to be held for at least one oracle update cycle before redemption, preventing same-block deposit-and-exit exploitation.

## Proof of Concept
Assume `pricePercentageLimit` = 5% (5e16), no fees, withdrawal processes at current `rsETHPrice`.

**Step 1 — Baseline**
- `totalETHInProtocol` = 1,000 ETH, `rsETH.totalSupply()` = 1,000, `rsETHPrice` = 1.000 ETH/rsETH.

**Step 2 — Rewards accrue**
- LST oracle price ticks up or ETH rewards swept to deposit pool; `totalETHInProtocol` = 1,010 ETH.
- `rsETHPrice` still = 1.000 (stale).

**Step 3 — Attacker deposits**
- Attacker calls `depositETH{value: 1000 ETH}()`.
- `getRsETHAmountToMint`: `1000e18 * 1e18 / 1.000e18 = 1,000 rsETH` minted.
- True fair amount: `1000 / 1.010 ≈ 990.1 rsETH`.
- New state: `totalETHInProtocol` = 2,010 ETH, `rsETH.totalSupply()` = 2,000.

**Step 4 — Attacker triggers price update**
- Attacker calls `updateRSETHPrice()`. Delta = `(2010/2000 - 1.000) / 1.000 = 0.5%` < 5% limit → succeeds.
- `rsETHPrice` = 2010 / 2000 = 1.005 ETH/rsETH.

**Step 5 — Outcome**
- Attacker redeems 1,000 rsETH at 1.005 → **1,005 ETH** (profit: 5 ETH, zero staking time).
- Original 1,000 rsETH holders redeem at 1.005 → 1,005 ETH total, instead of the 1,010 ETH they were owed.
- **5 ETH of yield permanently stolen** from pre-existing holders.

Foundry fork test plan: fork mainnet, set `rsETHPrice` to a value slightly below `totalETHInProtocol / rsETH.totalSupply()`, call `depositETH` as an unprivileged address, then call `updateRSETHPrice`, and assert that the attacker's rsETH redemption value exceeds their deposit.