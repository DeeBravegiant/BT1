### Title
Stale Cross-Chain Rate Snapshot Enables Over-Minting of wrsETH on L2 Pools - (File: contracts/cross-chain/CrossChainRateReceiver.sol)

### Summary

The L2 pool contracts (`RSETHPool`, `RSETHPoolV2`, `RSETHPoolV3`, and their variants) calculate the amount of wrsETH to mint using a rate fetched from a `CrossChainRateReceiver` oracle. This oracle stores a lazy-updated snapshot of the rsETH/ETH rate that was last pushed from L1 via LayerZero. There is no staleness check on this stored rate. Because rsETH is a yield-bearing token whose value relative to ETH monotonically increases over time, the stored snapshot will always lag behind the true L1 rate whenever `updateRate()` has not been called recently. An unprivileged depositor can exploit this divergence to receive more wrsETH than the deposited ETH is worth at the current true rate, stealing yield from existing wrsETH holders.

### Finding Description

`CrossChainRateReceiver` stores a single `rate` state variable and a `lastUpdated` timestamp. The rate is only updated when someone calls `updateRate()` on the corresponding `CrossChainRateProvider` on L1 and pays the LayerZero messaging fee. There is no on-chain mechanism that forces an update, and no staleness guard in the pool contracts that would reject a deposit when the rate is too old. [1](#0-0) 

The `getRate()` function simply returns the stored snapshot with no freshness validation: [2](#0-1) 

Every L2 pool variant calls this oracle to determine how many wrsETH tokens to mint. For example, in `RSETHPoolV3`:

```solidity
uint256 rsETHToETHrate = getRate();
rsETHAmount = amountAfterFee * 1e18 / rsETHToETHrate;
``` [3](#0-2) 

The same pattern appears in `RSETHPool`, `RSETHPoolV2`, `RSETHPoolV2ExternalBridge`, `RSETHPoolNoWrapper`, and `RSETHPoolV3WithNativeChainBridge`. [4](#0-3) 

On L1, `LRTOracle.rsETHPrice` is itself a stored snapshot updated by calling the public `updateRSETHPrice()`. The cross-chain rate provider reads this value and broadcasts it to L2 receivers. This creates two layers of lazy snapshots, both of which can diverge from the true current rate. [5](#0-4) 

### Impact Explanation

rsETH is a yield-bearing token: its ETH value increases continuously as staking rewards accrue. Whenever the L2 oracle snapshot is stale (showing a lower rate than the true current L1 rate), the division `amountAfterFee * 1e18 / rsETHToETHrate` yields a larger wrsETH amount than the deposited ETH actually warrants. The excess wrsETH represents a claim on protocol value that was not contributed by the depositor — it is extracted from the yield that should accrue to existing wrsETH holders. This is a **theft of unclaimed yield** (High severity).

### Likelihood Explanation

rsETH appreciates continuously, so the L2 snapshot is always stale to some degree. The divergence grows with time since the last `updateRate()` call. Calling `updateRate()` requires paying LayerZero messaging fees, so there is no economic incentive for a rational actor to keep the rate fresh on behalf of the protocol. An attacker who holds a large wrsETH position on L2 is directly incentivized to **avoid** calling `updateRate()`, allowing the staleness to accumulate before making a large deposit at the artificially low rate. No special privileges, flash loans, or cross-transaction coordination are required — a single `deposit()` call during a stale period suffices.

### Recommendation

1. **Add a staleness guard in the pool contracts**: Before minting, check that `block.timestamp - lastUpdated <= MAX_RATE_AGE` (e.g., 24 hours). Revert if the rate is too stale.
2. **Enforce rate updates on deposit**: Call `updateRate()` (or a lightweight on-chain rate refresh) as part of the deposit flow, or require the caller to supply a fresh rate proof.
3. **Use a time-weighted average rate (TWAR)**: Rather than trusting the last pushed snapshot, maintain a running average of pushed rates to smooth out both staleness and any transient manipulation.
4. **Incentivize rate keepers**: Subsidize or reward callers of `updateRate()` to ensure the rate is pushed frequently.

### Proof of Concept

Assume rsETH/ETH rate on L1 is currently `1.10e18` (rsETH has appreciated), but the L2 `CrossChainRateReceiver.rate` still holds the stale value `1.05e18` from a push 3 days ago.

1. Attacker calls `RSETHPoolV3.deposit{value: 1 ether}("ref")`.
2. `viewSwapRsETHAmountAndFee(1 ether)` is called:
   - `fee = 1e18 * feeBps / 10_000` (assume 0 for simplicity)
   - `rsETHToETHrate = getRate()` → returns stale `1.05e18`
   - `rsETHAmount = 1e18 * 1e18 / 1.05e18 ≈ 0.9524e18` wrsETH
3. At the true rate `1.10e18`, the attacker should have received `1e18 / 1.10e18 ≈ 0.9091e18` wrsETH.
4. The attacker received `≈ 0.0433e18` excess wrsETH (~4.8% more than entitled).
5. Once `updateRate()` is called and the L2 oracle reflects `1.10e18`, the attacker's wrsETH is redeemable at the correct higher rate — the excess was extracted from existing holders' yield. [6](#0-5) [7](#0-6)

### Citations

**File:** contracts/cross-chain/CrossChainRateReceiver.sol (L13-17)
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

**File:** contracts/pools/RSETHPoolV3.sol (L244-265)
```text
    /// @dev Swaps ETH for rsETH
    /// @param referralId The referral id
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

**File:** contracts/LRTOracle.sol (L87-89)
```text
    function updateRSETHPrice() public whenNotPaused {
        _updateRsETHPrice();
    }
```
