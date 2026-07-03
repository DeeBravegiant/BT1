### Title
Strict Token Balance Check in `removeSupportedToken` Can Be Permanently Griefed by Sending 1 Wei - (File: contracts/pools/RSETHPoolNoWrapper.sol, contracts/pools/RSETHPool.sol, contracts/pools/RSETHPoolV3.sol)

### Summary
The `removeSupportedToken` function in multiple L2 pool contracts enforces a strict `balanceOf(address(this)) != 0` check before allowing a token to be removed from the supported list. Any unprivileged user can send 1 wei of a supported token directly to the pool contract, permanently preventing the `TIMELOCK_ROLE` from ever removing that token.

### Finding Description
In `RSETHPoolNoWrapper.sol`, `RSETHPool.sol`, and `RSETHPoolV3.sol`, the `removeSupportedToken` function contains the following guard:

```solidity
if (IERC20(token).balanceOf(address(this)) != 0) revert TokenBalanceNotZero();
```

This check is intended to ensure no token balance remains before removal. However, because ERC-20 tokens can be transferred to any address without the recipient's consent, any external actor can send 1 wei of a supported token to the pool contract at any time. Once this is done, the strict `!= 0` check will always revert, making `removeSupportedToken` permanently uncallable for that token.

The same pattern appears identically in all three contracts:

- `RSETHPoolNoWrapper.sol` line 599
- `RSETHPool.sol` line 663
- `RSETHPoolV3.sol` line 562

### Impact Explanation
The `TIMELOCK_ROLE` loses the ability to remove a supported token from the pool. This means:
- A deprecated, paused, or otherwise problematic token cannot be delisted from the pool.
- The pool continues to accept user deposits of the griefed token indefinitely.
- Protocol management of the supported token list is permanently broken for the targeted token.

This matches the **Low** severity category: *Contract fails to deliver promised returns, but doesn't lose value.*

### Likelihood Explanation
The attack requires only sending 1 wei of any supported ERC-20 token to the pool contract. This is trivially cheap, requires no special permissions, and can be repeated after any recovery attempt (e.g., if the operator bridges out the balance, the attacker can immediately re-grief). The attack is permanent and costless to sustain.

### Recommendation
Replace the strict `!= 0` check with a threshold-based check (similar to how `LRTDepositPool._checkResidueLSTBalance` uses `maxNegligibleAmount`), or use a balance-delta approach that compares the balance before and after a sweep operation rather than requiring an exact zero balance:

```solidity
// Instead of:
if (IERC20(token).balanceOf(address(this)) != 0) revert TokenBalanceNotZero();

// Use a negligible-amount threshold:
if (IERC20(token).balanceOf(address(this)) > maxNegligibleAmount) revert TokenBalanceNotZero();
```

Alternatively, allow the admin to sweep residual token balances to a treasury address before removal, so the balance can be brought to zero in a controlled way that cannot be re-griefed atomically.

### Proof of Concept
1. Pool is deployed on Arbitrum/Unichain with token `T` in `supportedTokenList`.
2. Attacker calls `T.transfer(poolAddress, 1)` — costs ~1 wei + gas.
3. `TIMELOCK_ROLE` calls `removeSupportedToken(T, index)`.
4. Execution reaches `if (IERC20(T).balanceOf(address(this)) != 0) revert TokenBalanceNotZero();` — balance is 1 wei, so it reverts.
5. Attacker repeats step 2 after any operator sweep, permanently blocking removal. [1](#0-0) [2](#0-1) [3](#0-2)

### Citations

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
