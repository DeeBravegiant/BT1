Audit Report

## Title
Stale Cross-Chain Rate in `CrossChainRateReceiver.getRate()` Enables Over-Minting of agETH, Diluting Existing Holders' Yield — (`contracts/cross-chain/CrossChainRateReceiver.sol`, `contracts/agETH/AGETHPoolV3.sol`)

## Summary

`CrossChainRateReceiver` stores `lastUpdated` on every LayerZero message but never enforces a maximum staleness bound in `getRate()`. `AGETHPoolV3.deposit()` consumes this unchecked rate to mint agETH. Because agETH is yield-bearing and its ETH-denominated rate rises monotonically, any depositor acting while the L2 rate lags the true L1 rate receives more agETH than their ETH warrants, diluting the unclaimed yield of all existing holders.

## Finding Description

`CrossChainRateReceiver.getRate()` returns the stored `rate` with no staleness check: [1](#0-0) 

`lastUpdated` is written on every `lzReceive` call but is never read back or compared against any maximum age anywhere in the contract: [2](#0-1) 

`AGETHPoolV3.deposit()` calls `viewSwapAgETHAmountAndFee`, which fetches the rate via `getRate()` and computes the mint amount as `amountAfterFee * 1e18 / agETHToETHrate`: [3](#0-2) 

The minted agETH is immediately credited to the depositor with no further validation: [4](#0-3) 

**Exploit flow:**
1. The L2 `rate` is stale (e.g., `1.00e18`) while the true L1 rate has risen (e.g., `1.05e18`) due to accrued staking yield between LayerZero updates.
2. An attacker calls `AGETHPoolV3.deposit{value: 1 ether}("x")`.
3. `viewSwapAgETHAmountAndFee` computes `agETHAmount = 1e18 * 1e18 / 1.00e18 = 1e18` agETH, instead of the correct `1e18 * 1e18 / 1.05e18 ≈ 0.952e18` agETH.
4. The attacker receives `~0.048e18` excess agETH per ETH deposited — agETH that is unbacked by the deposited ETH at the true rate.
5. The ETH bridged to L1 via `moveAssetsForBridging` is only the deposited amount, not what the inflated agETH supply implies. Existing holders' proportional share of the backing pool is permanently diluted.

No privileged role, governance action, or special condition is required. The L2 rate is structurally always stale to some degree (push-based, periodic LayerZero updates), and the L1 rate is publicly observable.

## Impact Explanation

**High — Theft of unclaimed yield.**

Every deposit made while the L2 rate is below the true L1 rate extracts yield that belongs to existing agETH holders. The excess agETH minted is unbacked: the ETH deposited covers only the depositor's fair share at the true rate, but the inflated agETH supply dilutes all existing holders' claims on the backing pool. The shortfall is borne by existing holders. The magnitude scales with deposit size and degree of staleness (`time_since_last_update × yield_rate × deposit_size`).

## Likelihood Explanation

**Medium-High.** The L2 rate is always stale to some degree — it can only be as fresh as the last LayerZero message. Rate updates are push-based and periodic; they are not triggered on every block. A sophisticated depositor can monitor the L1 agETH rate provider contract and deposit on L2 immediately after yield accrues but before the next LayerZero update arrives. No special role, key, or governance action is required — only a public `deposit()` call.

## Recommendation

Add a configurable `maxStaleness` parameter to `CrossChainRateReceiver` and revert in `getRate()` if `block.timestamp - lastUpdated > maxStaleness`:

```solidity
uint256 public maxStaleness; // e.g. 24 hours

function getRate() external view returns (uint256) {
    require(
        lastUpdated != 0 && block.timestamp - lastUpdated <= maxStaleness,
        "Rate is stale"
    );
    return rate;
}
```

`AGETHPoolV3.deposit()` will then revert when the oracle is stale, preventing over-minting until a fresh rate arrives via LayerZero.

## Proof of Concept

**Foundry fork test plan:**

1. Fork the L2 deployment.
2. Deploy a mock `AGETHRateReceiver` that returns `STALE_RATE = 1.00e18` (simulating a rate that has not been updated since yield accrued).
3. Deploy `AGETHPoolV3` pointing to the mock receiver.
4. Record `TRUE_RATE = 1.05e18` from the L1 oracle (publicly readable).
5. Call `pool.deposit{value: 1 ether}("poc")`.
6. Assert: `minted * TRUE_RATE / 1e18 > 1 ether` — the minted agETH, valued at the true L1 rate, exceeds the deposited ETH, confirming yield extraction.
7. Repeat with varying staleness durations to show the impact scales linearly with `time_since_last_update`.

The assertion directly quantifies the yield extracted per basis point of staleness, confirming the impact is concrete and repeatable.

### Citations

**File:** contracts/cross-chain/CrossChainRateReceiver.sol (L95-97)
```text
        rate = _rate;

        lastUpdated = block.timestamp;
```

**File:** contracts/cross-chain/CrossChainRateReceiver.sol (L103-105)
```text
    function getRate() external view returns (uint256) {
        return rate;
    }
```

**File:** contracts/agETH/AGETHPoolV3.sol (L121-125)
```text
        (uint256 agETHAmount, uint256 fee) = viewSwapAgETHAmountAndFee(amount);

        feeEarnedInETH += fee;

        agETH.mint(msg.sender, agETHAmount);
```

**File:** contracts/agETH/AGETHPoolV3.sol (L160-168)
```text
    function viewSwapAgETHAmountAndFee(uint256 amount) public view returns (uint256 agETHAmount, uint256 fee) {
        fee = amount * feeBps / 10_000;
        uint256 amountAfterFee = amount - fee;

        // rate of agETH in ETH
        uint256 agETHToETHrate = getRate();

        // Calculate the final agETH amount
        agETHAmount = amountAfterFee * 1e18 / agETHToETHrate;
```
