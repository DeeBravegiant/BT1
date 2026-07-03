All code references check out exactly as claimed. Let me verify the full picture:

- `getExpectedAssetAmount` at line 593 makes a live call to `lrtOracle.getAssetPrice(asset)` [1](#0-0) 
- `rsETHPrice` is a cached state variable at line 28 of `LRTOracle.sol` [2](#0-1) 
- `getAssetPrice` delegates live to the registered `IPriceFetcher` [3](#0-2) 
- `ChainlinkPriceOracle.getAssetPrice` calls `latestRoundData()` with no try/catch or fallback [4](#0-3) 
- `initiateWithdrawal` calls `getExpectedAssetAmount` at line 168 [5](#0-4) 
- `instantWithdrawal` calls `getExpectedAssetAmount` at line 228 [6](#0-5) 

---

Audit Report

## Title
Live Oracle Call in `getExpectedAssetAmount` Blocks `initiateWithdrawal` and `instantWithdrawal` When Asset Price Oracle Reverts - (File: contracts/LRTWithdrawalManager.sol)

## Summary
`LRTWithdrawalManager.getExpectedAssetAmount()` makes a live call to `lrtOracle.getAssetPrice(asset)`, which delegates to a registered `IPriceFetcher` (e.g., `ChainlinkPriceOracle`) that calls `priceFeed.latestRoundData()` with no fallback. Both `initiateWithdrawal` and `instantWithdrawal` invoke this function unconditionally, so any revert from the underlying Chainlink feed permanently blocks all withdrawal paths for the affected asset until the oracle recovers.

## Finding Description
`getExpectedAssetAmount` computes the payout as:

```solidity
// LRTWithdrawalManager.sol L593
underlyingToReceive = amount * lrtOracle.rsETHPrice() / lrtOracle.getAssetPrice(asset);
```

`lrtOracle.rsETHPrice()` is a cached `uint256` state variable updated only when `updateRSETHPrice()` is explicitly called. `lrtOracle.getAssetPrice(asset)` is a live delegating call:

```solidity
// LRTOracle.sol L156-158
function getAssetPrice(address asset) public view onlySupportedOracle(asset) returns (uint256) {
    return IPriceFetcher(assetPriceOracle[asset]).getAssetPrice(asset);
}
```

`ChainlinkPriceOracle.getAssetPrice` calls `priceFeed.latestRoundData()` directly with no try/catch or fallback:

```solidity
// ChainlinkPriceOracle.sol L49-55
(, int256 price,,,) = priceFeed.latestRoundData();
```

Both user-facing entry points call `getExpectedAssetAmount` unconditionally before any state changes that could be reverted independently:

- `initiateWithdrawal` transfers rsETH to the contract at L166, then calls `getExpectedAssetAmount` at L168 — if the oracle reverts, the whole transaction reverts and rsETH is returned, but the user cannot queue a withdrawal.
- `instantWithdrawal` calls `getExpectedAssetAmount` at L228 before `burnFrom` at L229 — same revert behavior.

There are no try/catch wrappers, no stale-price fallbacks, and no circuit-breaker that would allow the protocol to use a cached asset price when the live feed is unavailable.

## Impact Explanation
**Medium — Temporary freezing of funds.** While the oracle for a supported asset (e.g., stETH, ETHx) is down, every call to `initiateWithdrawal` and `instantWithdrawal` for that asset reverts. Users holding rsETH who wish to exit into that asset cannot do so for the duration of the outage. Their rsETH remains in their wallets but the withdrawal path is completely blocked. The rest of the protocol (deposits, other asset withdrawals with functioning oracles) continues normally.

## Likelihood Explanation
Chainlink feeds have historically paused or reverted during extreme market conditions (LUNA crash, ETH Merge, L2 sequencer outages). The protocol supports multiple LST assets, each with its own oracle. No privileged action or attacker is required — the failure is triggered automatically by the oracle's own revert. Any single oracle failure blocks the corresponding withdrawal path for all users of that asset.

## Recommendation
Replace the live `getAssetPrice(asset)` call in `getExpectedAssetAmount` with a cached asset price, analogous to how `rsETHPrice` is already cached. Introduce a `mapping(address => uint256) public assetPrice` in `LRTOracle` that is updated alongside `rsETHPrice` in `_updateRsETHPrice()`. `getExpectedAssetAmount` (and `getRsETHAmountToMint` in `LRTDepositPool`) should read from this cached mapping rather than calling the live oracle, so that oracle downtime does not block user-facing withdrawal and deposit paths.

## Proof of Concept
1. Protocol has stETH as a supported asset with `ChainlinkPriceOracle` registered in `LRTOracle`.
2. Chainlink's stETH/ETH feed reverts (e.g., L2 sequencer down, feed deprecated, circuit-breaker triggered).
3. User calls `initiateWithdrawal(stETH, rsETHAmount, "")`.
4. Execution reaches `getExpectedAssetAmount(stETH, rsETHAmount)` → `lrtOracle.getAssetPrice(stETH)` → `ChainlinkPriceOracle.getAssetPrice(stETH)` → `priceFeed.latestRoundData()` → **reverts**.
5. The entire `initiateWithdrawal` transaction reverts. The user cannot queue a withdrawal for stETH.
6. `instantWithdrawal(stETH, rsETHAmount, "")` fails identically at line 228.
7. The user's rsETH remains in their wallet with no exit path for stETH until the oracle recovers.

**Foundry fork test plan:** Fork mainnet, mock the stETH Chainlink feed to revert on `latestRoundData()`, call `initiateWithdrawal` and `instantWithdrawal` with a funded rsETH holder, and assert both revert with the oracle error. Confirm the same calls succeed after restoring the mock feed.

### Citations

**File:** contracts/LRTWithdrawalManager.sol (L168-168)
```text
        uint256 expectedAssetAmount = getExpectedAssetAmount(asset, rsETHUnstaked);
```

**File:** contracts/LRTWithdrawalManager.sol (L228-228)
```text
        uint256 assetAmountUnlocked = getExpectedAssetAmount(asset, rsETHUnstaked);
```

**File:** contracts/LRTWithdrawalManager.sol (L593-593)
```text
        underlyingToReceive = amount * lrtOracle.rsETHPrice() / lrtOracle.getAssetPrice(asset);
```

**File:** contracts/LRTOracle.sol (L28-28)
```text
    uint256 public override rsETHPrice;
```

**File:** contracts/LRTOracle.sol (L156-158)
```text
    function getAssetPrice(address asset) public view onlySupportedOracle(asset) returns (uint256) {
        return IPriceFetcher(assetPriceOracle[asset]).getAssetPrice(asset);
    }
```

**File:** contracts/oracles/ChainlinkPriceOracle.sol (L49-55)
```text
    function getAssetPrice(address asset) external view onlySupportedAsset(asset) returns (uint256) {
        AggregatorV3Interface priceFeed = AggregatorV3Interface(assetPriceFeed[asset]);

        (, int256 price,,,) = priceFeed.latestRoundData();

        return uint256(price) * 1e18 / 10 ** uint256(priceFeed.decimals());
    }
```
