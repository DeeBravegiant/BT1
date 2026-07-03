### Title
Zero wrsETH Minted for Small ETH/Token Deposits Due to Integer Division Truncation in `viewSwapRsETHAmountAndFee` - (File: contracts/pools/RSETHPoolV2.sol, RSETHPoolV3.sol, RSETHPool.sol, RSETHPoolNoWrapper.sol, RSETHPoolV2ExternalBridge.sol, RSETHPoolV3ExternalBridge.sol, RSETHPoolV3WithNativeChainBridge.sol)

---

### Summary

All L2 RSETHPool variants compute the wrsETH output amount using integer division that can truncate to zero for small deposits. When this happens, the user's ETH or token is accepted by the pool but zero wrsETH is minted, causing the depositor to lose their funds with no recourse.

---

### Finding Description

Every pool contract in the `contracts/pools/` directory computes the rsETH output via `viewSwapRsETHAmountAndFee`:

```solidity
// RSETHPoolV2.sol line 233
rsETHAmount = amountAfterFee * 1e18 / rsETHToETHrate;
```

`rsETHToETHrate` is the rsETH/ETH exchange rate returned by the oracle, which is always ≥ 1e18 and grows over time as staking rewards accrue. [1](#0-0) 

Because Solidity performs truncating integer division, whenever `amountAfterFee * 1e18 < rsETHToETHrate`, the result is 0. For example, if the current rate is 1.05e18 (rsETH worth 1.05 ETH), a deposit of 1 wei ETH yields:

```
1 * 1e18 / 1.05e18 = 0   (truncated)
```

The `deposit` function checks only that `amount != 0`, but never checks that the computed `rsETHAmount` is non-zero before calling `wrsETH.mint(msg.sender, rsETHAmount)`:

```solidity
// RSETHPoolV2.sol lines 207-218
function deposit(string memory referralId) external payable nonReentrant whenNotPaused limitDailyMint(msg.value) {
    uint256 amount = msg.value;
    if (amount == 0) revert InvalidAmount();
    (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount);
    feeEarnedInETH += fee;
    wrsETH.mint(msg.sender, rsETHAmount);   // rsETHAmount can be 0
    emit SwapOccurred(msg.sender, rsETHAmount, fee, referralId);
}
``` [2](#0-1) 

The same pattern is present in every pool variant: [3](#0-2) [4](#0-3) [5](#0-4) 

The token-deposit overload has the same flaw with `amountAfterFee * tokenToETHRate / rsETHToETHrate`: [6](#0-5) 

---

### Impact Explanation

A depositor who sends a small ETH or token amount (specifically any amount where `amountAfterFee * 1e18 < rsETHToETHrate`) has their funds accepted by the pool contract but receives 0 wrsETH in return. The ETH/token remains in the pool, effectively enriching existing wrsETH holders. The depositor has no mechanism to recover the lost funds. This matches **Low — Contract fails to deliver promised returns, but doesn't lose value** from the allowed impact scope.

---

### Likelihood Explanation

As rsETH appreciates over time (rate grows beyond 1e18), the threshold below which deposits round to zero increases. Any user who accidentally sends a dust amount (e.g., 1 wei) through a script, wallet rounding, or direct contract call will silently lose those funds. The pool contracts are publicly callable with no minimum deposit enforcement, making this reachable by any external caller.

---

### Recommendation

Add a zero-check on the computed `rsETHAmount` before minting, and revert if it is zero:

```solidity
(uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount);
if (rsETHAmount == 0) revert InvalidAmount();
```

This mirrors the protection already present in `LRTDepositPool._beforeDeposit`, which reverts when `rsethAmountToMint < minRSETHAmountExpected` (and callers pass a non-zero minimum). [7](#0-6) 

---

### Proof of Concept

Assume `rsETHToETHrate = 1.05e18` (rsETH has appreciated 5% since launch, a realistic near-term value).

1. Attacker or user calls `RSETHPoolV2.deposit{value: 1}("")` (1 wei ETH).
2. `viewSwapRsETHAmountAndFee(1)` computes:
   - `fee = 1 * feeBps / 10_000` → 0 (rounds down for any feeBps < 10_000)
   - `amountAfterFee = 1`
   - `rsETHAmount = 1 * 1e18 / 1.05e18 = 0` (truncated)
3. `feeEarnedInETH += 0` (no fee recorded).
4. `wrsETH.mint(msg.sender, 0)` — mints nothing.
5. The 1 wei ETH is now held by the pool contract with no corresponding wrsETH issued.
6. The depositor has permanently lost 1 wei with no wrsETH received.

The same scenario applies to token deposits via `deposit(address token, uint256 amount, ...)` when `amountAfterFee * tokenToETHRate < rsETHToETHrate`. [8](#0-7)

### Citations

**File:** contracts/pools/RSETHPoolV2.sol (L207-234)
```text
    function deposit(string memory referralId) external payable nonReentrant whenNotPaused limitDailyMint(msg.value) {
        uint256 amount = msg.value;

        if (amount == 0) revert InvalidAmount();

        (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount);

        feeEarnedInETH += fee;

        wrsETH.mint(msg.sender, rsETHAmount);

        emit SwapOccurred(msg.sender, rsETHAmount, fee, referralId);
    }

    /// @dev view function to get the rsETH amount for a given amount of ETH
    /// @param amount The amount of ETH
    /// @return rsETHAmount The amount of rsETH that will be received
    /// @return fee The fee that will be charged
    function viewSwapRsETHAmountAndFee(uint256 amount) public view returns (uint256 rsETHAmount, uint256 fee) {
        fee = amount * feeBps / 10_000;
        uint256 amountAfterFee = amount - fee;

        // rate of rsETH in ETH
        uint256 rsETHToETHrate = getRate();

        // Calculate the final rsETH amount
        rsETHAmount = amountAfterFee * 1e18 / rsETHToETHrate;
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

**File:** contracts/pools/RSETHPoolV3.sol (L324-335)
```text
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

**File:** contracts/pools/RSETHPoolV3WithNativeChainBridge.sol (L335-344)
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

**File:** contracts/LRTDepositPool.sol (L665-669)
```text
        rsethAmountToMint = getRsETHAmountToMint(asset, depositAmount);

        if (rsethAmountToMint < minRSETHAmountExpected) {
            revert MinimumAmountToReceiveNotMet();
        }
```
