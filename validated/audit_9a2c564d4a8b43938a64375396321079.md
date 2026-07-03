### Title
Stale Cross-Chain rsETH/ETH Rate Used for L2 Minting Without Freshness Check — (`contracts/cross-chain/CrossChainRateReceiver.sol`)

---

### Summary

All L2 pool contracts (`RSETHPoolV3`, `RSETHPoolNoWrapper`, `RSETHPoolV3ExternalBridge`, `RSETHPoolV3WithNativeChainBridge`, `RSETHPool`, `RSETHPoolV2ExternalBridge`) compute the amount of rsETH/wrsETH to mint by dividing the deposited ETH value by the rsETH/ETH rate returned from `CrossChainRateReceiver.getRate()`. That function returns a stored `rate` value with no staleness check against `lastUpdated`. Because rsETH is a yield-bearing token whose price monotonically increases on L1, any delay in cross-chain rate propagation means the L2 oracle always lags behind the true rate. A depositor who acts during a staleness window receives more rsETH than the deposited ETH warrants, diluting all existing rsETH holders.

---

### Finding Description

The `CrossChainRateReceiver` contract stores the rsETH/ETH rate received via LayerZero and exposes it through `getRate()`:

```solidity
// contracts/cross-chain/CrossChainRateReceiver.sol
uint256 public rate;
uint256 public lastUpdated;

function getRate() external view returns (uint256) {
    return rate;   // ← no staleness check against lastUpdated
}
``` [1](#0-0) [2](#0-1) 

The rate is only updated when a LayerZero message arrives from L1 via `lzReceive()`, which is triggered by someone calling `updateRate()` on the L1 provider and paying the cross-chain gas fee: [3](#0-2) 

Every L2 pool contract calls `getRate()` to determine how many rsETH tokens to mint:

```solidity
// RSETHPoolV3.sol (and all other pool variants)
uint256 rsETHToETHrate = getRate();
rsETHAmount = amountAfterFee * 1e18 / rsETHToETHrate;
``` [4](#0-3) [5](#0-4) 

Because rsETH is a yield-bearing token, its price on L1 (`LRTOracle.rsETHPrice`) only increases over time as staking rewards accrue. The L2 oracle rate therefore always lags the true L1 rate whenever `updateRate()` has not been called recently. A lower stale rate in the denominator produces a larger `rsETHAmount` for the same ETH input — i.e., over-minting.

The `ChainlinkOracleForRSETHPoolCollateral` used for collateral token pricing does implement staleness checks (`answeredInRound < roundID`), confirming the team is aware of the pattern but did not apply it to the cross-chain rate path: [6](#0-5) 

---

### Impact Explanation

When the L2 oracle rate is stale and lower than the true L1 rsETH price, every depositor on L2 receives more rsETH than the deposited ETH is worth. The excess rsETH is backed by no additional ETH in the protocol. This dilutes the ETH-per-rsETH ratio for all existing holders, constituting a direct theft of accrued yield from every current rsETH holder. The magnitude scales with the size of the deposit and the degree of staleness. A sophisticated actor who monitors the staleness window and deposits large amounts can extract meaningful value.

**Impact**: High — Theft of unclaimed yield from all existing rsETH holders.

---

### Likelihood Explanation

The `updateRate()` call on the L1 provider is permissionless but requires the caller to pay LayerZero cross-chain gas fees. There is no on-chain enforcement that it must be called within any time bound. During periods of high L1 gas prices, network congestion, or simply when no keeper is running, the rate can remain stale for hours or days. Because rsETH yield accrues continuously, even a few hours of staleness creates a profitable window. The attack requires no special permissions — any L2 depositor can exploit it.

**Likelihood**: Medium.

---

### Recommendation

Add a configurable maximum staleness threshold to `CrossChainRateReceiver.getRate()` and revert if `block.timestamp - lastUpdated` exceeds it:

```solidity
uint256 public maxStaleness; // e.g., 24 hours

function getRate() external view returns (uint256) {
    if (block.timestamp - lastUpdated > maxStaleness) revert StaleRate();
    return rate;
}
```

Alternatively, the L2 pool contracts themselves should check `lastUpdated` before using the rate. The staleness bound should be set conservatively (e.g., 24 hours) and paired with an automated keeper that calls `updateRate()` on a regular cadence.

---

### Proof of Concept

1. At time T, the true L1 rsETH price is 1.05 ETH/rsETH. The L2 `CrossChainRateReceiver.rate` was last updated 48 hours ago at 1.03 ETH/rsETH. No one has called `updateRate()` since.
2. Attacker calls `deposit{value: 1050 ETH}()` on `RSETHPoolNoWrapper` (or any L2 pool).
3. `viewSwapRsETHAmountAndFee(1050 ETH)` calls `getRate()` → returns stale `1.03e18`.
4. `rsETHAmount = 1050e18 * 1e18 / 1.03e18 ≈ 1019.4 rsETH` (correct at true rate: `1050/1.05 = 1000 rsETH`).
5. Attacker receives ~19.4 rsETH more than the deposited ETH warrants.
6. Attacker bridges rsETH to L1 and redeems at the true rate of 1.05 ETH/rsETH, extracting ~20.4 ETH of value that was diluted from existing holders. [7](#0-6) [8](#0-7) [9](#0-8)

### Citations

**File:** contracts/cross-chain/CrossChainRateReceiver.sol (L13-17)
```text
    uint256 public rate;

    /// @notice Last time rate was updated
    uint256 public lastUpdated;

```

**File:** contracts/cross-chain/CrossChainRateReceiver.sol (L93-105)
```text
        uint256 _rate = abi.decode(_payload, (uint256));

        rate = _rate;

        lastUpdated = block.timestamp;

        emit RateUpdated(_rate);
    }

    /// @notice Gets the last stored rate in the contract
    function getRate() external view returns (uint256) {
        return rate;
    }
```

**File:** contracts/cross-chain/MultiChainRateProvider.sol (L108-137)
```text
    function updateRate() external payable nonReentrant {
        uint256 latestRate = getLatestRate();

        rate = latestRate;

        lastUpdated = block.timestamp;

        bytes memory _payload = abi.encode(latestRate);

        uint256 rateReceiversLength = rateReceivers.length;

        for (uint256 i; i < rateReceiversLength;) {
            uint16 dstChainId = uint16(rateReceivers[i]._chainId);

            bytes memory remoteAndLocalAddresses = abi.encodePacked(rateReceivers[i]._contract, address(this));

            (uint256 estimatedFee,) = ILayerZeroEndpoint(layerZeroEndpoint)
                .estimateFees(dstChainId, address(this), _payload, false, bytes(""));

            ILayerZeroEndpoint(layerZeroEndpoint).send{ value: estimatedFee }(
                dstChainId, remoteAndLocalAddresses, _payload, payable(msg.sender), address(0x0), bytes("")
            );

            unchecked {
                ++i;
            }
        }

        emit RateUpdated(rate);
    }
```

**File:** contracts/pools/RSETHPoolV3.sol (L303-307)
```text
        // rate of rsETH in ETH
        uint256 rsETHToETHrate = getRate();

        // Calculate the final rsETH amount
        rsETHAmount = amountAfterFee * 1e18 / rsETHToETHrate;
```

**File:** contracts/pools/RSETHPoolNoWrapper.sol (L231-244)
```text
    function deposit(string memory referralId) external payable nonReentrant whenNotPaused {
        if (!isEthDepositEnabled) revert EthDepositDisabled();
        uint256 amount = msg.value;

        if (amount == 0) revert InvalidAmount();

        (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount);

        feeEarnedInETH += fee;

        rsETH.safeTransfer(msg.sender, rsETHAmount);

        emit SwapOccurred(msg.sender, rsETHAmount, fee, referralId);
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
