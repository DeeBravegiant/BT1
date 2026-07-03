### Title
Zero rsETH Minted on Non-Zero Deposit Due to Rounding Down — (`contracts/pools/RSETHPoolV3.sol`, `contracts/pools/RSETHPoolNoWrapper.sol`, and all pool variants)

---

### Summary

All L2 pool deposit functions compute `rsETHAmount` via integer division that rounds down. When the deposited amount (after fee) is smaller than the rsETH-to-ETH rate, the division truncates to zero. The deposit proceeds without reverting, the user's ETH or token is consumed by the pool, and zero rsETH/wrsETH is minted or transferred. No minimum-output guard exists in any pool deposit path.

---

### Finding Description

`viewSwapRsETHAmountAndFee` in every pool variant computes the rsETH output as:

```solidity
rsETHAmount = amountAfterFee * 1e18 / rsETHToETHrate;   // ETH path
rsETHAmount = amountAfterFee * tokenToETHRate / rsETHToETHrate; // token path
```

Both divisions round down. When `amountAfterFee * 1e18 < rsETHToETHrate` (ETH path) or `amountAfterFee * tokenToETHRate < rsETHToETHrate` (token path), `rsETHAmount` evaluates to zero.

The deposit functions in every pool variant then proceed unconditionally:

**`RSETHPoolV3.deposit` (ETH path)**
```solidity
(uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount);
feeEarnedInETH += fee;
wrsETH.mint(msg.sender, rsETHAmount);   // mints 0 — user gets nothing
```

**`RSETHPoolNoWrapper.deposit` (ETH path)**
```solidity
(uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount);
feeEarnedInETH += fee;
rsETH.safeTransfer(msg.sender, rsETHAmount);  // transfers 0 — user gets nothing
```

There is no `require(rsETHAmount > 0)` guard and no `minRSETHAmountExpected` slippage parameter in any pool deposit function. The only input check is `if (amount == 0) revert InvalidAmount()`, which validates the input, not the output.

This is the direct analog of the Surge M-9 bug: a non-zero asset amount is consumed, but the corresponding share/token output rounds to zero, causing a silent mis-accounting that benefits existing holders at the depositor's expense. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) 

---

### Impact Explanation

A depositor sends a non-zero ETH or token amount and receives zero rsETH/wrsETH. The deposited ETH accumulates in the pool and is eventually bridged to L1, where it increases the total ETH backing rsETH without increasing the rsETH supply. This silently enriches all existing rsETH/wrsETH holders at the depositor's expense — a direct, permanent loss of the depositor's funds with no recourse.

**Impact class**: Low — Contract fails to deliver promised returns (depositor loses value without receiving the promised rsETH). [5](#0-4) 

---

### Likelihood Explanation

For ETH deposits, the rounding threshold is `amountAfterFee < rsETHToETHrate / 1e18`. Since `rsETHToETHrate ≈ 1.0x × 1e18`, the threshold is approximately 1 wei, making accidental triggering rare for normal deposits. For token deposits with low token-to-ETH rates (e.g., a token worth 0.001 ETH), the threshold rises to ~1050 token-wei, still small for 18-decimal tokens but potentially meaningful for 6-decimal tokens. The absence of any minimum-output parameter means users have no on-chain protection regardless of deposit size. [6](#0-5) [7](#0-6) 

---

### Recommendation

Add a zero-output guard in every pool deposit function, mirroring the protection already present in `LRTDepositPool._beforeDeposit`:

```solidity
// In every pool deposit() function, after computing rsETHAmount:
if (rsETHAmount == 0) revert InvalidAmount();
```

Optionally, expose a `minRsETHAmountExpected` parameter so callers can enforce their own slippage tolerance, consistent with `LRTDepositPool.depositETH` / `depositAsset`. [8](#0-7) 

---

### Proof of Concept

Assume `rsETHToETHrate = 1.05e18` (rsETH has accrued 5% staking yield) and `feeBps = 0`.

1. User calls `RSETHPoolV3.deposit{value: 1}("")` — deposits 1 wei of ETH.
2. `viewSwapRsETHAmountAndFee(1)` computes:
   - `fee = 1 * 0 / 10_000 = 0`
   - `amountAfterFee = 1`
   - `rsETHAmount = 1 * 1e18 / 1.05e18 = 0` (integer division truncates)
3. `feeEarnedInETH += 0` — no fee recorded.
4. `wrsETH.mint(msg.sender, 0)` — user receives 0 wrsETH.
5. The 1 wei ETH remains in the pool, to be bridged to L1 and credited to existing wrsETH holders.
6. The transaction succeeds with no revert; the user has permanently lost 1 wei with no rsETH received.

For the token path with a low-value token (`tokenToETHRate = 1e15`, i.e., 0.001 ETH/token), the threshold rises to `amountAfterFee < 1050` token-wei, meaning deposits of up to 1049 token-wei silently yield 0 rsETH. [1](#0-0) [4](#0-3)

### Citations

**File:** contracts/pools/RSETHPoolV3.sol (L256-264)
```text
        if (amount == 0) revert InvalidAmount();

        (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount);

        feeEarnedInETH += fee;

        wrsETH.mint(msg.sender, rsETHAmount);

        emit SwapOccurred(msg.sender, rsETHAmount, fee, referralId);
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

**File:** contracts/pools/RSETHPoolV3.sol (L315-335)
```text
    function viewSwapRsETHAmountAndFee(
        uint256 amount,
        address token
    )
        public
        view
        onlySupportedToken(token)
        returns (uint256 rsETHAmount, uint256 fee)
    {
        fee = amount * feeBps / 10_000;
        uint256 amountAfterFee = amount - fee;

        // rate of rsETH in ETH
        uint256 rsETHToETHrate = getRate();

        // rate of token in ETH
        uint256 tokenToETHRate = IOracle(supportedTokenOracle[token]).getRate();

        // Calculate the final rsETH amount
        rsETHAmount = amountAfterFee * tokenToETHRate / rsETHToETHrate;
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

**File:** contracts/pools/RSETHPoolNoWrapper.sol (L292-312)
```text
    function viewSwapRsETHAmountAndFee(
        uint256 amount,
        address token
    )
        public
        view
        onlySupportedToken(token)
        returns (uint256 rsETHAmount, uint256 fee)
    {
        fee = amount * feeBps / 10_000;
        uint256 amountAfterFee = amount - fee;

        // rate of rsETH in ETH
        uint256 rsETHToETHrate = getRate();

        // rate of token in ETH
        uint256 tokenToETHRate = IOracle(supportedTokenOracle[token]).getRate();

        // Calculate the final rsETH amount
        rsETHAmount = amountAfterFee * tokenToETHRate / rsETHToETHrate;
    }
```

**File:** contracts/LRTDepositPool.sol (L665-669)
```text
        rsethAmountToMint = getRsETHAmountToMint(asset, depositAmount);

        if (rsethAmountToMint < minRSETHAmountExpected) {
            revert MinimumAmountToReceiveNotMet();
        }
```
