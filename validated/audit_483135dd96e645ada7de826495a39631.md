Audit Report

## Title
Stale agETH Rate in `CrossChainRateReceiver` Allows Over-Minting of agETH on Token Deposits — (`contracts/agETH/AGETHRateReceiver.sol`)

## Summary
`CrossChainRateReceiver.getRate()` returns the stored `rate` unconditionally with no staleness check, despite `lastUpdated` being recorded on every `lzReceive` call. `AGETHPoolV3.deposit(address,uint256,string)` uses this potentially stale rate as the denominator when computing agETH to mint, while the deposited token's oracle rate is fetched live. When the agETH rate is stale-low relative to a token's current oracle rate, depositors receive more agETH than the ETH-equivalent value of their collateral justifies, causing the agETH supply to become under-collateralised.

## Finding Description
`CrossChainRateReceiver.lzReceive` sets `rate` and `lastUpdated` at lines 95–97, but `getRate()` at lines 103–105 returns `rate` with no age check on `lastUpdated`. `AGETHPoolV3.getRate()` (line 104–106) delegates directly to `IOracle(agETHOracle).getRate()`, which resolves to `CrossChainRateReceiver.getRate()` via `AGETHRateReceiver`. `AGETHPoolV3.viewSwapAgETHAmountAndFee(uint256,address)` at lines 188–194 then computes:

```solidity
uint256 agETHToETHrate = getRate();                                      // potentially stale
uint256 tokenToETHRate = IOracle(supportedTokenOracle[token]).getRate(); // live
agETHAmount = amountAfterFee * tokenToETHRate / agETHToETHrate;
```

This result is used directly by `deposit(address,uint256,string)` at line 147–151 to mint agETH. No circuit-breaker, pause, or maximum-age guard exists anywhere in the call chain. `lastUpdated` is stored but never read after being written.

## Impact Explanation
The protocol mints more agETH than the deposited collateral backs at current rates. The agETH supply becomes under-collateralised relative to the actual agETH/ETH exchange rate, meaning existing agETH holders' claims are diluted. This matches **Low — Contract fails to deliver promised returns, but does not lose the deposited collateral itself**.

## Likelihood Explanation
LayerZero cross-chain message delivery is subject to real-world delays (network congestion, relayer downtime, gas price spikes on the destination chain). During any such delay, the rate stored in `AGETHRateReceiver` becomes stale. A depositor only needs to observe that `lastUpdated` is old and that the token oracle rate has moved favourably — both are on-chain readable public state. No admin compromise, governance capture, or oracle manipulation is required. The exploit is repeatable for as long as the rate remains stale.

## Recommendation
Add a staleness guard in `CrossChainRateReceiver.getRate()`:

```solidity
uint256 public constant MAX_RATE_AGE = 1 days; // configurable by owner

function getRate() external view returns (uint256) {
    require(block.timestamp - lastUpdated <= MAX_RATE_AGE, "Rate is stale");
    return rate;
}
```

This causes deposits to revert when the agETH rate has not been refreshed within the acceptable window, preventing exploitation of stale rates.

## Proof of Concept

```solidity
// Foundry fork test (local fork only, no public mainnet testing)
// 1. Deploy AGETHRateReceiver; simulate lzReceive setting rate = 1.0e18,
//    lastUpdated = block.timestamp - 2 days (warp time forward 2 days)
// 2. Deploy a mock wstETH oracle returning 1.05e18
// 3. Deploy AGETHPoolV3 pointing to AGETHRateReceiver as agETHOracle,
//    add wstETH as supported token with the mock oracle
// 4. Depositor calls AGETHPoolV3.deposit(wstETH, 1e18, "")
// Expected fair agETHAmount: 1e18 * 1.05e18 / 1.05e18 = 1e18
// Actual agETHAmount:        1e18 * 1.05e18 / 1.0e18  = 1.05e18  (+5% over-mint)
// Assert: minted agETH (1.05e18) > fair value (1e18) → invariant broken
```