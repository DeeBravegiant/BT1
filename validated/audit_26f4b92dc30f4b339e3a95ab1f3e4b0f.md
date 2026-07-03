Audit Report

## Title
Stale Cross-Chain Rate Used for agETH Minting With No Freshness Enforcement — (`contracts/cross-chain/CrossChainRateReceiver.sol`, `contracts/agETH/AGETHPoolV3.sol`)

## Summary
`CrossChainRateReceiver.getRate()` returns the last stored `rate` unconditionally; `lastUpdated` is recorded on every `lzReceive` call but is never validated against a freshness window. `AGETHPoolV3.deposit()` uses this potentially stale rate to compute and mint agETH, causing excess agETH to be minted when the stored rate lags behind the true accrued rate, inflating supply and diluting existing holders.

## Finding Description
`CrossChainRateReceiver.lzReceive()` stores both `rate` and `lastUpdated` on every cross-chain message: [1](#0-0) 

`getRate()` returns `rate` with no reference to `lastUpdated`: [2](#0-1) 

`AGETHPoolV3.getRate()` delegates directly to `IOracle(agETHOracle).getRate()` with no additional guard: [3](#0-2) 

`AGETHPoolV3.viewSwapAgETHAmountAndFee()` uses this rate to compute the mint amount: [4](#0-3) 

`deposit()` calls `viewSwapAgETHAmountAndFee()` and mints the result directly: [5](#0-4) 

`updateRate()` on the provider is permissionless but has no on-chain heartbeat or enforcement: [6](#0-5) 

Since agETH is yield-bearing, its ETH-denominated rate increases over time. If the stored rate `R` is stale while the true rate is `R' > R`, then `agETHAmount = amountAfterFee * 1e18 / R` yields more agETH than `amountAfterFee * 1e18 / R'`, creating unbacked supply.

## Impact Explanation
**Low — Contract fails to deliver promised returns, but doesn't lose value.** The pool retains all deposited ETH, but the agETH supply is inflated beyond its backing. Existing agETH holders are diluted: when they redeem, the ETH-per-agETH ratio is lower than it should be, meaning the contract fails to deliver the promised yield-bearing returns. No direct theft of principal occurs.

## Likelihood Explanation
`updateRate()` requires an off-chain caller to pay LayerZero fees and trigger the cross-chain message. There is no on-chain keeper, heartbeat, or circuit-breaker. Any operational gap — missed bot calls, fee shortfall, bridge congestion, or network issues — leaves the rate stale indefinitely. The discrepancy grows continuously as agETH yield accrues, and any unprivileged depositor can exploit the window simply by calling `deposit()`.

## Recommendation
Add a staleness check in `CrossChainRateReceiver.getRate()`:

```solidity
uint256 public constant MAX_RATE_AGE = 1 days;

function getRate() external view returns (uint256) {
    require(block.timestamp - lastUpdated <= MAX_RATE_AGE, "Rate is stale");
    return rate;
}
```

Alternatively, expose `lastUpdated` in the return value so `AGETHPoolV3` can enforce its own freshness policy independently.

## Proof of Concept

```solidity
function test_staleRate() public {
    // 1. Simulate lzReceive with rate 1.05e18 at T0
    vm.prank(layerZeroEndpoint);
    receiver.lzReceive(
        srcChainId,
        abi.encodePacked(rateProvider, address(receiver)),
        0,
        abi.encode(1.05e18)
    );

    // 2. Advance 7 days — no further updateRate() calls
    vm.warp(block.timestamp + 7 days);

    // 3. getRate() returns stale rate with no revert
    assertEq(receiver.getRate(), 1.05e18);
    assertEq(block.timestamp - receiver.lastUpdated(), 7 days);

    // 4. True rate after 7 days of yield accrual would be ~1.06e18
    // Stale:   1e18 * 1e18 / 1.05e18 = 952380952380952380 agETH (too many)
    // Correct: 1e18 * 1e18 / 1.06e18 = 943396226415094339 agETH
    // Excess:  ~8984725965858041 agETH minted unbacked per 1 ETH deposited
}
```

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

**File:** contracts/agETH/AGETHPoolV3.sol (L104-106)
```text
    function getRate() public view returns (uint256) {
        return IOracle(agETHOracle).getRate();
    }
```

**File:** contracts/agETH/AGETHPoolV3.sol (L121-125)
```text
        (uint256 agETHAmount, uint256 fee) = viewSwapAgETHAmountAndFee(amount);

        feeEarnedInETH += fee;

        agETH.mint(msg.sender, agETHAmount);
```

**File:** contracts/agETH/AGETHPoolV3.sol (L165-168)
```text
        uint256 agETHToETHrate = getRate();

        // Calculate the final agETH amount
        agETHAmount = amountAfterFee * 1e18 / agETHToETHrate;
```

**File:** contracts/cross-chain/CrossChainRateProvider.sol (L85-101)
```text
    function updateRate() external payable nonReentrant {
        uint256 latestRate = getLatestRate();

        bytes memory remoteAndLocalAddresses = abi.encodePacked(rateReceiver, address(this));

        rate = latestRate;

        lastUpdated = block.timestamp;

        bytes memory _payload = abi.encode(latestRate);

        ILayerZeroEndpoint(layerZeroEndpoint).send{ value: msg.value }(
            dstChainId, remoteAndLocalAddresses, _payload, payable(msg.sender), address(0x0), bytes("")
        );

        emit RateUpdated(rate);
    }
```
