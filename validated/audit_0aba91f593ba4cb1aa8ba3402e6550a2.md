### Title
Division Rounding to Zero Allows ETH Deposits to Yield Zero rsETH — (File: `contracts/pools/RSETHPool.sol`, `contracts/pools/RSETHPoolV3.sol`, `contracts/pools/RSETHPoolNoWrapper.sol`, `contracts/pools/RSETHPoolV3ExternalBridge.sol`, `contracts/pools/RSETHPoolV3WithNativeChainBridge.sol`)

---

### Summary

All L2 pool deposit functions compute the rsETH output via integer division. When the deposited amount (after fee) is smaller than `rsETHToETHrate / 1e18`, the division truncates to zero. The pool accepts the ETH but transfers or mints **zero rsETH** to the caller. No guard against a zero output exists in any of these contracts.

---

### Finding Description

Every L2 pool's `viewSwapRsETHAmountAndFee` computes the rsETH output as:

```solidity
// ETH path (RSETHPool.sol:319, RSETHPoolV3.sol:307, RSETHPoolNoWrapper.sol:285, etc.)
rsETHAmount = amountAfterFee * 1e18 / rsETHToETHrate;

// Token path (RSETHPool.sol:346, RSETHPoolV3.sol:334, etc.)
rsETHAmount = amountAfterFee * tokenToETHRate / rsETHToETHrate;
```

`rsETHToETHrate` is the oracle rate returned by `getRate()`, expressed in 1e18 units (e.g., `1.05e18` when rsETH trades at a 5 % premium over ETH). For the ETH path, the condition that makes `rsETHAmount` truncate to zero is:

```
amountAfterFee * 1e18 < rsETHToETHrate
⟹ amountAfterFee < rsETHToETHrate / 1e18   (≈ 1.05 for a typical rate)
```

So any deposit of **1 wei** (after fee) satisfies this condition and produces `rsETHAmount = 0`.

The deposit functions only guard against `amount == 0`; they do **not** guard against `rsETHAmount == 0`:

```solidity
// RSETHPool.sol:265-277
function deposit(string memory referralId) external payable nonReentrant whenNotPaused {
    ...
    uint256 amount = msg.value;
    if (amount == 0) revert InvalidAmount();          // only zero-amount guard
    (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount);
    feeEarnedInETH += fee;
    IERC20(address(wrsETH)).safeTransfer(msg.sender, rsETHAmount); // transfers 0
    emit SwapOccurred(msg.sender, rsETHAmount, fee, referralId);
}
```

The same pattern is present in:
- `RSETHPoolV3.deposit()` — calls `wrsETH.mint(msg.sender, rsETHAmount)` with `rsETHAmount = 0`
- `RSETHPoolNoWrapper.deposit()` — calls `rsETH.safeTransfer(msg.sender, rsETHAmount)` with `rsETHAmount = 0`
- `RSETHPoolV3ExternalBridge.deposit()` — same
- `RSETHPoolV3WithNativeChainBridge.deposit()` — same

The token-path variant is additionally affected when `tokenToETHRate` is small relative to `rsETHToETHrate`, widening the zero-output range beyond 1 wei.

The same root cause exists in `LRTDepositPool.getRsETHAmountToMint`:

```solidity
// LRTDepositPool.sol:520
rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
```

However, `LRTDepositPool` is partially mitigated by `minAmountToDeposit` (if set non-zero) and by the `minRSETHAmountExpected` slippage parameter that the caller controls.

---

### Impact Explanation

A depositor who sends 1 wei of ETH (or a token amount below the rounding threshold) to any L2 pool:

1. Passes the `amount == 0` guard.
2. Receives `rsETHAmount = 0` from `viewSwapRsETHAmountAndFee`.
3. Has their ETH accepted by the pool.
4. Receives **zero rsETH/wrsETH** in return.

The depositor's ETH is not returned; it remains in the pool and will eventually be bridged to L1. The depositor permanently loses their deposit without receiving the promised rsETH. This matches the allowed impact: **"Low — Contract fails to deliver promised returns, but doesn't lose value."**

---

### Likelihood Explanation

The condition is reachable by any unprivileged caller with no preconditions beyond the pool being unpaused and ETH deposits being enabled. A user could trigger this accidentally (e.g., a script sending a dust amount) or a griefing actor could trigger it repeatedly to pollute pool accounting. The rsETH rate is always above `1e18`, so the 1-wei threshold is always active.

---

### Recommendation

Add a zero-output guard in every deposit function (or in `viewSwapRsETHAmountAndFee` itself):

```solidity
(uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount);
if (rsETHAmount == 0) revert InvalidAmount();
```

This mirrors the Sai report's recommendation to add `require(div(wad, chi()) > 0)` in `SaiTub.draw`. Apply the same guard to the token-path deposit variants and to `LRTDepositPool._beforeDeposit` (in addition to the existing `minAmountToDeposit` check).

---

### Proof of Concept

Assume `rsETHToETHrate = 1.05e18` (a realistic post-reward rate).

```
amount        = 1 wei (msg.value)
feeBps        = 10  (0.1 %)
fee           = 1 * 10 / 10_000 = 0   (rounds to zero)
amountAfterFee = 1 - 0 = 1 wei

rsETHAmount   = 1 * 1e18 / 1.05e18
              = 1e18 / 1_050_000_000_000_000_000
              = 0   (integer division truncates)
```

The pool accepts 1 wei of ETH and transfers 0 rsETH to the caller. The caller's ETH is permanently absorbed by the pool with no rsETH issued. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5) [7](#0-6) [8](#0-7)

### Citations

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

**File:** contracts/pools/RSETHPool.sol (L311-319)
```text
    function viewSwapRsETHAmountAndFee(uint256 amount) public view returns (uint256 rsETHAmount, uint256 fee) {
        fee = amount * feeBps / 10_000;
        uint256 amountAfterFee = amount - fee;

        // rate of rsETH in ETH
        uint256 rsETHToETHrate = getRate();

        // Calculate the final rsETH amount
        rsETHAmount = amountAfterFee * 1e18 / rsETHToETHrate;
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

**File:** contracts/pools/RSETHPoolNoWrapper.sol (L229-244)
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

**File:** contracts/LRTDepositPool.sol (L506-521)
```text
    function getRsETHAmountToMint(
        address asset,
        uint256 amount
    )
        public
        view
        override
        returns (uint256 rsethAmountToMint)
    {
        // setup oracle contract
        address lrtOracleAddress = lrtConfig.getContract(LRTConstants.LRT_ORACLE);
        ILRTOracle lrtOracle = ILRTOracle(lrtOracleAddress);

        // calculate rseth amount to mint based on asset amount and asset exchange rate
        rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
    }
```

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
