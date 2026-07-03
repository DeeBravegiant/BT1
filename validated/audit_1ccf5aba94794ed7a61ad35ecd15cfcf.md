### Title
Stale `AGETHRateReceiver.rate` Causes Over-Minting of agETH in `AGETHPoolV3.deposit()` — (`contracts/agETH/AGETHPoolV3.sol`)

---

### Summary

`AGETHPoolV3` on L2 reads the agETH/ETH rate from `AGETHRateReceiver` (a `CrossChainRateReceiver`), which stores the last rate pushed via LayerZero. There is no staleness check anywhere in the chain. If `updateRate()` on `AGETHMultiChainRateProvider` (L1) is not called after the agETH rate appreciates, `AGETHRateReceiver.rate` remains at the old lower value. `AGETHPoolV3.deposit()` then mints more agETH than the current backing rate justifies.

---

### Finding Description

**Rate propagation chain:**

1. L1: `AGETHMultiChainRateProvider.getLatestRate()` calls `IAgEthRateProvider(agETHPriceOracle).getRate()` — the live rate. [1](#0-0) 

2. L1: `MultiChainRateProvider.updateRate()` (permissionless, caller pays LayerZero gas) reads `getLatestRate()`, stores it, and sends it via LayerZero to all registered `AGETHRateReceiver` contracts. [2](#0-1) 

3. L2: `CrossChainRateReceiver.lzReceive()` stores the received value in `rate` and records `lastUpdated`. [3](#0-2) 

4. L2: `CrossChainRateReceiver.getRate()` returns `rate` with **no freshness check**. [4](#0-3) 

5. L2: `AGETHPoolV3.getRate()` calls `IOracle(agETHOracle).getRate()` — directly reading the potentially stale receiver value. [5](#0-4) 

6. L2: `viewSwapAgETHAmountAndFee` computes `agETHAmount = amountAfterFee * 1e18 / agETHToETHrate` using that stale rate. [6](#0-5) 

**The arithmetic flaw:** If the live rate has moved from `R` to `R * 1.1` (agETH appreciated 10%) but the receiver still holds `R`:

- Correct mint: `amountAfterFee * 1e18 / (R * 1.1)` → fewer agETH (agETH is worth more ETH)
- Actual mint: `amountAfterFee * 1e18 / R` → ~9.09% more agETH than justified

No guard prevents this: neither `AGETHPoolV3` nor `CrossChainRateReceiver` checks `lastUpdated` against any maximum staleness threshold.

---

### Impact Explanation

Users depositing ETH (or supported tokens) into `AGETHPoolV3` during a staleness window receive more agETH than the current backing rate justifies. This over-mints agETH relative to the ETH held, diluting the backing ratio for all existing agETH holders. The deposited ETH is not lost, but the protocol fails to enforce the correct exchange rate — matching **Low: Contract fails to deliver promised returns, but doesn't lose value**.

---

### Likelihood Explanation

`updateRate()` is permissionless but requires the caller to supply ETH for LayerZero cross-chain fees. There is no on-chain enforcement that it be called within any time bound. Operational delays, LayerZero congestion, or simply no one paying the fee creates a realistic staleness window. The agETH rate appreciates continuously as staking rewards accrue, so any non-trivial delay produces a measurable divergence.

---

### Recommendation

Add a maximum staleness check in `CrossChainRateReceiver.getRate()` (or in `AGETHPoolV3.getRate()`):

```solidity
uint256 public constant MAX_RATE_AGE = 24 hours;

function getRate() external view returns (uint256) {
    require(block.timestamp - lastUpdated <= MAX_RATE_AGE, "Rate is stale");
    return rate;
}
```

This causes deposits to revert rather than proceed at a stale rate, protecting the protocol's backing invariant.

---

### Proof of Concept

```solidity
// Fork test (local fork, no public mainnet)
function testStalePriceMinting() public {
    // T=0: seed receiver with rate R
    mockRateProvider.setRate(1.05e18); // R
    provider.updateRate{value: lzFee}();
    // LayerZero delivers: receiver.rate = 1.05e18

    // Advance time, agETH appreciates on L1
    vm.warp(block.timestamp + 2 days);
    mockRateProvider.setRate(1.155e18); // R * 1.1

    // updateRate() is NOT called — receiver.rate still = 1.05e18

    // User deposits 1 ETH
    uint256 balBefore = agETH.balanceOf(user);
    vm.prank(user);
    pool.deposit{value: 1 ether}("ref");
    uint256 minted = agETH.balanceOf(user) - balBefore;

    uint256 correctAmount = 1 ether * 1e18 / 1.155e18; // ~0.8658 agETH
    uint256 staleAmount   = 1 ether * 1e18 / 1.05e18;  // ~0.9524 agETH

    assertApproxEqRel(minted, staleAmount, 1e15);
    assertGt(minted, correctAmount); // user got ~9.1% too much agETH
}
```

### Citations

**File:** contracts/agETH/AGETHMultiChainRateProvider.sol (L29-31)
```text
    function getLatestRate() public view override returns (uint256) {
        return IAgEthRateProvider(agETHPriceOracle).getRate();
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

**File:** contracts/cross-chain/CrossChainRateReceiver.sol (L82-99)
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
