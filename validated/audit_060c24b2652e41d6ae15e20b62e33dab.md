### Title
Missing Minimum Return Parameter in L2 Pool Deposit Functions Leaves Users Without Slippage Protection - (`contracts/pools/RSETHPoolV3.sol`, `contracts/pools/RSETHPoolV3ExternalBridge.sol`, `contracts/pools/RSETHPool.sol`, `contracts/pools/RSETHPoolNoWrapper.sol`, `contracts/pools/RSETHPoolV3WithNativeChainBridge.sol`)

---

### Summary

All L2 deposit pool `deposit()` functions omit a `minRSETHAmountExpected` parameter that the L1 `LRTDepositPool` provides. Users depositing ETH or LSTs on any L2 pool have no on-chain mechanism to enforce a minimum amount of `wrsETH`/`rsETH` they are willing to accept, leaving them fully exposed to oracle rate changes that occur between transaction submission and execution.

---

### Finding Description

The L1 `LRTDepositPool.depositETH()` and `depositAsset()` functions both accept a `minRSETHAmountExpected` parameter and enforce it inside `_beforeDeposit()`:

```solidity
// LRTDepositPool.sol
function depositETH(uint256 minRSETHAmountExpected, string calldata referralId) external payable ...
```

```solidity
// LRTDepositPool.sol _beforeDeposit
rsethAmountToMint = getRsETHAmountToMint(asset, depositAmount);
if (rsethAmountToMint < minRSETHAmountExpected) {
    revert MinimumAmountToReceiveNotMet();
}
```

Every L2 pool variant exposes only:

```solidity
// RSETHPoolV3.sol
function deposit(string memory referralId) external payable ...
function deposit(address token, uint256 amount, string memory referralId) external ...
```

```solidity
// RSETHPoolNoWrapper.sol
function deposit(string memory referralId) external payable ...
function deposit(address token, uint256 amount, string memory referralId) external ...
```

The same pattern is repeated in `RSETHPool`, `RSETHPoolV3ExternalBridge`, and `RSETHPoolV3WithNativeChainBridge`. None of these functions accept or enforce any minimum return amount. The rsETH amount minted is computed solely from the live oracle rate at execution time:

```solidity
// RSETHPoolV3.sol viewSwapRsETHAmountAndFee
uint256 rsETHToETHrate = getRate();
rsETHAmount = amountAfterFee * 1e18 / rsETHToETHrate;
```

The oracle rate (`rsETHOracle`) is a cross-chain rate pushed from L1 and can be updated at any time by the rate provider. If a rate update is included in the same block as a user's deposit — or if the user's transaction is delayed in the mempool while a rate update lands — the user receives fewer `wrsETH`/`rsETH` tokens than they anticipated with no recourse.

---

### Impact Explanation

**Low — Contract fails to deliver promised returns, but doesn't lose value.**

A user who calls `deposit()` after observing a quoted rate may receive materially fewer `wrsETH`/`rsETH` tokens if the oracle rate is updated before their transaction executes. Because each token is worth proportionally more after the rate increase, the user's position value is approximately preserved, but the token count shortfall can be significant for large deposits and can violate the user's explicit intent (e.g., a user who needs a minimum token count for a downstream DeFi position). There is no on-chain protection the user can invoke to prevent this outcome.

---

### Likelihood Explanation

The cross-chain rate oracle is updated periodically from L1. On chains with public mempools and variable block times (Arbitrum, Base, Optimism, Linea, Scroll), a rate update transaction and a user deposit transaction can land in the same block. The likelihood of a meaningful rate delta in a single block is low under normal conditions but non-negligible during periods of rapid rsETH appreciation or high L1 activity. Any user depositing a large amount faces this risk on every transaction.

---

### Recommendation

Add a `minRSETHAmountExpected` parameter to all L2 pool `deposit()` overloads, mirroring the L1 `LRTDepositPool` pattern:

```solidity
function deposit(string memory referralId, uint256 minRSETHAmountExpected)
    external payable nonReentrant whenNotPaused limitDailyMint(msg.value, ETH_IDENTIFIER)
{
    ...
    (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount);
    if (rsETHAmount < minRSETHAmountExpected) revert MinimumAmountToReceiveNotMet();
    ...
}
```

---

### Proof of Concept

1. The oracle rate for rsETH/ETH is `1.05e18` (1 ETH = ~0.952 rsETH).
2. Alice submits `deposit{value: 10 ETH}("ref")` on `RSETHPoolV3`, expecting ~9.52 wrsETH (minus fee).
3. Before Alice's transaction is included, the L1 rate provider pushes a new rate of `1.10e18` to the L2 oracle.
4. Alice's transaction executes with the new rate: she receives `10e18 * 1e18 / 1.10e18 ≈ 9.09 wrsETH` — approximately 4.5% fewer tokens than expected.
5. Alice has no parameter to revert the transaction if the received amount falls below her threshold.

The L1 path enforces this check: [1](#0-0) 

The L2 paths do not: [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5) 

The rate used in all L2 pools is fetched live at execution time with no floor check: [7](#0-6)

### Citations

**File:** contracts/LRTDepositPool.sol (L665-669)
```text
        rsethAmountToMint = getRsETHAmountToMint(asset, depositAmount);

        if (rsethAmountToMint < minRSETHAmountExpected) {
            revert MinimumAmountToReceiveNotMet();
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
