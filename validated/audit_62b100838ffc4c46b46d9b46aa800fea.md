### Title
Stale Cross-Chain Rate Used for wrsETH Minting Allows Depositors to Extract Value from Existing LPs - (File: contracts/cross-chain/CrossChainRateReceiver.sol)

---

### Summary

`CrossChainRateReceiver.getRate()` returns the last stored `rate` with no staleness validation. All L2 pool contracts (`RSETHPoolV2`, `RSETHPoolV3`, `RSETHPoolV3ExternalBridge`) use this rate to price wrsETH minting. When the actual rsETH price on L1 has risen but the L2 rate has not yet been updated, any depositor can mint more wrsETH than the current value warrants, extracting value from existing LPs — the direct analog of using an averaged/snapshot value instead of the current value for settlement.

---

### Finding Description

The cross-chain rate synchronization system uses a push-based model: an operator calls `MultiChainRateProvider.updateRate()` (permissionless but requires ETH payment for LayerZero fees), which broadcasts the current L1 rsETH price to `CrossChainRateReceiver` contracts on each L2.

`CrossChainRateReceiver` stores the received rate and a `lastUpdated` timestamp:

```solidity
// contracts/cross-chain/CrossChainRateReceiver.sol
uint256 public rate;        // line 13
uint256 public lastUpdated; // line 16
```

However, `getRate()` returns the stored value unconditionally with no staleness check:

```solidity
// contracts/cross-chain/CrossChainRateReceiver.sol line 103-105
function getRate() external view returns (uint256) {
    return rate;
}
```

All L2 pool variants call this oracle to price deposits. In `RSETHPoolV3`:

```solidity
// contracts/pools/RSETHPoolV3.sol line 235-237
function getRate() public view returns (uint256) {
    return IOracle(rsETHOracle).getRate();
}
```

This rate is used directly in `viewSwapRsETHAmountAndFee` to compute how many wrsETH tokens to mint:

```solidity
// contracts/pools/RSETHPoolV3.sol line 299-308
function viewSwapRsETHAmountAndFee(uint256 amount) public view returns (uint256 rsETHAmount, uint256 fee) {
    fee = amount * feeBps / 10_000;
    uint256 amountAfterFee = amount - fee;
    uint256 rsETHToETHrate = getRate();
    rsETHAmount = amountAfterFee * 1e18 / rsETHToETHrate;
}
```

And `deposit()` mints wrsETH based on this computation:

```solidity
// contracts/pools/RSETHPoolV3.sol line 258-262
(uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount);
feeEarnedInETH += fee;
wrsETH.mint(msg.sender, rsETHAmount);
```

The identical pattern exists in `RSETHPoolV2.viewSwapRsETHAmountAndFee` (line 225-234) and `RSETHPoolV3ExternalBridge.viewSwapRsETHAmountAndFee` (lines 418-427, 433-453).

The structural parallel to the reported bug is exact: the original protocol uses an **average of epoch snapshots** instead of the current PnL value; here the L2 pools use a **last-pushed snapshot** of the rsETH price instead of the current L1 value. In both cases, the discrepancy between the stale/averaged value and the true current value creates a window for depositors to mint at a favorable rate.

---

### Impact Explanation

When rsETH appreciates on L1 (from staking rewards, new asset deposits, or EigenLayer yield) but the L2 rate has not yet been updated:

- A depositor calling `deposit()` on any L2 pool receives `amountAfterFee * 1e18 / staleRate` wrsETH
- Since `staleRate < currentRate`, the depositor receives **more wrsETH than the ETH they deposited is worth at the current rate**
- Once the rate is updated, the depositor's wrsETH is redeemable for more ETH than they put in
- The excess comes directly from the pool's existing LP value — existing holders are diluted

This is **theft of unclaimed yield** from existing LPs. The magnitude scales with the rate lag and the deposit size. Given that rsETH accrues staking rewards continuously, even a few hours of lag during a period of rapid appreciation creates a meaningful extraction opportunity.

**Impact: High** — theft of unclaimed yield.

---

### Likelihood Explanation

Rate updates require a manual call to `MultiChainRateProvider.updateRate()` with ETH attached to cover LayerZero messaging fees. There is no on-chain enforcement of a maximum update interval. During periods of:
- High L2 gas costs (discouraging keepers)
- LayerZero network congestion or outages
- Rapid rsETH price appreciation (e.g., large reward distributions)

...the lag between the true L1 rate and the L2 stored rate can be hours or days. An attacker monitoring L1 rsETH price vs. the L2 stored rate can trivially identify and exploit this window.

**Likelihood: High** — the condition (stale rate) is a normal operational state, not an edge case.

---

### Recommendation

1. **Add a staleness check in `getRate()`**: Revert or return a sentinel value if `block.timestamp - lastUpdated` exceeds a configured maximum (e.g., 24 hours).
2. **Enforce rate freshness in pool deposits**: Add a modifier in `RSETHPoolV3`, `RSETHPoolV2`, and `RSETHPoolV3ExternalBridge` that reverts if the oracle rate is older than a threshold.
3. **Automate rate updates**: Use a keeper/bot with guaranteed execution (e.g., Chainlink Automation) to push rate updates on a fixed cadence, reducing the staleness window.

---

### Proof of Concept

1. At time T=0, L1 rsETH price = 1.05 ETH/rsETH. L2 stored rate = 1.05e18 (in sync).
2. Staking rewards accrue; at T=6h, L1 rsETH price = 1.06 ETH/rsETH. No keeper has called `updateRate()`.
3. Attacker deposits 100 ETH on L2 via `RSETHPoolV3.deposit()`:
   - `rsETHToETHrate = getRate()` → returns stale `1.05e18`
   - `rsETHAmount = 100e18 * 1e18 / 1.05e18 ≈ 95.238 wrsETH`
   - At the **current** rate of 1.06, 100 ETH should only buy `100/1.06 ≈ 94.340 wrsETH`
   - Attacker receives **~0.898 wrsETH extra** (~0.95 ETH of value) per 100 ETH deposited
4. Keeper calls `updateRate()`, L2 rate updates to 1.06e18.
5. Attacker's 95.238 wrsETH is now worth `95.238 * 1.06 ≈ 100.95 ETH` — a ~0.95 ETH profit extracted from existing LPs.

This is directly reachable by any unprivileged depositor with no special access. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5) [7](#0-6) [8](#0-7)

### Citations

**File:** contracts/cross-chain/CrossChainRateReceiver.sol (L13-16)
```text
    uint256 public rate;

    /// @notice Last time rate was updated
    uint256 public lastUpdated;
```

**File:** contracts/cross-chain/CrossChainRateReceiver.sol (L103-105)
```text
    function getRate() external view returns (uint256) {
        return rate;
    }
```

**File:** contracts/pools/RSETHPoolV3.sol (L235-237)
```text
    function getRate() public view returns (uint256) {
        return IOracle(rsETHOracle).getRate();
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
