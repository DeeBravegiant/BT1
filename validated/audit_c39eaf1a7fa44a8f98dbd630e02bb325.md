Audit Report

## Title
Stale Cross-Chain Rate in `CrossChainRateReceiver.getRate()` Enables Over-Minting of agETH — (`contracts/cross-chain/CrossChainRateReceiver.sol` / `contracts/agETH/AGETHPoolV3.sol`)

## Summary
`CrossChainRateReceiver` stores `lastUpdated` on every `lzReceive` call but never reads it back in `getRate()`, which unconditionally returns the last stored `rate`. `AGETHPoolV3.deposit()` calls `viewSwapAgETHAmountAndFee()` → `getRate()` with no freshness guard, so any depositor transacting while the cross-chain rate is stale receives more agETH than the deposited ETH backs at the current L1 rate.

## Finding Description
`CrossChainRateReceiver.lzReceive()` writes both `rate` and `lastUpdated` on receipt of a LayerZero message: [1](#0-0) 

`getRate()` returns only `rate` with no staleness check: [2](#0-1) 

`AGETHRateReceiver` is a thin wrapper that adds no additional validation: [3](#0-2) 

`AGETHPoolV3.deposit()` calls `viewSwapAgETHAmountAndFee()` unconditionally: [4](#0-3) 

`viewSwapAgETHAmountAndFee()` uses the stale rate directly in the mint calculation: [5](#0-4) 

The formula `agETHAmount = amountAfterFee * 1e18 / agETHToETHrate` means a stale (lower) rate produces a larger `agETHAmount`. No existing check in `AGETHPoolV3` or `CrossChainRateReceiver` compares `lastUpdated` against any threshold before minting.

## Impact Explanation
**Low — Contract fails to deliver promised returns, but doesn't lose value.** Deposited ETH remains in the pool, but the agETH supply on L2 is inflated beyond what the bridged ETH backs at the true current rate. The full-backing invariant is violated for the duration of any LZ relay gap. Existing agETH holders are diluted by the over-issued supply.

## Likelihood Explanation
LayerZero relay delays are a normal operational condition (network congestion, relayer downtime, gas spikes). agETH accrues yield continuously on L1, so its rate increases monotonically; any gap between `lzReceive` calls is a window where the stored rate is below the true rate. No special privileges are required — any unprivileged depositor calling `deposit()` during such a window automatically receives the over-minted amount.

## Recommendation
Add a staleness threshold check in `CrossChainRateReceiver.getRate()`:

```solidity
uint256 public constant MAX_RATE_AGE = 24 hours;

function getRate() external view returns (uint256) {
    require(block.timestamp - lastUpdated <= MAX_RATE_AGE, "Rate is stale");
    return rate;
}
```

This causes `deposit()` to revert when the oracle is stale, preventing over-minting until a fresh LZ message arrives.

## Proof of Concept
```solidity
// Fork test on L2 (e.g. Arbitrum)
function testStaleRateOverMint() public {
    // Simulate stale rate: last lzReceive set rate = 1.01e18, true rate = 1.02e18
    vm.store(address(rateReceiver), RATE_SLOT, bytes32(uint256(1.01e18)));
    vm.store(address(rateReceiver), LAST_UPDATED_SLOT, bytes32(block.timestamp - 7 days));

    uint256 depositAmount = 1 ether;
    uint256 agETHBefore = agETH.balanceOf(alice);

    vm.prank(alice);
    pool.deposit{value: depositAmount}("ref");

    uint256 agETHReceived = agETH.balanceOf(alice) - agETHBefore;
    uint256 correctAmount = depositAmount * 1e18 / 1.02e18;

    // agETHReceived > correctAmount — over-minted
    assertGt(agETHReceived, correctAmount);
}
```

### Citations

**File:** contracts/cross-chain/CrossChainRateReceiver.sol (L95-97)
```text
        rate = _rate;

        lastUpdated = block.timestamp;
```

**File:** contracts/cross-chain/CrossChainRateReceiver.sol (L102-105)
```text
    /// @notice Gets the last stored rate in the contract
    function getRate() external view returns (uint256) {
        return rate;
    }
```

**File:** contracts/agETH/AGETHRateReceiver.sol (L9-15)
```text
contract AGETHRateReceiver is CrossChainRateReceiver {
    constructor(uint16 _srcChainId, address _rateProvider, address _layerZeroEndpoint) {
        rateInfo = RateInfo({ tokenSymbol: "agETH", baseTokenSymbol: "ETH" });
        srcChainId = _srcChainId;
        rateProvider = _rateProvider;
        layerZeroEndpoint = _layerZeroEndpoint;
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

**File:** contracts/agETH/AGETHPoolV3.sol (L160-169)
```text
    function viewSwapAgETHAmountAndFee(uint256 amount) public view returns (uint256 agETHAmount, uint256 fee) {
        fee = amount * feeBps / 10_000;
        uint256 amountAfterFee = amount - fee;

        // rate of agETH in ETH
        uint256 agETHToETHrate = getRate();

        // Calculate the final agETH amount
        agETHAmount = amountAfterFee * 1e18 / agETHToETHrate;
    }
```
