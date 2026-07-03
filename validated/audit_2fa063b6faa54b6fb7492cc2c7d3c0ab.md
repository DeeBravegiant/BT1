Audit Report

## Title
Stale Cached Rate in `CrossChainRateReceiver` Enables Front-Running of Rate Updates — (File: `contracts/cross-chain/CrossChainRateReceiver.sol`)

## Summary
`CrossChainRateReceiver` stores a single cached `rate` that is only updated when a LayerZero message arrives via `lzReceive`, with no staleness guard. Both `RSETHPoolNoWrapper` and `RSETHPoolV3` price every deposit against this cached value without any freshness check. Because `updateRate()` on `MultiChainRateProvider` is permissionless and LayerZero delivery introduces a latency window, an unprivileged attacker can deposit ETH on L2 at the stale (lower) rate before the update lands, receive excess rsETH/wrsETH, and redeem at the true rate on L1, extracting value from the pool's reserves.

## Finding Description

**Root cause — `CrossChainRateReceiver.sol`**

`CrossChainRateReceiver` holds a single `uint256 public rate` written only inside `lzReceive`:

```solidity
// CrossChainRateReceiver.sol L95-L97
rate = _rate;
lastUpdated = block.timestamp;
emit RateUpdated(_rate);
```

`getRate()` returns the raw cached value with no staleness check:

```solidity
// CrossChainRateReceiver.sol L103-L105
function getRate() external view returns (uint256) {
    return rate;
}
```

**Consumption in deposit pools**

`RSETHPoolNoWrapper.getRate()` and `RSETHPoolV3.getRate()` both forward directly to `IOracle(rsETHOracle).getRate()` with no freshness validation:

```solidity
// RSETHPoolNoWrapper.sol L220-L222
function getRate() public view returns (uint256) {
    return IOracle(rsETHOracle).getRate();
}
```

Both `viewSwapRsETHAmountAndFee` implementations use this rate to compute rsETH output:

```solidity
// RSETHPoolNoWrapper.sol L282-L285
uint256 rsETHToETHrate = getRate();
rsETHAmount = amountAfterFee * 1e18 / rsETHToETHrate;
```

**Permissionless rate update trigger**

`MultiChainRateProvider.updateRate()` has no access control — any caller can trigger a cross-chain rate push:

```solidity
// MultiChainRateProvider.sol L108
function updateRate() external payable nonReentrant {
```

This means an attacker can observe the L1 rate, call `updateRate()` themselves, and then race the LayerZero message to L2.

**Attack path (RSETHPoolNoWrapper)**

`RSETHPoolNoWrapper.deposit` transfers pre-minted rsETH OFT tokens directly from the pool's reserves:

```solidity
// RSETHPoolNoWrapper.sol L237-L241
(uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount);
feeEarnedInETH += fee;
rsETH.safeTransfer(msg.sender, rsETHAmount);
```

1. L1 rsETH rate increases from `R_old` to `R_new` (reward accrual).
2. Attacker calls `updateRate()` on L1; LayerZero message is in flight.
3. **Before** `lzReceive` lands on L2, attacker calls `deposit()` with `ETH_amount`.
   - Receives `rsETHAmount = ETH_amount * 1e18 / R_old` (inflated, since `R_old < R_new`).
4. `lzReceive` lands; `rate = R_new`.
5. Attacker bridges rsETH OFT back to L1 and redeems via `LRTWithdrawalManager`.
   - ETH received = `rsETHAmount * R_new / 1e18 = ETH_amount * R_new / R_old > ETH_amount`.

**Existing checks are insufficient**

- `RSETHPoolV3` has a `dailyMintLimit` modifier, which caps per-day exposure but does not prevent the attack within the limit.
- `RSETHPoolNoWrapper` has no analogous cap.
- Neither pool checks `lastUpdated` from the oracle.
- No circuit-breaker on rate-change magnitude exists anywhere in the path.

## Impact Explanation

**Critical — Direct theft of protocol funds.**

In `RSETHPoolNoWrapper`, the pool holds a finite pre-minted rsETH OFT reserve. Every deposit at a stale low rate transfers more rsETH from that reserve than the deposited ETH is worth at the true rate. The excess rsETH is bridged to L1 and redeemed for ETH, leaving the pool short. The pool receives ETH worth less than the rsETH it disbursed, constituting direct theft from the pool's rsETH reserves. Honest depositors who arrive after the rate update receive fewer rsETH per ETH, effectively subsidising the attacker.

In `RSETHPoolV3`, excess wrsETH minted beyond fair value dilutes all existing wrsETH holders and over-claims on the underlying ETH collateral.

## Likelihood Explanation

**Medium-High.**

- The rsETH rate increases continuously as EigenLayer staking rewards accrue; every rate update creates an exploitable window.
- `updateRate()` is permissionless — the attacker controls the timing of the L1 push.
- LayerZero message delivery latency (typically minutes) is publicly observable; the attacker monitors the L1 `RateUpdated` event and front-runs `lzReceive` on L2.
- No special role, governance access, or external protocol compromise is required — only a standard ETH deposit.
- The attack is repeatable on every rate update cycle.

## Recommendation

1. **Staleness check**: In `CrossChainRateReceiver.getRate()` (or in each pool's `getRate()`), revert if `block.timestamp - lastUpdated > MAX_STALENESS` (e.g., 1 hour).
2. **Rate-change circuit-breaker**: In `lzReceive`, revert or pause deposits if the incoming rate deviates from the previous rate by more than a threshold (e.g., 0.5%).
3. **Rate smoothing**: Interpolate linearly from `rate_old` to `rate_new` over a short window after each `lzReceive`, analogous to ERC-4626 vault share-price smoothing.
4. **Reduce update gap**: Trigger `updateRate()` automatically on every L1 rsETH rate change rather than relying on periodic or permissionless keeper calls.

## Proof of Concept

```
Assumptions:
  R_old = 1.050e18  (stale L2 rate)
  R_new = 1.055e18  (true L1 rate after reward accrual)
  ETH deposited = 100 ETH
  feeBps = 0

Step 1 — Attacker calls updateRate() on L1; LZ message in flight.

Step 2 — Before lzReceive lands, attacker calls RSETHPoolNoWrapper.deposit{value: 100e18}(""):
  rsETHAmount = 100e18 * 1e18 / 1.050e18 = 95.238 rsETH

Step 3 — lzReceive lands: rate = 1.055e18

Step 4 — Attacker bridges 95.238 rsETH OFT to L1 via LayerZero OFT bridge.

Step 5 — Attacker calls LRTWithdrawalManager.initiateWithdrawal(ETH, 95.238e18, ""):
  expectedAssetAmount = 95.238e18 * 1.055e18 / 1e18 = 100.476 ETH

Step 6 — After withdrawalDelayBlocks, attacker calls completeWithdrawal():
  Receives 100.476 ETH

Profit = 0.476 ETH (~0.476%) on 100 ETH.

Foundry fork test outline:
1. Fork Arbitrum + Ethereum mainnet.
2. Deploy/configure RSETHPoolNoWrapper with CrossChainRateReceiver as oracle.
3. Set rate = R_old in receiver.
4. Call deposit() as attacker with 100 ETH → assert rsETHAmount > 100e18 * 1e18 / R_new.
5. Update rate to R_new via lzReceive mock.
6. Bridge rsETH to L1 mock, call initiateWithdrawal, advance blocks, completeWithdrawal.
7. Assert attacker ETH balance > initial 100 ETH.
```