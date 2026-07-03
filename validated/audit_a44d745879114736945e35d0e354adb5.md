Audit Report

## Title
Stale Cross-Chain Rate Used Without Staleness Validation Allows Over-Minting of wrsETH - (File: contracts/cross-chain/CrossChainRateReceiver.sol)

## Summary
`CrossChainRateReceiver` stores both `rate` and `lastUpdated` on every LayerZero message receipt, but `getRate()` returns `rate` unconditionally without consulting `lastUpdated`. All L2 deposit pools use this unchecked rate to compute how many wrsETH tokens to mint per unit of deposited ETH or collateral, so any delay in LayerZero message delivery leaves a stale (lower) rate in place that any depositor can exploit to receive excess wrsETH at the expense of existing holders' accrued yield.

## Finding Description
`CrossChainRateReceiver` declares both `rate` and `lastUpdated` as public state variables: [1](#0-0) 

`lzReceive` writes both fields on every inbound LayerZero message: [2](#0-1) 

`getRate()` ignores `lastUpdated` entirely and returns the stored rate unconditionally: [3](#0-2) 

`RSETHRateReceiver` extends this base contract and adds no staleness check of its own: [4](#0-3) 

`RSETHPool.viewSwapRsETHAmountAndFee` calls `getRate()` and uses the result as the denominator to compute the wrsETH mint amount: [5](#0-4) 

The public `deposit()` function calls `viewSwapRsETHAmountAndFee` and immediately transfers the computed wrsETH amount to the caller: [6](#0-5) 

The only existing guard checks that the rate is non-zero at oracle registration time, not at read time: [7](#0-6) 

The same pattern is present in `RSETHPoolV3`, `RSETHPoolNoWrapper`, and the other pool variants, all of which call `IOracle(rsETHOracle).getRate()` with no freshness check.

## Impact Explanation
rsETH is a yield-bearing token whose ETH value increases monotonically. A stale rate is always lower than the true current rate. Because `rsETHAmount = amountAfterFee * 1e18 / rsETHToETHrate`, a lower denominator produces a larger mint amount. Every deposit made against a stale rate mints more wrsETH than the depositor's ETH entitles them to, diluting the share of accrued yield belonging to all existing wrsETH holders. This is a concrete, repeatable **theft of unclaimed yield** (High impact) requiring no special privilege — only a normal `deposit()` call.

## Likelihood Explanation
LayerZero message delivery is not instantaneous or guaranteed. Relayer downtime, network congestion, or a gap between rate-provider update cadences can leave `lastUpdated` hours or days behind the true on-chain rate. rsETH accrues yield continuously, so even a modest delay (e.g., 24–48 hours) creates a measurable and profitable arbitrage window. Any unprivileged depositor monitoring the `lastUpdated` timestamp can identify and exploit this window by simply calling `deposit()` on any active L2 pool. The condition is realistic, requires no capital beyond the deposit itself, and is repeatable across all deployed pool contracts.

## Recommendation
Add a configurable `maxStaleness` parameter to `CrossChainRateReceiver` and enforce it in `getRate()`:

```solidity
uint256 public maxStaleness;

error StaleRate();

function getRate() external view returns (uint256) {
    if (block.timestamp - lastUpdated > maxStaleness) revert StaleRate();
    return rate;
}
```

Set `maxStaleness` conservatively relative to the LayerZero send cadence for each deployed receiver. Document the expected update frequency and ensure monitoring alerts fire before the threshold is breached.

## Proof of Concept
1. Deploy `RSETHRateReceiver` and `RSETHPool` on a local Arbitrum fork. Configure the pool with the receiver as `rsETHOracle`.
2. Simulate a LayerZero message delivering `rate = 1.05e18` at time `T` (`lastUpdated = T`).
3. Advance the fork clock by 48 hours (`vm.warp(T + 48 hours)`). Do not deliver a new LayerZero message.
4. Call `RSETHPool.deposit{value: 1 ether}("ref")` from an unprivileged address.
5. Assert that `getRate()` returns `1.05e18` (stale) rather than the current rate (e.g., `1.06e18`).
6. Assert that the caller received `≈ 0.952 wrsETH` instead of the correct `≈ 0.943 wrsETH` — approximately 0.009 excess wrsETH per ETH deposited, extracted from existing holders' accrued yield.
7. Repeat step 4 in a loop (fuzz over deposit amounts) to confirm the over-mint scales linearly with deposit size and with the magnitude of rate staleness.

### Citations

**File:** contracts/cross-chain/CrossChainRateReceiver.sol (L12-16)
```text
    /// @notice Last rate updated on the receiver
    uint256 public rate;

    /// @notice Last time rate was updated
    uint256 public lastUpdated;
```

**File:** contracts/cross-chain/CrossChainRateReceiver.sol (L93-99)
```text
        uint256 _rate = abi.decode(_payload, (uint256));

        rate = _rate;

        lastUpdated = block.timestamp;

        emit RateUpdated(_rate);
```

**File:** contracts/cross-chain/CrossChainRateReceiver.sol (L102-105)
```text
    /// @notice Gets the last stored rate in the contract
    function getRate() external view returns (uint256) {
        return rate;
    }
```

**File:** contracts/cross-chain/RSETHRateReceiver.sol (L9-15)
```text
contract RSETHRateReceiver is CrossChainRateReceiver {
    constructor(uint16 _srcChainId, address _rateProvider, address _layerZeroEndpoint) {
        rateInfo = RateInfo({ tokenSymbol: "rsETH", baseTokenSymbol: "ETH" });
        srcChainId = _srcChainId;
        rateProvider = _rateProvider;
        layerZeroEndpoint = _layerZeroEndpoint;
    }
```

**File:** contracts/pools/RSETHPool.sol (L265-278)
```text
    function deposit(string memory referralId) external payable nonReentrant whenNotPaused {
        if (!isEthDepositEnabled) revert EthDepositDisabled();
        uint256 amount = msg.value;

        if (amount == 0) revert InvalidAmount();

        (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount);

        feeEarnedInETH += fee;

        IERC20(address(wrsETH)).safeTransfer(msg.sender, rsETHAmount);

        emit SwapOccurred(msg.sender, rsETHAmount, fee, referralId);
    }
```

**File:** contracts/pools/RSETHPool.sol (L311-320)
```text
    function viewSwapRsETHAmountAndFee(uint256 amount) public view returns (uint256 rsETHAmount, uint256 fee) {
        fee = amount * feeBps / 10_000;
        uint256 amountAfterFee = amount - fee;

        // rate of rsETH in ETH
        uint256 rsETHToETHrate = getRate();

        // Calculate the final rsETH amount
        rsETHAmount = amountAfterFee * 1e18 / rsETHToETHrate;
    }
```

**File:** contracts/pools/RSETHPool.sol (L648-650)
```text
        if (IOracle(oracle).getRate() == 0) {
            revert UnsupportedOracle();
        }
```
