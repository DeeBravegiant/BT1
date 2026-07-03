### Title
Zero-Output Token Deposit Permanently Loses Depositor Funds Due to Missing `rsETHAmount == 0` Guard - (File: contracts/pools/RSETHPoolV3.sol)

---

### Summary

`RSETHPoolV3.deposit(address token, uint256 amount, string referralId)` transfers the depositor's token into the pool and then calls `wrsETH.mint(msg.sender, rsETHAmount)` without ever checking whether `rsETHAmount` is zero. When WETH is the deposit token and the rsETH/ETH rate exceeds `1e18`, a deposit of 1 wei of WETH produces `rsETHAmount = 0` due to integer division truncation. The WETH is transferred in, zero wrsETH is minted, and the depositor has no recovery path.

---

### Finding Description

`WETHOracle.getRate()` always returns exactly `1e18`. [1](#0-0) 

The token-deposit variant of `viewSwapRsETHAmountAndFee` computes:

```
rsETHAmount = amountAfterFee * tokenToETHRate / rsETHToETHrate
``` [2](#0-1) 

With WETH as the token, `tokenToETHRate = 1e18`. When `rsETHToETHrate > 1e18` (rsETH has accrued yield, e.g. `1.05e18`), any `amountAfterFee` smaller than `rsETHToETHrate / 1e18 = 1` (i.e., `amountAfterFee = 0` or `1`) produces `rsETHAmount = 0` by Solidity integer truncation.

The `deposit` function only guards against `amount == 0`; it does **not** guard against `rsETHAmount == 0`: [3](#0-2) 

Execution flow for `amount = 1 wei`, `feeBps = 0`, `rsETHToETHrate = 1.05e18`:

1. `amount == 0` → false, passes.
2. `safeTransferFrom` → 1 wei WETH transferred to pool. ✓
3. `viewSwapRsETHAmountAndFee(1, WETH)` → `fee = 0`, `amountAfterFee = 1`, `rsETHAmount = 1 * 1e18 / 1.05e18 = 0`.
4. `feeEarnedInToken[WETH] += 0` → fee tracking unchanged.
5. `wrsETH.mint(msg.sender, 0)` → OpenZeppelin ERC20 `_mint(address, 0)` does **not** revert; it emits a Transfer event for 0 and returns.
6. Transaction succeeds. Depositor holds 0 wrsETH. 1 wei WETH is in the pool.

The `limitDailyMint` modifier also silently passes when `rsETHAmount = 0` because `0 + 0 > dailyMintLimit` is always false: [4](#0-3) 

The same pattern is present in `RSETHPoolV3ExternalBridge` and `RSETHPoolV3WithNativeChainBridge`: [5](#0-4) [6](#0-5) 

---

### Impact Explanation

The depositor's WETH is permanently lost to them. The 1 wei enters the pool's general token balance (`getTokenBalanceMinusFees`) and will eventually be bridged to L1 via `bridgeTokens` — the depositor has no function to reclaim it. There is no `withdrawDeposit`, no refund path, and no minimum-output slippage parameter on the token deposit path. The depositor receives nothing in return.

The per-transaction loss is bounded to dust amounts (1 wei of WETH = negligible ETH value), so the practical financial impact is extremely low. However, the invariant "a non-zero deposit always produces a non-zero output" is broken, and the funds are unrecoverable by the depositor.

---

### Likelihood Explanation

This requires `rsETHToETHrate > 1e18`, which is the normal operating state once rsETH has accrued any yield. It requires depositing exactly 1 wei of WETH (or a similarly tiny amount). A rational user would not do this intentionally, but a buggy integration, a UI rounding error, or a deliberate dust-deposit could trigger it. The condition is always present in production once rsETH appreciates.

---

### Recommendation

Add a post-computation guard in `deposit` (and in `viewSwapRsETHAmountAndFee` or as a modifier):

```solidity
if (rsETHAmount == 0) revert InvalidAmount();
```

Insert this check immediately after computing `rsETHAmount` and before `wrsETH.mint`. Apply the same fix to all pool variants (`RSETHPoolV3`, `RSETHPoolV3ExternalBridge`, `RSETHPoolV3WithNativeChainBridge`, `RSETHPool`, `RSETHPoolNoWrapper`).

---

### Proof of Concept

```solidity
// Pseudocode unit test (local fork or mock)
// Setup:
//   rsETHOracle.getRate() returns 1.05e18
//   WETH is a supported token with WETHOracle (returns 1e18)
//   feeBps = 0 (or any value where 1 * (10000 - feeBps) / 10000 == 0 or 1)

uint256 wethBefore = WETH.balanceOf(address(pool));
uint256 wrsETHBefore = wrsETH.balanceOf(depositor);

vm.prank(depositor);
WETH.approve(address(pool), 1);
vm.prank(depositor);
pool.deposit(address(WETH), 1, "");  // succeeds, no revert

uint256 wethAfter = WETH.balanceOf(address(pool));
uint256 wrsETHAfter = wrsETH.balanceOf(depositor);

assertEq(wethAfter - wethBefore, 1);       // 1 wei WETH transferred in
assertEq(wrsETHAfter - wrsETHBefore, 0);   // 0 wrsETH minted
// depositor's 1 wei WETH is permanently lost
```

### Citations

**File:** contracts/pools/oracle/WETHOracle.sol (L7-9)
```text
    function getRate() external pure returns (uint256) {
        return 1e18;
    }
```

**File:** contracts/pools/RSETHPoolV3.sol (L119-123)
```text
        if (dailyMintAmount + rsETHAmount > dailyMintLimit) {
            revert DailyMintLimitExceeded();
        }

        dailyMintAmount += rsETHAmount;
```

**File:** contracts/pools/RSETHPoolV3.sol (L282-292)
```text
        if (amount == 0) revert InvalidAmount();

        IERC20(token).safeTransferFrom(msg.sender, address(this), amount);

        (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount, token);

        feeEarnedInToken[token] += fee;

        wrsETH.mint(msg.sender, rsETHAmount);

        emit SwapOccurred(msg.sender, rsETHAmount, fee, referralId, token);
```

**File:** contracts/pools/RSETHPoolV3.sol (L324-334)
```text
        fee = amount * feeBps / 10_000;
        uint256 amountAfterFee = amount - fee;

        // rate of rsETH in ETH
        uint256 rsETHToETHrate = getRate();

        // rate of token in ETH
        uint256 tokenToETHRate = IOracle(supportedTokenOracle[token]).getRate();

        // Calculate the final rsETH amount
        rsETHAmount = amountAfterFee * tokenToETHRate / rsETHToETHrate;
```

**File:** contracts/pools/RSETHPoolV3ExternalBridge.sol (L401-411)
```text
        if (amount == 0) revert InvalidAmount();

        IERC20(token).safeTransferFrom(msg.sender, address(this), amount);

        (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount, token);

        feeEarnedInToken[token] += fee;

        wrsETH.mint(msg.sender, rsETHAmount);

        emit SwapOccurred(msg.sender, rsETHAmount, fee, referralId, token);
```

**File:** contracts/pools/RSETHPoolV3WithNativeChainBridge.sol (L318-328)
```text
        if (amount == 0) revert InvalidAmount();

        IERC20(token).safeTransferFrom(msg.sender, address(this), amount);

        (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount, token);

        feeEarnedInToken[token] += fee;

        wrsETH.mint(msg.sender, rsETHAmount);

        emit SwapOccurred(msg.sender, rsETHAmount, fee, referralId, token);
```
