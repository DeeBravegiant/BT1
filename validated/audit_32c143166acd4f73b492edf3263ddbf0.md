### Title
Stale Cross-Chain Rate in `CrossChainRateReceiver` Allows Over-Minting of wrsETH on L2 - (`contracts/cross-chain/CrossChainRateReceiver.sol`)

---

### Summary

The `CrossChainRateReceiver.getRate()` function returns the last cached `rate` with no staleness check. All L2 deposit pools (`RSETHPoolV3`, `RSETHPoolV3ExternalBridge`, `RSETHPoolV3WithNativeChainBridge`, `RSETHPoolNoWrapper`) use this rate to compute how many wrsETH/rsETH tokens to mint per unit of ETH or LST deposited. Because rsETH is a continuously appreciating yield-bearing token, the L2 cached rate will routinely lag behind the true L1 rate stored in `LRTOracle.rsETHPrice()`. An unprivileged depositor can exploit this divergence to receive more wrsETH than the actual L1 rate justifies, diluting existing rsETH holders.

---

### Finding Description

The `CrossChainRateReceiver` stores the rsETH/ETH rate received from L1 via LayerZero:

```solidity
// contracts/cross-chain/CrossChainRateReceiver.sol
uint256 public rate;
uint256 public lastUpdated;

function lzReceive(...) external {
    ...
    rate = _rate;
    lastUpdated = block.timestamp;
}

function getRate() external view returns (uint256) {
    return rate;  // no staleness check
}
``` [1](#0-0) [2](#0-1) 

The rate is only updated when someone calls `updateRate()` on the L1 `CrossChainRateProvider` / `MultiChainRateProvider` and pays the LayerZero fee. There is no on-chain enforcement of a maximum staleness window.

All L2 pool variants use this oracle directly for minting:

```solidity
// contracts/pools/RSETHPoolV3.sol
function viewSwapRsETHAmountAndFee(uint256 amount) public view returns (uint256 rsETHAmount, uint256 fee) {
    fee = amount * feeBps / 10_000;
    uint256 amountAfterFee = amount - fee;
    uint256 rsETHToETHrate = getRate();          // stale L2 rate
    rsETHAmount = amountAfterFee * 1e18 / rsETHToETHrate;
}
``` [3](#0-2) [4](#0-3) 

The same pattern appears in `RSETHPoolV3ExternalBridge` and `RSETHPoolV3WithNativeChainBridge`. [5](#0-4) [6](#0-5) 

The L1 source of truth is `LRTOracle.rsETHPrice()`, which is updated by calling `updateRSETHPrice()` and reflects accumulated staking rewards. The L2 rate is a snapshot of this value at the time of the last LayerZero message. [7](#0-6) [8](#0-7) 

**The mismatch**: rsETH is a yield-bearing token — its ETH value monotonically increases as staking rewards accumulate. If the L2 oracle has not been updated for, say, 24 hours, the cached rate is lower than the true L1 rate. The minting formula `rsETHAmount = amountAfterFee * 1e18 / rsETHToETHrate` then produces a larger `rsETHAmount` than the actual L1 rate would justify. The depositor receives excess wrsETH backed by the same ETH, diluting all existing rsETH holders.

This is the direct analog to M-9: just as AAVE's single-oracle feature causes the extension to use a different price than AAVE uses internally, the L2 pool uses a different (stale) rsETH/ETH rate than the L1 protocol uses internally, leading to incorrect token issuance.

---

### Impact Explanation

When the L2 cached rate is stale (lower than the true L1 rate):

- `rsETHToETHrate` (L2) < `rsETHPrice` (L1)
- `rsETHAmount = amountAfterFee * 1e18 / rsETHToETHrate` is **larger** than it should be
- The depositor receives excess wrsETH for the same ETH
- When this wrsETH is eventually redeemed on L1, it claims more ETH than was deposited
- The excess comes from the pool of existing rsETH holders — this is **theft of yield**

The magnitude scales with: (a) how long the L2 oracle has been stale, and (b) the rate of rsETH appreciation. With typical LST yields (~4–5% APY), a 24-hour staleness window creates ~0.013% over-issuance per deposit. An attacker depositing large amounts during a prolonged staleness window can extract meaningful value.

**Impact**: High — theft of unclaimed yield from existing rsETH holders.

---

### Likelihood Explanation

- rsETH appreciates continuously; the L2 rate is always eventually stale.
- `updateRate()` on the provider requires paying LayerZero fees and is not automated on-chain; any delay in off-chain keepers creates a window.
- No on-chain staleness guard exists in `CrossChainRateReceiver.getRate()`.
- The attack requires only a standard `deposit()` call — no special permissions.
- The attacker can monitor L1 `LRTOracle.rsETHPrice()` vs. the L2 `CrossChainRateReceiver.rate` and act when divergence is profitable.

**Likelihood**: Medium — the condition (stale rate) is a normal operational state, not an exceptional one.

---

### Recommendation

Add a staleness check in `CrossChainRateReceiver.getRate()`:

```solidity
uint256 public constant MAX_RATE_AGE = 24 hours;

function getRate() external view returns (uint256) {
    require(block.timestamp - lastUpdated <= MAX_RATE_AGE, "Rate is stale");
    return rate;
}
``` [2](#0-1) 

This causes deposits to revert when the oracle is stale, preventing over-minting. Alternatively, the L2 pools should enforce a maximum acceptable rate age before allowing minting.

---

### Proof of Concept

1. At time T=0, L1 `LRTOracle.rsETHPrice()` = 1.05 ETH/rsETH. L2 `CrossChainRateReceiver.rate` = 1.05 ETH/rsETH (in sync).
2. 48 hours pass. Staking rewards accumulate. L1 `rsETHPrice` = 1.056 ETH/rsETH. No one calls `updateRate()` on L1 (or the LayerZero message is delayed). L2 rate remains 1.05.
3. Attacker deposits 1000 ETH into `RSETHPoolV3.deposit()` on L2.
4. L2 mints: `1000e18 * 1e18 / 1.05e18 ≈ 952.38 wrsETH` (using stale rate).
5. Correct amount at L1 rate: `1000e18 * 1e18 / 1.056e18 ≈ 946.97 wrsETH`.
6. Attacker receives ~5.41 excess wrsETH, redeemable for ~5.71 ETH on L1.
7. The excess is borne by existing rsETH holders whose share is diluted. [9](#0-8) [10](#0-9)

### Citations

**File:** contracts/cross-chain/CrossChainRateReceiver.sol (L13-16)
```text
    uint256 public rate;

    /// @notice Last time rate was updated
    uint256 public lastUpdated;
```

**File:** contracts/cross-chain/CrossChainRateReceiver.sol (L93-97)
```text
        uint256 _rate = abi.decode(_payload, (uint256));

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

**File:** contracts/pools/RSETHPoolV3ExternalBridge.sol (L421-427)
```text

        // rate of rsETH in ETH
        uint256 rsETHToETHrate = getRate();

        // Calculate the final rsETH amount
        rsETHAmount = amountAfterFee * 1e18 / rsETHToETHrate;
    }
```

**File:** contracts/pools/RSETHPoolV3WithNativeChainBridge.sol (L271-273)
```text
    function getRate() public view returns (uint256) {
        return IOracle(rsETHOracle).getRate();
    }
```

**File:** contracts/LRTOracle.sol (L87-89)
```text
    function updateRSETHPrice() public whenNotPaused {
        _updateRsETHPrice();
    }
```

**File:** contracts/cross-chain/RSETHMultiChainRateProvider.sol (L26-28)
```text
    function getLatestRate() public view override returns (uint256) {
        return ILRTOracle(rsETHPriceOracle).rsETHPrice();
    }
```
