### Title
Missing Staleness Check in `CrossChainRateReceiver.getRate()` Allows Stale Rate to Price Pool Swaps — (`contracts/cross-chain/CrossChainRateReceiver.sol`)

---

### Summary

`CrossChainRateReceiver.getRate()` returns the last stored `rate` with no check against `lastUpdated`. When LayerZero message delivery stops (relayer outage, insufficient fee, or simply no one calling `updateRate()`), the stored rate silently ages. Every pool contract that points its `rsETHOracle` at a `CrossChainRateReceiver` (e.g. `RSETHRateReceiver`) will price all subsequent deposits using an arbitrarily old rsETH/ETH rate.

---

### Finding Description

`CrossChainRateReceiver` stores two state variables: `rate` and `lastUpdated`. [1](#0-0) 

`lastUpdated` is written only inside `lzReceive()` when a cross-chain message arrives: [2](#0-1) 

`getRate()` returns `rate` unconditionally — `lastUpdated` is never read: [3](#0-2) 

Every pool variant delegates its oracle call to this function: [4](#0-3) 

That rate is then used directly to compute how many rsETH tokens a depositor receives: [5](#0-4) 

The same pattern appears in every pool variant (`RSETHPool`, `RSETHPoolV3`, `RSETHPoolV3ExternalBridge`, `RSETHPoolV3WithNativeChainBridge`, `RSETHPoolNoWrapper`). [6](#0-5) 

By contrast, the protocol's Chainlink wrapper **does** implement a staleness check, confirming the team is aware of the pattern: [7](#0-6) 

---

### Impact Explanation

rsETH is a liquid restaking token whose ETH-denominated rate increases monotonically as staking rewards accrue. A stale (lower) rate causes the division `amountAfterFee * 1e18 / rsETHToETHrate` to yield a **larger** rsETH amount than the depositor is entitled to at the current rate. Depositors who transact during a stale-rate window receive more rsETH than their ETH is worth at the true current rate, diluting existing holders. The pool continues to accept deposits and mint tokens throughout the outage with no revert or warning.

Impact: **Low — contract fails to deliver promised returns** (incorrect swap pricing; no direct ETH loss from the pool's ETH balance, but rsETH is over-issued relative to the true current rate).

---

### Likelihood Explanation

The trigger requires `lzReceive` to go uncalled for an extended period. This is realistic:

- LayerZero relayer outages have occurred on live networks.
- `updateRate()` on the provider side is permissionless but requires a caller to pay the cross-chain fee; if no one calls it, the rate ages indefinitely.
- The contract provides no circuit-breaker, no maximum staleness window, and no revert path.

No admin compromise or governance capture is required.

---

### Recommendation

Add a configurable `maxStaleness` parameter and revert in `getRate()` if `block.timestamp - lastUpdated > maxStaleness`:

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

This mirrors the staleness guard already present in `ChainlinkOracleForRSETHPoolCollateral`. [8](#0-7) 

---

### Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.0;

// Fork test (local fork, no public-mainnet execution)
// 1. Deploy RSETHRateReceiver with a mock LayerZero endpoint.
// 2. Simulate lzReceive with rate = 1.05e18 (rsETH/ETH) at T=0.
// 3. Warp block.timestamp forward by 7 days.
// 4. Call getRate() — returns 1.05e18 (stale).
// 5. Fetch current LRTOracle.rsETHPrice() — returns e.g. 1.07e18.
// 6. Call RSETHPoolV2.viewSwapRsETHAmountAndFee(1 ether):
//      stale:   rsETHAmount = 1e18 * 1e18 / 1.05e18 ≈ 0.9524 rsETH
//      current: rsETHAmount = 1e18 * 1e18 / 1.07e18 ≈ 0.9346 rsETH
//      delta:   ~0.0178 rsETH over-issued per ETH deposited (~1.9%)
// 7. Assert getRate() == 1.05e18 and lastUpdated == T=0 (7 days stale).
// 8. Assert pool output diverges from expected output at current oracle price.
```

The test requires no privileged access — only a time warp after a normal `lzReceive` call, followed by a public `deposit()`.

### Citations

**File:** contracts/cross-chain/CrossChainRateReceiver.sol (L12-17)
```text
    /// @notice Last rate updated on the receiver
    uint256 public rate;

    /// @notice Last time rate was updated
    uint256 public lastUpdated;

```

**File:** contracts/cross-chain/CrossChainRateReceiver.sol (L82-100)
```text
    function lzReceive(uint16 _srcChainId, bytes memory _srcAddress, uint64, bytes calldata _payload) external {
        require(msg.sender == layerZeroEndpoint, "Sender should be lz endpoint");

        address srcAddress;
        assembly {
            srcAddress := mload(add(_srcAddress, 20))
        }

        require(_srcChainId == srcChainId, "Src chainId must be correct");
        require(srcAddress == rateProvider, "Src address must be provider");

        uint256 _rate = abi.decode(_payload, (uint256));

        rate = _rate;

        lastUpdated = block.timestamp;

        emit RateUpdated(_rate);
    }
```

**File:** contracts/cross-chain/CrossChainRateReceiver.sol (L102-105)
```text
    /// @notice Gets the last stored rate in the contract
    function getRate() external view returns (uint256) {
        return rate;
    }
```

**File:** contracts/pools/RSETHPoolV2.sol (L200-203)
```text
    /// @dev Gets the rate from the rsETHOracle
    function getRate() public view returns (uint256) {
        return IOracle(rsETHOracle).getRate();
    }
```

**File:** contracts/pools/RSETHPoolV2.sol (L225-234)
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

**File:** contracts/pools/oracle/ChainlinkOracleForRSETHPoolCollateral.sol (L26-37)
```text
    function getRate() public view returns (uint256) {
        (uint80 roundID, int256 ethPrice,, uint256 timestamp, uint80 answeredInRound) =
            AggregatorV3Interface(oracle).latestRoundData();

        if (answeredInRound < roundID) revert StalePrice();
        if (timestamp == 0) revert IncompleteRound();
        if (ethPrice <= 0) revert InvalidPrice();

        uint256 normalizedPrice = uint256(ethPrice) * 1e18 / 10 ** uint256(AggregatorV3Interface(oracle).decimals());

        return normalizedPrice;
    }
```
