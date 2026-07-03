Audit Report

## Title
rsETH Inflation Attack via ETH Donation to `LRTDepositPool` Enables Zero-Share Minting - (File: `contracts/LRTDepositPool.sol`)

## Summary
An attacker who is the first depositor can donate ETH directly to `LRTDepositPool` and call the permissionless `updateRSETHPrice()` to inflate `rsETHPrice` by an unbounded factor, because `pricePercentageLimit` defaults to `0`. Any subsequent depositor who passes `minRSETHAmountExpected = 0` will have their ETH accepted by the protocol while receiving 0 rsETH in return. With no rsETH balance, the victim has no mechanism to initiate a withdrawal, permanently freezing their funds.

## Finding Description

Three weaknesses cooperate to produce the exploit:

**1. ETH donation directly inflates TVL**

`LRTDepositPool` exposes a bare `receive()`:
```solidity
receive() external payable { }   // LRTDepositPool.sol L58
```
`getETHDistributionData()` reads the raw contract balance:
```solidity
ethLyingInDepositPool = address(this).balance;   // LRTDepositPool.sol L480
```
Any ETH sent directly to the contract is immediately counted in TVL and therefore in the rsETH price calculation.

**2. `updateRSETHPrice()` is public and uncapped when `pricePercentageLimit == 0`**

```solidity
function updateRSETHPrice() public whenNotPaused {   // LRTOracle.sol L87-89
    _updateRsETHPrice();
}
```
The only guard against a large price jump is:
```solidity
bool isPriceIncreaseOffLimit =
    pricePercentageLimit > 0 && priceDifference > pricePercentageLimit.mulWad(highestRsethPrice);
// LRTOracle.sol L256-257
```
`pricePercentageLimit` is a storage variable that defaults to `0`. When it is `0`, the condition short-circuits to `false` and the price can be inflated by any arbitrary multiple in a single call.

**3. `_beforeDeposit` does not reject a zero-rsETH mint**

```solidity
rsethAmountToMint = getRsETHAmountToMint(asset, depositAmount);   // LRTDepositPool.sol L665

if (rsethAmountToMint < minRSETHAmountExpected) {                 // LRTDepositPool.sol L667-669
    revert MinimumAmountToReceiveNotMet();
}
```
There is no `require(rsethAmountToMint > 0)`. If the caller passes `minRSETHAmountExpected = 0`, the check `0 < 0` is `false` and the deposit proceeds. `RSETH.mint(victim, 0)` does not revert: the `checkDailyMintLimit(0)` modifier evaluates `currentPeriodMintedAmount + 0 > maxMintAmountPerDay` as false for any `maxMintAmountPerDay > 0`, and OpenZeppelin `_mint` with amount `0` is a no-op.

**Mint formula:**
```solidity
rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
// LRTDepositPool.sol L520
```
After inflation, `rsETHPrice = (1 + X) * 1e18`. For a victim deposit `Y` where `Y < 1 + X`, integer division yields `rsethAmountToMint = 0`.

## Impact Explanation

**Critical — permanent freezing of user funds and direct theft.**

The victim's ETH is transferred into `LRTDepositPool` as `msg.value` before any check can revert it, but 0 rsETH is minted. With no rsETH balance, the victim has no mechanism to initiate a withdrawal. Their ETH is permanently locked in the protocol. The attacker, holding the only 1 wei of rsETH (100% of supply), can eventually redeem it for the entire pool balance (their donation + victim's deposit), constituting direct theft of user funds.

## Likelihood Explanation

**Medium.** The attacker must be the first depositor or act when rsETH supply is negligibly small. On Ethereum mainnet, front-running the first legitimate deposit is straightforward. The victim must pass `minRSETHAmountExpected = 0`, which is the default for many integrations and scripts that omit slippage protection. The critical enabler — `pricePercentageLimit == 0` — is the contract's default state at deployment. The protocol must be live (i.e., `maxMintAmountPerDay > 0` must be set by the admin for any deposits to work), which is a realistic operational precondition, not a special assumption.

## Recommendation

1. **Enforce a non-zero mint amount.** Add `require(rsethAmountToMint > 0, "ZeroMint")` inside `_beforeDeposit` before the slippage check (`LRTDepositPool.sol` L665-669).
2. **Initialize `pricePercentageLimit` to a safe non-zero value** (e.g., 1% = `1e16`) during deployment so that a single `updateRSETHPrice()` call cannot inflate the price by an unbounded factor (`LRTOracle.sol` L29, L125-128).
3. **Seed the pool at deployment.** Mint a small amount of rsETH to a dead address (e.g., `address(0xdead)`) during initialization so that `rsethSupply` is never `0` for an attacker-controlled first deposit (`LRTOracle.sol` L218-222).

## Proof of Concept

```
Preconditions: maxMintAmountPerDay > 0 (protocol live), pricePercentageLimit = 0 (default), rsETH totalSupply = 0

Step 0 — Attacker calls updateRSETHPrice() (rsethSupply == 0):
  → rsETHPrice = 1e18, highestRsethPrice = 1e18 (LRTOracle.sol L218-222)

Step 1 — Attacker deposits 1 wei ETH:
  depositETH{value: 1}(minRSETHAmountExpected=0, "")
  rsethAmountToMint = (1 * 1e18) / 1e18 = 1
  → Attacker receives 1 wei rsETH. rsETH totalSupply = 1.

Step 2 — Attacker donates X ETH directly to LRTDepositPool:
  (bool ok,) = address(lrtDepositPool).call{value: X}("");
  → LRTDepositPool.balance = 1 + X

Step 3 — Attacker calls updateRSETHPrice():
  rsethSupply = 1, totalETHInProtocol = 1 + X
  previousTVL = 1 * 1e18 / 1e18 = 1
  newRsETHPrice = (1 + X - fee) * 1e18 / 1 = (1 + X - fee) * 1e18
  pricePercentageLimit == 0 → isPriceIncreaseOffLimit = false → no revert
  → rsETHPrice ≈ (1 + X) * 1e18

Step 4 — Victim deposits Y ETH (Y ≤ X) with minRSETHAmountExpected = 0:
  rsethAmountToMint = (Y * 1e18) / ((1 + X) * 1e18) = Y / (1 + X) = 0 (integer division)
  0 < 0 → false → no revert
  RSETH.mint(victim, 0) → checkDailyMintLimit(0) passes, _mint(victim, 0) is no-op
  → Victim's Y ETH is in the pool; victim has 0 rsETH and no withdrawal path.

Step 5 — Attacker redeems 1 wei rsETH (100% of supply):
  → Attacker recovers 1 + X + Y ETH.
  Net attacker profit: Y ETH (victim's entire deposit).
```

**Foundry test plan:** Deploy `LRTDepositPool`, `LRTOracle`, and `RSETH` on a local fork. Set `maxMintAmountPerDay` to a large value. Execute Steps 0–5 above. Assert `balanceOf(victim) == 0` and `address(lrtDepositPool).balance == 1 + X + Y` after Step 4. Assert attacker recovers `1 + X + Y` ETH after Step 5.