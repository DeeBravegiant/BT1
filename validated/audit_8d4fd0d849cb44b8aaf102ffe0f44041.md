Audit Report

## Title
Missing Staleness Check on Cross-Chain Rate Enables Unbounded agETH Over-Minting — (`contracts/cross-chain/CrossChainRateReceiver.sol`, `contracts/agETH/AGETHPoolV3.sol`)

## Summary

`CrossChainRateReceiver` records `lastUpdated` when a rate arrives via LayerZero but `getRate()` returns the stored `rate` unconditionally with no staleness validation. `AGETHPoolV3.deposit()` mints agETH using this unchecked rate, so any period during which `updateRate()` is not called allows users to mint agETH at an outdated (lower) rate, receiving more agETH per ETH than the protocol's backing supports, leading to protocol insolvency.

## Finding Description

The cross-chain rate pipeline is confirmed in the code:

1. `MultiChainRateProvider.updateRate()` is `external payable` with no access control — anyone may call it, but it requires the caller to supply ETH for LayerZero fees on every invocation. [1](#0-0) 

2. On the destination chain, `CrossChainRateReceiver.lzReceive()` stores the received rate and sets `lastUpdated = block.timestamp`. [2](#0-1) 

3. `CrossChainRateReceiver.getRate()` returns `rate` with **no staleness check** — `lastUpdated` is stored but never read in this function or by any caller in the minting path. [3](#0-2) 

4. `AGETHPoolV3.viewSwapAgETHAmountAndFee()` calls `getRate()` and computes `agETHAmount = amountAfterFee * 1e18 / agETHToETHrate` with no staleness guard. [4](#0-3) 

5. `AGETHPoolV3.deposit()` calls `viewSwapAgETHAmountAndFee()` and immediately mints agETH at the returned amount, with no circuit-breaker for a stale oracle. [5](#0-4) 

There is no `maxStaleness` parameter, no `paused` flag triggered by staleness, and no other guard anywhere in the minting path that reads `lastUpdated`. [6](#0-5) 

## Impact Explanation

agETH is a liquid restaking token whose ETH exchange rate increases monotonically as staking rewards accrue. When the stored rate is stale (lower than the true current rate), the minting formula produces a larger `agETHAmount` than the protocol's ETH backing supports. Every deposit during the stale window mints excess agETH, making the token progressively undercollateralized. Sustained staleness leads to **protocol insolvency** and eventual **permanent freezing of withdrawals** for honest holders — both Critical-class impacts under the allowed scope.

## Likelihood Explanation

`updateRate()` has no on-chain keeper, no heartbeat enforcement, and no automatic circuit-breaker. Realistic conditions that cause prolonged staleness include: the caller's ETH balance for LZ fees being depleted, a LayerZero outage, or the operator simply failing to call the function. Because agETH's rate drifts upward continuously, even a few weeks of staleness creates a meaningful over-minting window. The exploit requires no privileged access — any user calling `deposit()` during the stale window benefits, intentionally or not.

## Recommendation

Add a configurable `maxStaleness` parameter to `CrossChainRateReceiver` and revert in `getRate()` if the rate is stale:

```solidity
// CrossChainRateReceiver.sol
uint256 public maxStaleness; // e.g. 24 hours

function getRate() external view returns (uint256) {
    require(block.timestamp - lastUpdated <= maxStaleness, "Rate is stale");
    return rate;
}
```

This causes `AGETHPoolV3.deposit()` to revert automatically when the oracle is stale, protecting the protocol from over-minting without requiring changes to `AGETHPoolV3` itself.

## Proof of Concept

```solidity
// Fork test on destination chain (e.g. Arbitrum)
function test_staleRateCausesOverMint() external {
    // 1. Warp 30 days forward without calling updateRate()
    vm.warp(block.timestamp + 30 days);

    // 2. True agETH/ETH rate has increased (e.g. 1.05e18 -> 1.10e18)
    //    but CrossChainRateReceiver still holds the old 1.05e18 rate.
    uint256 storedRate = agETHRateReceiver.getRate(); // returns stale 1.05e18

    // 3. User deposits 1 ETH — deposit does NOT revert
    uint256 balanceBefore = agETH.balanceOf(user);
    vm.prank(user);
    agETHPoolV3.deposit{value: 1 ether}("ref");
    uint256 minted = agETH.balanceOf(user) - balanceBefore;

    // 4. User received ~0.952 agETH (1e18/1.05e18) instead of ~0.909 agETH (1e18/1.10e18)
    //    The excess ~0.043 agETH per ETH is unbacked — protocol is undercollateralized.
    assertGt(minted, 1 ether * 1e18 / 1.10e18);
}
```

### Citations

**File:** contracts/cross-chain/MultiChainRateProvider.sol (L108-113)
```text
    function updateRate() external payable nonReentrant {
        uint256 latestRate = getLatestRate();

        rate = latestRate;

        lastUpdated = block.timestamp;
```

**File:** contracts/cross-chain/CrossChainRateReceiver.sol (L16-16)
```text
    uint256 public lastUpdated;
```

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

**File:** contracts/agETH/AGETHPoolV3.sol (L115-128)
```text
    function deposit(string memory referralId) external payable nonReentrant {
        if (!isEthDepositEnabled) revert EthDepositDisabled();
        uint256 amount = msg.value;

        if (amount == 0) revert InvalidAmount();

        (uint256 agETHAmount, uint256 fee) = viewSwapAgETHAmountAndFee(amount);

        feeEarnedInETH += fee;

        agETH.mint(msg.sender, agETHAmount);

        emit SwapOccurred(msg.sender, agETHAmount, fee, referralId);
    }
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
