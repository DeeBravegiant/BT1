### Title
Zero rsETH Minting on Small Deposits Silently Burns User Funds - (`contracts/pools/RSETHPoolV3.sol`, `contracts/pools/RSETHPoolNoWrapper.sol`, `contracts/pools/RSETHPoolV3ExternalBridge.sol`)

---

### Summary

The `deposit()` functions across all L2 pool contracts compute the rsETH output amount via integer division that can silently round down to zero. No guard checks that `rsETHAmount > 0` before the contract accepts the user's ETH or tokens and mints/transfers zero wrsETH/rsETH. A depositor who sends a small-but-nonzero amount loses their funds with no revert and no warning.

---

### Finding Description

In `RSETHPoolV3.sol`, `RSETHPoolNoWrapper.sol`, and `RSETHPoolV3ExternalBridge.sol`, the ETH deposit path computes:

```solidity
// RSETHPoolV3.sol line 307
rsETHAmount = amountAfterFee * 1e18 / rsETHToETHrate;
```

and the token deposit path computes:

```solidity
// RSETHPoolV3.sol line 334
rsETHAmount = amountAfterFee * tokenToETHRate / rsETHToETHrate;
```

`rsETHToETHrate` is the oracle-reported price of rsETH in ETH (currently ≈ 1.05 × 10¹⁸ and growing as restaking rewards accrue). Whenever `amountAfterFee * 1e18 < rsETHToETHrate`, Solidity's integer division truncates the result to **zero**.

The deposit functions then proceed unconditionally:

```solidity
// RSETHPoolV3.sol lines 258-262
(uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount);
feeEarnedInETH += fee;
wrsETH.mint(msg.sender, rsETHAmount);   // mints 0 — no revert
```

```solidity
// RSETHPoolNoWrapper.sol lines 237-241
(uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount);
feeEarnedInETH += fee;
rsETH.safeTransfer(msg.sender, rsETHAmount);  // transfers 0 — no revert
```

The only guard present is `if (amount == 0) revert InvalidAmount()`, which does not protect against a nonzero `amount` that produces a zero output. There is no `minRSETHAmountExpected` slippage parameter in any of these deposit signatures, so the caller has no on-chain mechanism to protect themselves.

By contrast, `LRTDepositPool._beforeDeposit` has a `minRSETHAmountExpected` slippage guard and a `minAmountToDeposit` floor; the L2 pool contracts have neither.

---

### Impact Explanation

**Low — Contract fails to deliver promised returns.**

A user who deposits any amount of ETH (or supported token) smaller than `rsETHToETHrate / 1e18` wei (currently ≈ 1 wei for ETH, growing over time) receives zero wrsETH/rsETH while their ETH/tokens are retained by the pool. The deposited value is not returned and not credited. Because `ERC20.mint(addr, 0)` and `safeTransfer(addr, 0)` both succeed silently, the transaction emits a `SwapOccurred` event with `rsETHAmount = 0`, giving no on-chain indication of failure.

The practical loss per transaction is at most a few wei today, but the threshold grows as the rsETH exchange rate appreciates, and the absence of any revert means integrators or smart-contract callers that do not inspect the return value will silently lose funds.

---

### Likelihood Explanation

Any unprivileged depositor can trigger this by sending a sufficiently small ETH value (e.g., 1 wei) to `deposit(string)`. No front-running, oracle manipulation, or admin action is required. The condition is deterministic and reproducible at any time. The affected entry points are public and payable.

---

### Recommendation

Add a zero-output guard in each `deposit()` function immediately after computing `rsETHAmount`:

```solidity
(uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount);
if (rsETHAmount == 0) revert ZeroRsETHMinted();
```

Apply this to all four deposit overloads in `RSETHPoolV3`, `RSETHPoolNoWrapper`, and `RSETHPoolV3ExternalBridge`. Optionally, also add a `minRSETHAmountExpected` parameter (as `LRTDepositPool` does) to give callers explicit slippage control.

---

### Proof of Concept

Assume `rsETHToETHrate = 1.05e18` (a realistic current value):

1. Alice calls `RSETHPoolV3.deposit{value: 1}("")` (1 wei ETH).
2. `viewSwapRsETHAmountAndFee(1)` computes:
   - `fee = 1 * feeBps / 10_000 = 0` (rounds to 0 for small feeBps)
   - `amountAfterFee = 1`
   - `rsETHAmount = 1 * 1e18 / 1.05e18 = 0` (integer division truncates)
3. `feeEarnedInETH += 0`
4. `wrsETH.mint(Alice, 0)` — succeeds, Alice receives 0 wrsETH.
5. Alice's 1 wei ETH is now held by the pool with no corresponding wrsETH issued.
6. `SwapOccurred(Alice, 0, 0, "")` is emitted — no revert, no indication of failure.

The same path applies to token deposits via `deposit(address token, uint256 amount, string referralId)` in all three pool contracts. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5) [7](#0-6) [8](#0-7)

### Citations

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

**File:** contracts/pools/RSETHPoolV3.sol (L271-293)
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
        limitDailyMint(amount, token)
    {
        if (amount == 0) revert InvalidAmount();

        IERC20(token).safeTransferFrom(msg.sender, address(this), amount);

        (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount, token);

        feeEarnedInToken[token] += fee;

        wrsETH.mint(msg.sender, rsETHAmount);

        emit SwapOccurred(msg.sender, rsETHAmount, fee, referralId, token);
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

**File:** contracts/pools/RSETHPoolV3ExternalBridge.sol (L364-384)
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
