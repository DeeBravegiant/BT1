### Title
Dust Donation Permanently Blocks `removeSupportedToken` Admin Function - (File: contracts/pools/RSETHPoolV3ExternalBridge.sol, contracts/pools/RSETHPoolV3.sol, contracts/pools/RSETHPool.sol, contracts/pools/RSETHPoolNoWrapper.sol)

---

### Summary

Every pool variant's `removeSupportedToken` function guards removal with a strict `balanceOf(address(this)) != 0` check. Any unprivileged user holding even 1 wei of a supported token can donate that dust to the pool contract and permanently block the admin/timelock from removing that token, because the check will never pass again.

---

### Finding Description

In all four pool contracts the removal guard is identical:

`RSETHPoolV3ExternalBridge.sol`:
```solidity
if (IERC20(token).balanceOf(address(this)) != 0) revert TokenBalanceNotZero();
```

`RSETHPoolV3.sol`:
```solidity
if (IERC20(token).balanceOf(address(this)) != 0) revert TokenBalanceNotZero();
```

`RSETHPool.sol`:
```solidity
if (IERC20(token).balanceOf(address(this)) != 0) revert TokenBalanceNotZero();
```

`RSETHPoolNoWrapper.sol`:
```solidity
if (IERC20(token).balanceOf(address(this)) != 0) revert TokenBalanceNotZero();
```

The intent is to ensure no user funds are stranded when a token is delisted. However, because the check is `!= 0` rather than `<= dust_threshold`, a single wei of the token transferred directly to the pool (bypassing the `deposit` path, so no rsETH is minted) satisfies the revert condition forever. The attacker pays only the gas cost of one ERC-20 transfer.

The attack is also frontrunnable: an attacker watching the mempool can observe a pending `removeSupportedToken` transaction and insert a 1-wei `transfer` to the pool address before it lands, causing the removal to revert. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) 

---

### Impact Explanation

**Low — Contract fails to deliver promised returns, but doesn't lose value.**

The administrative invariant that a depegged, deprecated, or compromised token can be cleanly removed from the supported list is permanently broken for any token targeted by the attacker. The `supportedTokenList` array cannot be pruned, and the token's oracle and bridge mappings cannot be deleted via the intended path. While the admin retains the ability to pause the contract or update the oracle address, the `removeSupportedToken` code path — which is the only mechanism to delete `supportedTokenOracle[token]`, `tokenBridge[token]`, and shrink `supportedTokenList` — is rendered permanently inoperable for the targeted token at negligible cost to the attacker.

---

### Likelihood Explanation

**Medium.** The attack requires only that the attacker holds 1 wei of any supported token (e.g., wstETH, which is freely tradeable). The cost is a single ERC-20 transfer. The attack is repeatable: every time the admin attempts removal, the attacker can re-donate. Frontrunning is straightforward on any chain where the mempool is visible. The motivation exists whenever a token is being delisted due to a security event, because keeping the token listed may allow continued deposits at a stale oracle rate.

---

### Recommendation

Replace the strict equality check with a dust threshold, mirroring the fix suggested in the reference report:

```solidity
// Before
if (IERC20(token).balanceOf(address(this)) != 0) revert TokenBalanceNotZero();

// After
uint256 dust = 1000; // configurable threshold
if (IERC20(token).balanceOf(address(this)) > dust) revert TokenBalanceNotZero();
```

Alternatively, add a privileged `sweepToken` function that lets the admin drain any residual balance before removal, so the balance can always be brought to zero through a legitimate path before calling `removeSupportedToken`.

---

### Proof of Concept

1. Protocol has wstETH as a supported token in `RSETHPoolV3ExternalBridge` with a non-zero balance from normal user deposits.
2. Admin bridges all user wstETH to L1 via `bridgeTokens(wstETH)`, bringing `getTokenBalanceMinusFees(wstETH)` to 0. However, `feeEarnedInToken[wstETH]` may still be non-zero, or the admin calls `withdrawFees` to drain fees too, leaving `balanceOf(pool, wstETH) == 0`.
3. Admin submits `removeSupportedToken(wstETH, index)` via the timelock.
4. Attacker observes the pending transaction and calls `IERC20(wstETH).transfer(pool, 1)` with higher gas, landing first.
5. `removeSupportedToken` executes: `IERC20(wstETH).balanceOf(address(this))` returns `1 != 0`, reverts with `TokenBalanceNotZero`.
6. wstETH remains permanently in `supportedTokenList`, `supportedTokenOracle`, and `tokenBridge`. The attacker can repeat step 4 indefinitely. [1](#0-0) [2](#0-1)

### Citations

**File:** contracts/pools/RSETHPoolV3ExternalBridge.sol (L769-779)
```text
    function removeSupportedToken(address token, uint256 tokenIndex) external onlyRole(TIMELOCK_ROLE) {
        UtilLib.checkNonZeroAddress(token);
        if (supportedTokenList[tokenIndex] != token) revert TokenNotFoundError();
        if (IERC20(token).balanceOf(address(this)) != 0) revert TokenBalanceNotZero();

        delete supportedTokenOracle[token];
        delete tokenBridge[token];
        supportedTokenList[tokenIndex] = supportedTokenList[supportedTokenList.length - 1];
        supportedTokenList.pop();
        emit RemovedSupportedToken(token);
    }
```

**File:** contracts/pools/RSETHPoolV3.sol (L559-568)
```text
    function removeSupportedToken(address token, uint256 tokenIndex) external onlyRole(TIMELOCK_ROLE) {
        UtilLib.checkNonZeroAddress(token);
        if (supportedTokenList[tokenIndex] != token) revert TokenNotFoundError();
        if (IERC20(token).balanceOf(address(this)) != 0) revert TokenBalanceNotZero();

        delete supportedTokenOracle[token];
        supportedTokenList[tokenIndex] = supportedTokenList[supportedTokenList.length - 1];
        supportedTokenList.pop();
        emit RemovedSupportedToken(token);
    }
```

**File:** contracts/pools/RSETHPool.sol (L660-670)
```text
    function removeSupportedToken(address token, uint256 tokenIndex) external onlyRole(TIMELOCK_ROLE) {
        UtilLib.checkNonZeroAddress(token);
        if (supportedTokenList[tokenIndex] != token) revert TokenNotFoundError();
        if (IERC20(token).balanceOf(address(this)) != 0) revert TokenBalanceNotZero();

        delete supportedTokenOracle[token];
        delete tokenBridge[token];
        supportedTokenList[tokenIndex] = supportedTokenList[supportedTokenList.length - 1];
        supportedTokenList.pop();
        emit RemovedSupportedToken(token);
    }
```

**File:** contracts/pools/RSETHPoolNoWrapper.sol (L596-606)
```text
    function removeSupportedToken(address token, uint256 tokenIndex) external onlyRole(TIMELOCK_ROLE) {
        UtilLib.checkNonZeroAddress(token);
        if (supportedTokenList[tokenIndex] != token) revert TokenNotFoundError();
        if (IERC20(token).balanceOf(address(this)) != 0) revert TokenBalanceNotZero();

        delete supportedTokenOracle[token];
        delete tokenBridge[token];
        supportedTokenList[tokenIndex] = supportedTokenList[supportedTokenList.length - 1];
        supportedTokenList.pop();
        emit RemovedSupportedToken(token);
    }
```
