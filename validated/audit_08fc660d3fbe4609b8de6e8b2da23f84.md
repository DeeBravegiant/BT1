Audit Report

## Title
Stale Cross-Chain Rate Allows Over-Issuance of wrsETH, Leading to Pool Undercollateralization — (`contracts/cross-chain/CrossChainRateReceiver.sol`)

## Summary
`CrossChainRateReceiver.getRate()` returns the last stored rate with no staleness check despite storing a `lastUpdated` timestamp. Both `RSETHPoolV2` and `RSETHPoolV3` use this rate directly to compute how much `wrsETH` to mint per ETH deposited. Because rsETH/ETH is monotonically increasing, any lag between an L1 rate update and the next LayerZero delivery creates a window where depositors receive more `wrsETH` than the deposited ETH can cover at the true rate, permanently undercollateralizing the pool.

## Finding Description
`CrossChainRateReceiver` stores both `rate` and `lastUpdated` but `getRate()` exposes no staleness guard:

```solidity
// contracts/cross-chain/CrossChainRateReceiver.sol L102-105
function getRate() external view returns (uint256) {
    return rate;
}
```

`lastUpdated` is written on every `lzReceive()` call but is never read by `getRate()`.

Both pool versions delegate pricing entirely to this oracle:

- `RSETHPoolV2.getRate()` → `IOracle(rsETHOracle).getRate()` (L201-203)
- `RSETHPoolV3.getRate()` → `IOracle(rsETHOracle).getRate()` (L235-237)

Both `viewSwapRsETHAmountAndFee` implementations compute:

```solidity
rsETHAmount = amountAfterFee * 1e18 / rsETHToETHrate;
```

where `rsETHToETHrate` is the potentially stale value. `deposit()` mints directly from this result with no additional freshness check.

**Exploit flow:**
1. L1 rsETH/ETH rate increases R0 → R1 (R1 > R0) due to staking rewards.
2. LZ message not yet relayed; `RSETHRateReceiver.rate` still holds R0.
3. Attacker calls `RSETHPoolV2.deposit{value: E}()`.
4. Pool computes `rsETHAmount = E * 1e18 / R0 > E * 1e18 / R1`.
5. Attacker receives excess `wrsETH` delta: `E * 1e18 * (1/R0 − 1/R1)`.
6. Attacker bridges `wrsETH` to L1, unwraps to rsETH, redeems at R1.
7. Attacker extracts `E * (R1/R0 − 1)` ETH of value never deposited.

The `dailyMintLimit` modifier caps per-day exposure but does not prevent the exploit; it only bounds the rate of insolvency accumulation.

## Impact Explanation
**Critical — Protocol insolvency.** The pool's ETH reserves are insufficient to back all outstanding `wrsETH` at the true rsETH/ETH rate. Every deposit made during a stale-rate window permanently widens this gap. The pool issues more `wrsETH` than the ETH it holds can redeem at the true rate, creating a structural deficit that grows with deposit size and stale duration. This matches the allowed Critical impact: "Protocol insolvency."

## Likelihood Explanation
- rsETH/ETH increases continuously (~4–5% APY), so any delay creates a profitable window.
- LayerZero relayer delays are normal operational events; there is no on-chain SLA enforcing freshness.
- The contract stores `lastUpdated` but never uses it as a guard, so any delay — routine or adversarial (relayer censorship, gas spike) — opens the window.
- The attack requires no special role, no front-running, and no external protocol compromise: a single `deposit()` call suffices.
- Profit per deposit is small under normal conditions (~0.013%/day lag) but scales with deposit size and stale duration, and is repeatable up to the daily limit.

## Recommendation
Add a configurable maximum staleness threshold and revert in `getRate()` if exceeded:

```solidity
uint256 public maxStaleness; // e.g., 24 hours

function getRate() external view returns (uint256) {
    require(
        block.timestamp - lastUpdated <= maxStaleness,
        "Rate is stale"
    );
    return rate;
}
```

This causes `deposit()` to revert automatically when the oracle is stale, eliminating the exploit window without requiring admin intervention.

## Proof of Concept

```solidity
// Foundry fork test against a chain where RSETHRateReceiver is deployed
function testStaleRateOverIssuance() external {
    uint256 R0 = rateReceiver.getRate(); // e.g., 1.05e18

    // Simulate L1 rate increase without LZ delivery
    vm.warp(block.timestamp + 7 days);
    // rateReceiver.rate is still R0

    uint256 R1 = 1.06e18; // true rate after 7 days of rewards

    uint256 E = 10 ether;
    uint256 rsETHAtStale = E * 1e18 / R0;  // more rsETH (stale)
    uint256 rsETHAtTrue  = E * 1e18 / R1;  // correct rsETH

    vm.deal(attacker, E);
    vm.prank(attacker);
    pool.deposit{value: E}("ref");

    uint256 minted = wrsETH.balanceOf(attacker);
    assertGt(minted, rsETHAtTrue, "Over-issued wrsETH");
    assertEq(minted, rsETHAtStale);

    // Excess wrsETH bridged to L1 and redeemed at R1 extracts:
    // (minted - rsETHAtTrue) * R1 / 1e18 ETH from the protocol
}
```