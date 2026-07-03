### Title
Stale Cross-Chain Rate Used for rsETH Minting Without Staleness Check — (`contracts/cross-chain/CrossChainRateReceiver.sol`)

---

### Summary

The L2 pool contracts (`RSETHPool`, `RSETHPoolNoWrapper`, `RSETHPoolV3`, `RSETHPoolV3ExternalBridge`, `RSETHPoolV3WithNativeChainBridge`) all compute the rsETH mint amount by dividing the deposited ETH by a rate fetched from an oracle. That oracle is a `CrossChainRateReceiver` whose `getRate()` returns a **frozen snapshot** of the rsETH/ETH price that was last pushed from L1 via LayerZero. The stored `lastUpdated` timestamp is never validated against any staleness threshold before the rate is used. Because rsETH accrues staking rewards over time, the true L1 price rises continuously, but the L2 rate stays frozen until someone manually triggers a cross-chain update. A depositor who observes the gap can deposit at the stale (lower) rate and receive more rsETH than their ETH is currently worth, diluting all existing rsETH holders.

---

### Finding Description

`CrossChainRateReceiver` stores the last pushed rate and a `lastUpdated` timestamp:

```solidity
uint256 public rate;
uint256 public lastUpdated;
```

`lzReceive` updates both fields when a LayerZero message arrives from L1. `getRate()` simply returns the stored value with no freshness check:

```solidity
function getRate() external view returns (uint256) {
    return rate;
}
``` [1](#0-0) 

Every L2 pool calls `IOracle(rsETHOracle).getRate()` and uses the result as the denominator when computing how many rsETH tokens to mint:

```solidity
uint256 rsETHToETHrate = getRate();
rsETHAmount = amountAfterFee * 1e18 / rsETHToETHrate;
``` [2](#0-1) [3](#0-2) [4](#0-3) 

The rate is only refreshed when someone calls `updateRate()` on the L1 `CrossChainRateProvider` / `MultiChainRateProvider` and pays for the LayerZero message. There is no on-chain enforcement that this happens within any time window. [5](#0-4) 

On L1, `rsETHPrice` grows continuously as EigenLayer staking rewards accrue and `updateRSETHPrice()` is called. The L2 rate is a frozen snapshot of a past L1 price. [6](#0-5) 

---

### Impact Explanation

When the L2 rate lags behind the true L1 price, the denominator in the mint calculation is smaller than it should be, so `rsETHAmount` is larger than it should be. The depositor receives more rsETH per ETH than the current backing ratio justifies. Because rsETH is a share token whose value is backed by the total ETH in the protocol divided by total supply, every extra rsETH minted at a stale (lower) rate dilutes the value held by all existing rsETH holders. This constitutes **theft of unclaimed yield** from existing holders — the yield that has accrued since the last rate update is effectively transferred to the new depositor.

**Impact: High — Theft of unclaimed yield.**

---

### Likelihood Explanation

rsETH accrues staking rewards continuously; the L1 price rises every time `updateRSETHPrice()` is called. Cross-chain rate updates require a manual call and a LayerZero fee payment, so any gap between L1 price updates and L2 rate pushes is a normal operating condition, not an edge case. An attacker needs only to monitor the L1 `rsETHPrice` and the L2 `CrossChainRateReceiver.rate`, wait for a meaningful divergence (which grows with every passing hour), and call `deposit()` on any L2 pool. No special role, no front-running, no oracle manipulation is required — the entry path is the standard public `deposit()` function available to any user. [7](#0-6) [8](#0-7) 

---

### Recommendation

Add a staleness guard inside `CrossChainRateReceiver.getRate()` (or in a wrapper used by all pools) that reverts if `block.timestamp - lastUpdated` exceeds a configurable maximum age (e.g., 24 hours):

```solidity
uint256 public maxRateAge; // e.g. 86400

function getRate() external view returns (uint256) {
    require(block.timestamp - lastUpdated <= maxRateAge, "Rate stale");
    return rate;
}
``` [9](#0-8) 

This mirrors the fix recommended in the original report: the stored value must not be used as-is without accounting for elapsed time. Pausing deposits when the rate is stale is an acceptable alternative.

---

### Proof of Concept

1. At time T₀, L1 `rsETHPrice = 1.05 ETH` and the L2 `CrossChainRateReceiver.rate = 1.05 ETH` (in sync).
2. Staking rewards accrue. At time T₁ (e.g., 48 hours later), L1 `rsETHPrice = 1.06 ETH` after `updateRSETHPrice()` is called, but no one has called `updateRate()` on the L1 provider, so the L2 rate remains `1.05 ETH`.
3. Attacker calls `deposit{value: 1 ETH}()` on `RSETHPoolNoWrapper`:
   - `rsETHToETHrate = getRate() = 1.05e18` (stale)
   - `rsETHAmount = 1e18 * 1e18 / 1.05e18 ≈ 0.952381 rsETH`
   - Correct amount at current rate: `1e18 * 1e18 / 1.06e18 ≈ 0.943396 rsETH`
   - Attacker receives `≈ 0.008985 rsETH` more than they are entitled to.
4. The excess rsETH represents yield that had accrued to existing holders but is now diluted away.
5. The `lastUpdated` field on the receiver records the time of the last push but is never checked anywhere in the deposit path. [10](#0-9) [11](#0-10)

### Citations

**File:** contracts/cross-chain/CrossChainRateReceiver.sol (L13-17)
```text
    uint256 public rate;

    /// @notice Last time rate was updated
    uint256 public lastUpdated;

```

**File:** contracts/cross-chain/CrossChainRateReceiver.sol (L93-100)
```text
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

**File:** contracts/pools/RSETHPoolNoWrapper.sol (L229-243)
```text
    /// @dev Swaps ETH for rsETH
    /// @param referralId The referral id
    function deposit(string memory referralId) external payable nonReentrant whenNotPaused {
        if (!isEthDepositEnabled) revert EthDepositDisabled();
        uint256 amount = msg.value;

        if (amount == 0) revert InvalidAmount();

        (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount);

        feeEarnedInETH += fee;

        rsETH.safeTransfer(msg.sender, rsETHAmount);

        emit SwapOccurred(msg.sender, rsETHAmount, fee, referralId);
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

**File:** contracts/pools/RSETHPool.sol (L315-319)
```text
        // rate of rsETH in ETH
        uint256 rsETHToETHrate = getRate();

        // Calculate the final rsETH amount
        rsETHAmount = amountAfterFee * 1e18 / rsETHToETHrate;
```

**File:** contracts/pools/RSETHPoolV3WithNativeChainBridge.sol (L339-343)
```text
        // rate of rsETH in ETH
        uint256 rsETHToETHrate = getRate();

        // Calculate the final rsETH amount
        rsETHAmount = amountAfterFee * 1e18 / rsETHToETHrate;
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

**File:** contracts/LRTOracle.sol (L87-89)
```text
    function updateRSETHPrice() public whenNotPaused {
        _updateRsETHPrice();
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
