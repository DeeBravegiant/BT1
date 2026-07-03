### Title
Missing Minimum rsETH Output Protection in L2 Pool `deposit()` Functions - (File: contracts/pools/RSETHPoolV3.sol, contracts/pools/RSETHPool.sol, contracts/pools/RSETHPoolNoWrapper.sol, contracts/pools/RSETHPoolV3ExternalBridge.sol, contracts/pools/RSETHPoolV3WithNativeChainBridge.sol, contracts/pools/RSETHPoolV2ExternalBridge.sol)

---

### Summary

Every L2 pool `deposit()` function mints rsETH based on the oracle rate read at execution time, but accepts no `minRsETHAmountExpected` parameter. The L1 `LRTDepositPool` explicitly provides this protection; the L2 pools do not. A user who submits a deposit transaction and observes a favourable rate at submission time has no on-chain guarantee that the same rate will apply at execution time, and cannot revert if the minted amount falls below their acceptable threshold.

---

### Finding Description

`LRTDepositPool.depositETH()` and `LRTDepositPool.depositAsset()` on L1 both accept a `minRSETHAmountExpected` parameter and revert in `_beforeDeposit()` if the computed mint amount falls below it:

```solidity
// LRTDepositPool.sol L667-669
if (rsethAmountToMint < minRSETHAmountExpected) {
    revert MinimumAmountToReceiveNotMet();
}
```

Every L2 pool `deposit()` function omits this parameter entirely. For example, in `RSETHPoolV3.sol`:

```solidity
// RSETHPoolV3.sol L246-265
function deposit(string memory referralId)
    external payable nonReentrant whenNotPaused
    limitDailyMint(msg.value, ETH_IDENTIFIER)
{
    ...
    (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount);
    ...
    wrsETH.mint(msg.sender, rsETHAmount);
}
```

`viewSwapRsETHAmountAndFee` reads the live oracle rate at execution time:

```solidity
// RSETHPoolV3.sol L299-308
function viewSwapRsETHAmountAndFee(uint256 amount) public view returns (uint256 rsETHAmount, uint256 fee) {
    fee = amount * feeBps / 10_000;
    uint256 amountAfterFee = amount - fee;
    uint256 rsETHToETHrate = getRate();          // live oracle read
    rsETHAmount = amountAfterFee * 1e18 / rsETHToETHrate;
}
```

The same pattern is present in all six L2 pool variants: `RSETHPool.sol`, `RSETHPoolNoWrapper.sol`, `RSETHPoolV3ExternalBridge.sol`, `RSETHPoolV3WithNativeChainBridge.sol`, and `RSETHPoolV2ExternalBridge.sol`.

The oracle rate (`rsETHOracle`) is updated by calling `updateRSETHPrice()` on L1, which is a **public, permissionless function**. Any actor can trigger an oracle update at any time. If staking rewards have accrued since the last update, calling `updateRSETHPrice()` raises the rsETH/ETH rate, meaning subsequent depositors receive fewer rsETH tokens for the same ETH input. A user who observed a lower rate when constructing their transaction will silently receive fewer rsETH than anticipated, with no on-chain recourse.

---

### Impact Explanation

Users depositing ETH or supported LSTs into any L2 pool receive fewer rsETH tokens than they observed at transaction-submission time if the oracle rate increases before their transaction is mined. Because the L2 pools provide no minimum-output guard, the shortfall is accepted silently. The deposited ETH is not lost (it remains in the pool), but the user receives a smaller rsETH position than they intended — a failure to deliver the promised return.

**Impact: Low — Contract fails to deliver promised returns, but doesn't lose value.**

---

### Likelihood Explanation

The `updateRSETHPrice()` function on L1 is public and callable by anyone. Staking rewards accrue continuously, so the oracle rate drifts upward over time. Any actor can call `updateRSETHPrice()` immediately before a large pending deposit to ensure the deposit executes at the freshly-updated (higher) rate, reducing the depositor's rsETH output. No special privilege is required. The rate change per update is bounded by accrued rewards and the `pricePercentageLimit` guard in `LRTOracle`, so the per-transaction loss is modest but non-zero and repeatable.

---

### Recommendation

Add a `minRsETHAmountExpected` parameter to every L2 pool `deposit()` function, mirroring the protection already present in `LRTDepositPool`:

```solidity
function deposit(string memory referralId, uint256 minRsETHAmountExpected)
    external payable nonReentrant whenNotPaused
    limitDailyMint(msg.value, ETH_IDENTIFIER)
{
    ...
    (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount);
    if (rsETHAmount < minRsETHAmountExpected) revert SlippageExceeded();
    wrsETH.mint(msg.sender, rsETHAmount);
}
```

Apply the same change to the token-deposit overload and to all other L2 pool variants.

---

### Proof of Concept

1. User observes oracle rate R and submits `deposit{value: 1 ether}("ref")` expecting `~1e18 / R` rsETH.
2. Attacker (or anyone) calls `LRTOracle.updateRSETHPrice()` on L1, which propagates a higher rate R' > R to the L2 oracle.
3. User's transaction executes; `viewSwapRsETHAmountAndFee` reads R' and mints `1e18 / R'` rsETH — fewer than the user expected.
4. No revert occurs; the user silently receives a smaller rsETH position.

The same sequence applies to the token-deposit path in `RSETHPoolV3.sol` and all other L2 pool contracts. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5) [7](#0-6) [8](#0-7) [9](#0-8)

### Citations

**File:** contracts/LRTDepositPool.sol (L648-670)
```text
    function _beforeDeposit(
        address asset,
        uint256 depositAmount,
        uint256 minRSETHAmountExpected
    )
        private
        view
        returns (uint256 rsethAmountToMint)
    {
        if (depositAmount == 0 || depositAmount < minAmountToDeposit) {
            revert InvalidAmountToDeposit();
        }

        if (_checkIfDepositAmountExceedesCurrentLimit(asset, depositAmount)) {
            revert MaximumDepositLimitReached();
        }

        rsethAmountToMint = getRsETHAmountToMint(asset, depositAmount);

        if (rsethAmountToMint < minRSETHAmountExpected) {
            revert MinimumAmountToReceiveNotMet();
        }
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

**File:** contracts/pools/RSETHPool.sol (L265-278)
```text
    function deposit(string memory referralId) external payable nonReentrant whenNotPaused {
        if (!isEthDepositEnabled) revert EthDepositDisabled();
        uint256 amount = msg.value;

        if (amount == 0) revert InvalidAmount();

        (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount);

        feeEarnedInETH += fee;

        IERC20(address(wrsETH)).safeTransfer(msg.sender, rsETHAmount);

        emit SwapOccurred(msg.sender, rsETHAmount, fee, referralId);
    }
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

**File:** contracts/pools/RSETHPoolV3ExternalBridge.sol (L366-384)
```text
    function deposit(string memory referralId)
        external
        payable
        nonReentrant
        whenNotPaused
        limitDailyMint(msg.value, ETH_IDENTIFIER)
    {
        uint256 amount = msg.value;

        if (amount == 0) revert InvalidAmount();

        (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount);

        feeEarnedInETH += fee;

        wrsETH.mint(msg.sender, rsETHAmount);

        emit SwapOccurred(msg.sender, rsETHAmount, fee, referralId);
    }
```

**File:** contracts/pools/RSETHPoolV3WithNativeChainBridge.sol (L282-301)
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

**File:** contracts/pools/RSETHPoolV2ExternalBridge.sol (L289-301)
```text
    function deposit(string memory referralId) external payable nonReentrant whenNotPaused limitDailyMint(msg.value) {
        uint256 amount = msg.value;

        if (amount == 0) revert InvalidAmount();

        (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount);

        feeEarnedInETH += fee;

        wrsETH.mint(msg.sender, rsETHAmount);

        emit SwapOccurred(msg.sender, rsETHAmount, fee, referralId);
    }
```

**File:** contracts/LRTOracle.sol (L87-89)
```text
    function updateRSETHPrice() public whenNotPaused {
        _updateRsETHPrice();
    }
```
