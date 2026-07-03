### Title
Stale Cross-Chain rsETH/ETH Rate Enables Over-Minting of rsETH on L2 Pools — (File: contracts/cross-chain/CrossChainRateReceiver.sol)

---

### Summary

The `CrossChainRateReceiver` stores the rsETH/ETH exchange rate received via LayerZero from L1 but exposes it through `getRate()` with **no staleness check**. All L2 deposit pools (`RSETHPoolV3`, `RSETHPoolNoWrapper`, `RSETHPoolV3ExternalBridge`, `RSETHPoolV3WithNativeChainBridge`) use this rate to compute how many rsETH tokens to mint per ETH deposited. When the rate is stale (i.e., rsETH has appreciated on L1 but the L2 oracle has not been updated), depositors receive more rsETH than their ETH contribution warrants, diluting existing rsETH holders' accrued yield.

---

### Finding Description

The `CrossChainRateReceiver` contract records both the rate and the time it was last updated: [1](#0-0) 

The `lzReceive` callback sets `lastUpdated = block.timestamp` when a new rate arrives from L1: [2](#0-1) 

However, `getRate()` returns the stored `rate` unconditionally, with no check against `lastUpdated`: [3](#0-2) 

All L2 pools call `IOracle(rsETHOracle).getRate()` inside `viewSwapRsETHAmountAndFee` to determine the rsETH mint amount: [4](#0-3) 

The formula is:

```
rsETHAmount = amountAfterFee * 1e18 / rsETHToETHrate
```

A **lower** (stale) rate produces a **larger** rsETHAmount. Since rsETH appreciates monotonically as staking rewards accrue on L1, any delay in propagating the updated rate to L2 creates a window where depositors receive more rsETH than their ETH is worth at the current L1 price.

The rate update mechanism (`MultiChainRateProvider.updateRate()`) is permissionless but requires the caller to pay LayerZero fees: [5](#0-4) 

There is no on-chain enforcement that `updateRate()` is called within any maximum interval. The `lastUpdated` field is purely informational and is never read by any consumer.

---

### Impact Explanation

**High — Theft of unclaimed yield.**

rsETH is a yield-bearing token: its ETH-denominated value increases over time as EigenLayer staking rewards accrue. When a depositor mints rsETH using a stale (lower) rate, they receive more rsETH shares than their ETH contribution justifies at the true current rate. This excess dilutes the share of accrued yield belonging to all existing rsETH holders. The attacker does not need to do anything beyond depositing ETH during a staleness window; the protocol itself over-mints on their behalf.

---

### Likelihood Explanation

**Medium.** LayerZero cross-chain message delivery is not instantaneous and is subject to network conditions. The `updateRate()` call requires the caller to pay native gas fees on L1 and LayerZero relay fees; there is no keeper or on-chain incentive to call it frequently. In practice, rate updates are expected to be periodic (e.g., daily or on-demand), meaning staleness windows of hours to days are realistic. During any such window, any depositor on any L2 pool automatically benefits from the stale rate.

---

### Recommendation

Add a configurable maximum staleness threshold to `CrossChainRateReceiver` and revert (or return a sentinel value) in `getRate()` if `block.timestamp - lastUpdated` exceeds that threshold:

```solidity
uint256 public maxStaleness; // e.g., 24 hours

function getRate() external view returns (uint256) {
    require(block.timestamp - lastUpdated <= maxStaleness, "Rate is stale");
    return rate;
}
```

This causes all L2 pool deposits to revert when the oracle is stale, preventing over-minting until the rate is refreshed. Pair this with an automated keeper or on-chain incentive to call `updateRate()` regularly.

---

### Proof of Concept

1. At time T=0, L1 rsETH price is 1.05 ETH/rsETH. The L2 `CrossChainRateReceiver` holds `rate = 1.05e18`, `lastUpdated = T`.
2. Staking rewards accrue on L1. At T=48h, the true rsETH price is 1.06 ETH/rsETH, but no one has called `updateRate()`. The L2 oracle still returns `1.05e18`.
3. Attacker deposits 100 ETH into `RSETHPoolV3.deposit()` on L2.
4. `viewSwapRsETHAmountAndFee(100e18)` computes: `rsETHAmount = 100e18 * 1e18 / 1.05e18 ≈ 95.238 rsETH`.
5. At the true rate of 1.06, the attacker should have received: `100e18 / 1.06e18 ≈ 94.340 rsETH`.
6. The attacker receives ~0.898 rsETH more than they are entitled to, at the expense of existing holders' accrued yield. Scaled to the daily mint limit, this excess compounds across every depositor during the staleness window. [6](#0-5) [3](#0-2)

### Citations

**File:** contracts/cross-chain/CrossChainRateReceiver.sol (L13-16)
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
