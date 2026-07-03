### Title
Missing Minimum Output Parameter in L2 Pool `deposit()` Functions Exposes Depositors to Unbounded Rate Slippage - (File: contracts/pools/RSETHPoolV3ExternalBridge.sol, contracts/pools/RSETHPool.sol, contracts/pools/RSETHPoolV3.sol, contracts/pools/RSETHPoolNoWrapper.sol, contracts/pools/RSETHPoolV3WithNativeChainBridge.sol)

---

### Summary

Every L2 pool `deposit()` variant (ETH and token) across all RSETHPool contracts lacks a caller-supplied minimum output parameter. Users have no on-chain mechanism to enforce the rsETH/wrsETH amount they previewed via `viewSwapRsETHAmountAndFee`. If the oracle rate is updated between preview and execution, the depositor silently receives fewer tokens than expected with no recourse.

---

### Finding Description

All L2 pool deposit entry points follow the same pattern:

```solidity
// RSETHPoolV3ExternalBridge.sol – ETH deposit (line 366)
function deposit(string memory referralId)
    external payable nonReentrant whenNotPaused limitDailyMint(msg.value, ETH_IDENTIFIER)
{
    uint256 amount = msg.value;
    if (amount == 0) revert InvalidAmount();

    (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount);
    feeEarnedInETH += fee;
    wrsETH.mint(msg.sender, rsETHAmount);          // ← no minRsETHAmount guard
    emit SwapOccurred(msg.sender, rsETHAmount, fee, referralId);
}
```

The amount minted is computed entirely from the live oracle rate at execution time:

```solidity
// viewSwapRsETHAmountAndFee (line 418)
function viewSwapRsETHAmountAndFee(uint256 amount) public view returns (uint256 rsETHAmount, uint256 fee) {
    fee = amount * feeBps / 10_000;
    uint256 amountAfterFee = amount - fee;
    uint256 rsETHToETHrate = getRate();            // ← oracle read at execution time
    rsETHAmount = amountAfterFee * 1e18 / rsETHToETHrate;
}
```

The same pattern is present in every pool variant:

| Contract | ETH deposit | Token deposit |
|---|---|---|
| `RSETHPool.sol` | line 265 | line 284 |
| `RSETHPoolV2ExternalBridge.sol` | line 289 | — |
| `RSETHPoolV3ExternalBridge.sol` | line 366 | line 390 |
| `RSETHPoolV3.sol` | line 246 | line 271 |
| `RSETHPoolNoWrapper.sol` | analogous | analogous |
| `RSETHPoolV3WithNativeChainBridge.sol` | line 282 | line 307 |

By contrast, the L1 `LRTDepositPool` correctly accepts a `minRSETHAmountExpected` parameter and enforces it:

```solidity
// LRTDepositPool.sol line 667
if (rsethAmountToMint < minRSETHAmountExpected) {
    revert MinimumAmountToReceiveNotMet();
}
```

The L2 pools expose the same oracle-rate-based minting logic but provide no equivalent guard.

---

### Impact Explanation

**Low — Contract fails to deliver promised returns, but doesn't lose value.**

The rsETH/ETH rate returned by `getRate()` is a monotonically increasing cross-chain rate (staking rewards accrue on L1 and are periodically pushed to L2 oracles). Between the moment a user calls `viewSwapRsETHAmountAndFee` to preview their output and the moment their `deposit()` transaction is included, the oracle rate can be updated upward. Because rsETH becomes more valuable per ETH, the user receives fewer wrsETH/rsETH tokens than previewed. The deposited ETH/tokens are not lost, but the user receives a worse exchange than they observed and agreed to, with no on-chain protection.

---

### Likelihood Explanation

**Medium.** Oracle rate updates on L2 are routine protocol operations triggered by cross-chain rate pushes from L1. Any deposit transaction that lands in a block after an oracle update will silently receive fewer tokens than previewed. This is not a rare edge case; it is a normal operating condition. No attacker action is required — the discrepancy arises from ordinary protocol activity.

---

### Recommendation

Add a `minRsETHAmount` parameter to all `deposit()` overloads and revert if the computed output falls below it, mirroring the protection already present in `LRTDepositPool`:

```solidity
function deposit(string memory referralId, uint256 minRsETHAmount)
    external payable nonReentrant whenNotPaused limitDailyMint(msg.value, ETH_IDENTIFIER)
{
    uint256 amount = msg.value;
    if (amount == 0) revert InvalidAmount();

    (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount);
    if (rsETHAmount < minRsETHAmount) revert MinimumAmountToReceiveNotMet();

    feeEarnedInETH += fee;
    wrsETH.mint(msg.sender, rsETHAmount);
    emit SwapOccurred(msg.sender, rsETHAmount, fee, referralId);
}
```

Apply the same change to all token `deposit()` overloads across all pool variants.

---

### Proof of Concept

1. User calls `viewSwapRsETHAmountAndFee(1 ether)` and observes they will receive `X` wrsETH at the current oracle rate `R`.
2. User submits `deposit{value: 1 ether}("ref")`.
3. Before the transaction is included, the L2 oracle rate is updated from `R` to `R'` (where `R' > R`, reflecting accrued staking rewards).
4. `deposit()` executes: `rsETHAmount = 1e18 * (1 ether - fee) / R'`, which is strictly less than `X`.
5. The user receives fewer wrsETH than previewed. No revert occurs. The user has no recourse. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

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

**File:** contracts/pools/RSETHPool.sol (L284-305)
```text
    function deposit(
        address token,
        uint256 amount,
        string memory referralId
    )
        external
        nonReentrant
        whenNotPaused
        onlySupportedToken(token)
    {
        if (amount == 0) revert InvalidAmount();

        IERC20(token).safeTransferFrom(msg.sender, address(this), amount);

        (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount, token);

        feeEarnedInToken[token] += fee;

        IERC20(address(wrsETH)).safeTransfer(msg.sender, rsETHAmount);

        emit SwapOccurred(msg.sender, rsETHAmount, fee, referralId); // Add token address?
    }
```

**File:** contracts/LRTDepositPool.sol (L665-669)
```text
        rsethAmountToMint = getRsETHAmountToMint(asset, depositAmount);

        if (rsethAmountToMint < minRSETHAmountExpected) {
            revert MinimumAmountToReceiveNotMet();
        }
```
