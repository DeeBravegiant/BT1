### Title
Fee-on-Transfer Token Accounting Mismatch Permanently Freezes Token Fees and Principal — (`contracts/pools/RSETHPool.sol`)

---

### Summary

`deposit(address,uint256,string)` computes `feeEarnedInToken[token]` using the caller-supplied `amount`, but `safeTransferFrom` may credit the pool with fewer tokens if the token charges a transfer-side fee. The accumulated accounting surplus causes `getTokenBalanceMinusFees` to underflow, permanently bricking `bridgeTokens`, `moveAssetsForBridging`, and `withdrawFees` for that token.

---

### Finding Description

In `deposit(address,uint256,string)`:

```solidity
// line 296 — pool receives amount minus token's own transfer fee
IERC20(token).safeTransferFrom(msg.sender, address(this), amount);

// line 298-300 — fee computed on the full input `amount`, not actual received
(uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount, token);
feeEarnedInToken[token] += fee;   // fee = amount * tokenFeeBps / 10_000
``` [1](#0-0) 

`viewSwapRsETHAmountAndFee` computes the fee on the raw `amount`:

```solidity
fee = amount * feeBpsForToken / 10_000;
``` [2](#0-1) 

If the token deducts, say, 1% on every transfer, the pool receives `0.99 * amount` but `feeEarnedInToken[token]` grows by `amount * tokenFeeBps / 10_000`. After enough deposits the invariant `feeEarnedInToken[token] <= IERC20(token).balanceOf(address(this))` breaks.

`getTokenBalanceMinusFees` then underflows (Solidity 0.8.27 checked arithmetic):

```solidity
return IERC20(token).balanceOf(address(this)) - feeEarnedInToken[token]; // reverts
``` [3](#0-2) 

Every downstream function that calls this view reverts permanently:

- `bridgeTokens` — line 556 calls `getTokenBalanceMinusFees` [4](#0-3) 
- `moveAssetsForBridging(token, amount)` — line 472 calls `getTokenBalanceMinusFees` [5](#0-4) 
- `withdrawFees(receiver, token)` — line 440 attempts `safeTransfer` of `feeEarnedInToken[token]` which exceeds the actual balance, causing the ERC-20 transfer to revert [6](#0-5) 

---

### Impact Explanation

The correct impact classification is **Medium — Permanent freezing of unclaimed yield** (fees) and, more severely, **Critical — Permanent freezing of funds** (principal deposits), because `bridgeTokens` and `moveAssetsForBridging` are also bricked. All token balances held by the pool for that token become permanently unrecoverable with no admin escape hatch.

---

### Likelihood Explanation

The precondition is that a fee-on-transfer token is added via `addSupportedToken` (TIMELOCK_ROLE) and assigned a non-zero `tokenFeeBps` (DEFAULT_ADMIN_ROLE). This does **not** require admin compromise — it requires only that an admin legitimately adds a deflationary or rebasing token without auditing its transfer mechanics. The contract performs no check that the token is non-deflationary. The likelihood is low for the current token set (wstETH-like LSTs), but the contract is generic and the bug is latent for any future token addition.

---

### Recommendation

Use a balance-before/after pattern to determine the actual received amount, and base all fee accounting on that:

```solidity
uint256 balanceBefore = IERC20(token).balanceOf(address(this));
IERC20(token).safeTransferFrom(msg.sender, address(this), amount);
uint256 actualReceived = IERC20(token).balanceOf(address(this)) - balanceBefore;

(uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(actualReceived, token);
feeEarnedInToken[token] += fee;
```

Alternatively, explicitly disallow fee-on-transfer tokens in `addSupportedToken` by performing a test transfer and verifying the received amount equals the sent amount.

---

### Proof of Concept

```solidity
// 1. Deploy MockFeeToken: 1% fee on every transfer
// 2. addSupportedToken(mockFeeToken, oracle, bridge)  [TIMELOCK_ROLE]
// 3. setTokenFeeBps(mockFeeToken, 500)                [DEFAULT_ADMIN_ROLE]
// 4. Fund pool with wrsETH for swaps
// 5. Loop N times: attacker calls deposit(mockFeeToken, 1e18, "")
//    - Pool receives 0.99e18 per iteration
//    - feeEarnedInToken grows by 0.05e18 per iteration (5% of 1e18)
// 6. After enough iterations:
//    feeEarnedInToken[token] > IERC20(token).balanceOf(pool)
// 7. Assert: bridgeTokens(mockFeeToken) reverts (underflow in getTokenBalanceMinusFees)
// 8. Assert: withdrawFees(receiver, mockFeeToken) reverts (transfer exceeds balance)
// 9. All token funds in pool are permanently frozen
```

### Citations

**File:** contracts/pools/RSETHPool.sol (L296-300)
```text
        IERC20(token).safeTransferFrom(msg.sender, address(this), amount);

        (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount, token);

        feeEarnedInToken[token] += fee;
```

**File:** contracts/pools/RSETHPool.sol (L335-336)
```text
        uint256 feeBpsForToken = tokenFeeBps[token];
        fee = amount * feeBpsForToken / 10_000;
```

**File:** contracts/pools/RSETHPool.sol (L396-398)
```text
    function getTokenBalanceMinusFees(address token) public view returns (uint256) {
        return IERC20(token).balanceOf(address(this)) - feeEarnedInToken[token];
    }
```

**File:** contracts/pools/RSETHPool.sol (L438-440)
```text
        uint256 amountToSendInToken = feeEarnedInToken[token];
        feeEarnedInToken[token] = 0;
        IERC20(token).safeTransfer(receiver, amountToSendInToken);
```

**File:** contracts/pools/RSETHPool.sol (L472-473)
```text
        uint256 tokenBalanceMinusFees = getTokenBalanceMinusFees(token);
        if (amount > tokenBalanceMinusFees) revert InsufficientBalanceInPool();
```

**File:** contracts/pools/RSETHPool.sol (L556-556)
```text
        uint256 balance = getTokenBalanceMinusFees(token);
```
