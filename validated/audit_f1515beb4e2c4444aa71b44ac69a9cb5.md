### Title
Stale Cross-Chain Oracle Rate Allows Excess wrsETH Minting on L2 - (`contracts/cross-chain/CrossChainRateReceiver.sol`)

### Summary

The L2 pool contracts (`RSETHPoolV3`, `RSETHPoolV3ExternalBridge`, `RSETHPoolV3WithNativeChainBridge`, `RSETHPoolNoWrapper`, etc.) mint `wrsETH` to depositors using a rate fetched from an `rsETHOracle`. On L2, this oracle is the `CrossChainRateReceiver` (deployed as `RSETHRateReceiver`), which stores the rsETH/ETH rate propagated from L1 via LayerZero. The `getRate()` function on `CrossChainRateReceiver` returns the stored rate with **no staleness check**, despite the contract tracking `lastUpdated`. Because rsETH is a yield-bearing token whose rate monotonically increases over time, a stale (lower-than-current) L2 rate causes the pools to mint **more wrsETH than the deposited ETH is worth at the current L1 rate**. An attacker can exploit this discrepancy to extract value from existing rsETH holders.

---

### Finding Description

The `CrossChainRateReceiver` stores the rsETH/ETH rate received from L1 via LayerZero and exposes it through `getRate()`:

```solidity
// contracts/cross-chain/CrossChainRateReceiver.sol
uint256 public rate;
uint256 public lastUpdated;

function lzReceive(...) external {
    ...
    rate = _rate;
    lastUpdated = block.timestamp;
    emit RateUpdated(_rate);
}

function getRate() external view returns (uint256) {
    return rate;  // No staleness check against lastUpdated
}
``` [1](#0-0) [2](#0-1) 

Every L2 pool calls `IOracle(rsETHOracle).getRate()` to determine how many wrsETH to mint per unit of ETH deposited:

```solidity
// contracts/pools/RSETHPoolV3.sol
function viewSwapRsETHAmountAndFee(uint256 amount) public view returns (uint256 rsETHAmount, uint256 fee) {
    fee = amount * feeBps / 10_000;
    uint256 amountAfterFee = amount - fee;
    uint256 rsETHToETHrate = getRate();
    rsETHAmount = amountAfterFee * 1e18 / rsETHToETHrate;
}
``` [3](#0-2) 

The same pattern is present in `RSETHPoolV3ExternalBridge`, `RSETHPoolV3WithNativeChainBridge`, `RSETHPoolNoWrapper`, and `RSETHPoolV2`: [4](#0-3) [5](#0-4) 

The `RSETHRateProvider` on L1 reads `ILRTOracle(rsETHPriceOracle).rsETHPrice()` and sends it to L2 via LayerZero: [6](#0-5) 

The rsETH/ETH rate is monotonically increasing (staking rewards accrue continuously). If the LayerZero message is delayed or the rate provider is not called for an extended period, the L2 oracle holds a **stale, lower-than-current rate**. Since `rsETHAmount = amountAfterFee * 1e18 / rsETHToETHrate`, a lower stale rate produces a **larger rsETHAmount** than the deposited ETH justifies at the current L1 rate.

---

### Impact Explanation

An attacker deposits ETH on L2 when the oracle is stale, receiving excess `wrsETH`. They bridge `wrsETH` to L1, unwrap to `rsETH`, and redeem via `LRTWithdrawalManager` for more ETH than they deposited. The surplus ETH is extracted from the protocol's TVL, diluting all existing rsETH holders. This constitutes **theft of unclaimed yield** (High) and, if the rate divergence is large enough (e.g., extended oracle outage), can approach **protocol insolvency** (Critical).

---

### Likelihood Explanation

LayerZero message delivery is not guaranteed to be instantaneous or continuous. The rate provider must be actively called to push updates; there is no on-chain enforcement of update frequency. During periods of network congestion, bridge downtime, or simply infrequent rate pushes, the L2 oracle can lag behind the L1 rate by hours or days. rsETH accrues ~4% APY in staking rewards, meaning a 7-day stale rate creates ~0.077% excess minting per deposit — exploitable repeatedly and at scale with no capital risk beyond gas.

---

### Recommendation

Add a staleness threshold check inside `CrossChainRateReceiver.getRate()`:

```solidity
uint256 public constant MAX_RATE_AGE = 24 hours;

function getRate() external view returns (uint256) {
    require(block.timestamp - lastUpdated <= MAX_RATE_AGE, "Rate is stale");
    return rate;
}
``` [2](#0-1) 

This causes all L2 pool deposits to revert when the oracle is stale, preventing excess minting until a fresh rate is received from L1.

---

### Proof of Concept

1. L1 rsETH/ETH rate is currently `1.05e18` (reflecting accumulated staking rewards).
2. The L2 `CrossChainRateReceiver` holds a stale rate of `1.04e18` (last updated 7 days ago; `lastUpdated` is never checked by `getRate()`).
3. Attacker calls `RSETHPoolV3.deposit{value: 100 ether}("ref")` on L2.
4. `viewSwapRsETHAmountAndFee(100 ether)` computes: `rsETHAmount = 100e18 * 1e18 / 1.04e18 ≈ 96.153 wrsETH`.
5. At the current L1 rate of `1.05e18`, fair wrsETH for 100 ETH is `100e18 * 1e18 / 1.05e18 ≈ 95.238 wrsETH`.
6. Attacker received `~0.915 wrsETH` excess (~0.96 ETH of value at L1 rate).
7. Attacker bridges wrsETH to L1, unwraps, and redeems via `LRTWithdrawalManager` for `~100.96 ETH`, netting `~0.96 ETH` profit at the expense of existing rsETH holders.
8. This is repeatable for as long as the oracle remains stale. [7](#0-6) [8](#0-7)

### Citations

**File:** contracts/cross-chain/CrossChainRateReceiver.sol (L13-17)
```text
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

**File:** contracts/pools/RSETHPoolV3.sol (L246-265)
```text
    function deposit(string memory referralId)
        external
        payable
        nonReentrant
        whenNotPaused
        limitDailyMint(msg.value, ETH_IDENTIFIER)
    {
        if (!isEthDepositEnabled) revert EthDepositDisabled();
        uint256 amount = msg.value;

        if (amount == 0) revert InvalidAmount();

        (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount);

        feeEarnedInETH += fee;

        wrsETH.mint(msg.sender, rsETHAmount);

        emit SwapOccurred(msg.sender, rsETHAmount, fee, referralId);
    }
```

**File:** contracts/pools/RSETHPoolV3.sol (L299-308)
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

**File:** contracts/pools/RSETHPoolV3ExternalBridge.sol (L418-427)
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

**File:** contracts/pools/RSETHPoolNoWrapper.sol (L277-286)
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

**File:** contracts/cross-chain/RSETHRateProvider.sol (L27-29)
```text
    function getLatestRate() public view override returns (uint256) {
        return ILRTOracle(rsETHPriceOracle).rsETHPrice();
    }
```
