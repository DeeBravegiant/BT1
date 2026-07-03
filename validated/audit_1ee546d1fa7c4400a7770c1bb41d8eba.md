### Title
No Staleness Check on Cross-Chain Rate Allows Excess wrsETH Minting During LayerZero Network Failures - (`contracts/cross-chain/CrossChainRateReceiver.sol`)

---

### Summary

`CrossChainRateReceiver.getRate()` returns the stored `rate` with no staleness validation. All L2 deposit pools consume this rate to compute how many wrsETH tokens to mint per ETH deposited. During a LayerZero outage or congestion event, the on-chain rate becomes stale (lower than the true L1 rsETH/ETH rate), allowing depositors to mint excess wrsETH at the expense of existing holders — an analog to the "no minimum rate" vulnerability in the reference report.

---

### Finding Description

`CrossChainRateReceiver` stores the rsETH/ETH exchange rate pushed from L1 via LayerZero and exposes it through `getRate()`:

```solidity
// contracts/cross-chain/CrossChainRateReceiver.sol
uint256 public rate;
uint256 public lastUpdated;   // recorded but never validated

function getRate() external view returns (uint256) {
    return rate;              // no staleness check
}
``` [1](#0-0) [2](#0-1) 

The rate is updated only when a LayerZero message arrives from L1 via `lzReceive`. If no message arrives (network congestion, outage, or message queue backup), `rate` remains at its last value indefinitely. [3](#0-2) 

Every L2 pool variant (`RSETHPoolV3`, `RSETHPoolV3ExternalBridge`, `RSETHPoolV3WithNativeChainBridge`, `RSETHPoolNoWrapper`) calls `IOracle(rsETHOracle).getRate()` and uses the result as the denominator when computing wrsETH to mint:

```solidity
// contracts/pools/RSETHPoolV3.sol
uint256 rsETHToETHrate = getRate();
rsETHAmount = amountAfterFee * 1e18 / rsETHToETHrate;
``` [4](#0-3) [5](#0-4) 

Because rsETH/ETH rate monotonically increases as EigenLayer staking rewards accrue, a stale rate is always **lower** than the true current rate. A lower denominator produces a **larger** `rsETHAmount`, so depositors receive more wrsETH than they are entitled to.

There is no minimum-rate guard, no maximum-staleness guard, and no circuit-breaker in any pool's `deposit()` path that would reject a stale oracle value.

---

### Impact Explanation

**High — Theft of unclaimed yield.**

rsETH/ETH rate grows over time. If the rate has not been updated for, say, 7 days and 0.5% of staking yield has accrued, every depositor during that window receives ~0.5% more wrsETH than their ETH entitles them to. When those wrsETH tokens are later redeemed on L1 (via the wrapper), the excess claim is satisfied by diluting the yield owed to pre-existing rsETH holders. At scale (e.g., $100 M TVL, 0.5% drift), this represents ~$500 K in stolen yield per outage window. The impact scales with both the staleness duration and the deposit volume during the outage.

---

### Likelihood Explanation

LayerZero has experienced message delivery delays and outages. The external report explicitly cites Solana downtime, Arbitrum downtime, and an Ethereum client fork as realistic network-failure scenarios. The same class of event applies here: any period during which LayerZero messages from L1 are delayed or dropped leaves the L2 rate stale. Because `updateRate()` on `MultiChainRateProvider` / `CrossChainRateProvider` is permissionless (anyone can call it), the rate is normally kept fresh, but the call itself requires a live LayerZero path and sufficient ETH for gas — both of which can fail simultaneously during congestion. [6](#0-5) 

---

### Recommendation

1. **Add a staleness check inside `getRate()`** in `CrossChainRateReceiver`:
   ```solidity
   uint256 public constant MAX_RATE_AGE = 24 hours;
   function getRate() external view returns (uint256) {
       require(block.timestamp - lastUpdated <= MAX_RATE_AGE, "Rate stale");
       return rate;
   }
   ```
2. **Add a minimum-rate floor** (e.g., `require(rate >= 1e18, "Rate below floor")`) since rsETH/ETH should never be below 1:1.
3. **Pause L2 deposits** automatically if the rate has not been updated within the staleness window, mirroring the downside-protection pause already present in `LRTOracle._updateRsETHPrice()` on L1. [7](#0-6) 

---

### Proof of Concept

1. At time T=0, L1 rsETH/ETH rate is 1.05e18. LayerZero delivers this to `CrossChainRateReceiver`; `rate = 1.05e18`, `lastUpdated = T`.
2. LayerZero experiences a 48-hour outage. L1 rate grows to 1.06e18 (staking rewards). L2 `rate` remains 1.05e18.
3. Attacker (or any depositor) calls `RSETHPoolV3.deposit{value: 100 ether}("")`.
4. Pool computes: `rsETHAmount = 100e18 * 1e18 / 1.05e18 = 95.238e18 wrsETH`.
5. Correct amount at true rate: `100e18 * 1e18 / 1.06e18 = 94.339e18 wrsETH`.
6. Excess minted: `0.899e18 wrsETH` per 100 ETH — a ~0.95% over-issuance that dilutes all existing rsETH holders' yield.
7. No revert occurs; no staleness check exists anywhere in the call path. [8](#0-7) [9](#0-8) [4](#0-3)

### Citations

**File:** contracts/cross-chain/CrossChainRateReceiver.sol (L13-16)
```text
    uint256 public rate;

    /// @notice Last time rate was updated
    uint256 public lastUpdated;
```

**File:** contracts/cross-chain/CrossChainRateReceiver.sol (L82-105)
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

**File:** contracts/LRTOracle.sol (L269-282)
```text
        // downside protection — pause if price drops too far
        if (newRsETHPrice < highestRsethPrice) {
            uint256 diff = highestRsethPrice - newRsETHPrice;
            // normalizing to 1e18
            bool isPriceDecreaseOffLimit =
                pricePercentageLimit > 0 && diff > pricePercentageLimit.mulWad(highestRsethPrice);

            // if price decrease is off limit, pause the protocol (unless it's already paused)
            if (isPriceDecreaseOffLimit) {
                if (!lrtDepositPool.paused()) lrtDepositPool.pause();
                if (!withdrawalManager.paused()) withdrawalManager.pause();
                _pause();
                return;
            }
```
