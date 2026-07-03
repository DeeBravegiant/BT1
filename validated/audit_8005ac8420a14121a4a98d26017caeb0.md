### Title
Griefing DoS on `removeSupportedToken` via Direct Token Dust Transfer Permanently Blocks Token Delisting - (File: contracts/pools/RSETHPoolV3.sol, contracts/pools/RSETHPoolNoWrapper.sol, contracts/pools/RSETHPoolV3ExternalBridge.sol, contracts/pools/RSETHPoolV3WithNativeChainBridge.sol)

---

### Summary

All four production pool contracts guard `removeSupportedToken` with a raw `IERC20(token).balanceOf(address(this)) != 0` check. Because any address can transfer tokens directly to a contract, an unprivileged attacker can permanently prevent the protocol from ever delisting any supported token by donating a single wei of that token to the pool.

---

### Finding Description

Every pool variant contains the same pattern in `removeSupportedToken`:

```solidity
// RSETHPoolV3.sol  line 562
if (IERC20(token).balanceOf(address(this)) != 0) revert TokenBalanceNotZero();
```

The guard is intended to ensure no user funds remain before a token is delisted. However, it reads the raw ERC-20 balance of the contract, which includes tokens sent directly (i.e., not through the `deposit` flow). Because ERC-20 `transfer` is permissionless, any external actor can send 1 wei of a supported token to the pool at any time. After that transfer, `balanceOf(address(this))` is permanently non-zero (unless the bridger happens to drain it to exactly zero, which is operationally unlikely), and every subsequent call to `removeSupportedToken` for that token reverts with `TokenBalanceNotZero`.

The same guard appears verbatim in:
- `RSETHPoolV3.sol` line 562
- `RSETHPoolNoWrapper.sol` line 599
- `RSETHPoolV3ExternalBridge.sol` line 772
- `RSETHPoolV3WithNativeChainBridge.sol` line 609

This is the direct analog of the Origin Dollar finding: the contract uses `balanceOf(address(this))` without accounting for tokens that can be sent to it directly, causing a critical administrative function to fail.

---

### Impact Explanation

The `removeSupportedToken` function is the only mechanism by which the protocol can delist a token. If it is permanently blocked:

- A token whose oracle becomes stale, manipulated, or deprecated cannot be removed from the supported list.
- Users can continue depositing that token at a potentially incorrect exchange rate.
- The protocol cannot respond to a compromised or deprecated LST integration.

The contract fails to deliver its promised administrative capability (token delisting), without any direct loss of value at the moment of the attack. This maps to **Low: Contract fails to deliver promised returns, but doesn't lose value** — with a realistic escalation path to Medium if the blocked token's oracle degrades.

---

### Likelihood Explanation

The attack requires no privilege, no capital at risk, and no complex setup. The attacker only needs to call `IERC20(token).transfer(poolAddress, 1)` for any supported token (e.g., wstETH). This is a one-transaction, zero-cost griefing attack that any external actor can execute at any time, including front-running a pending `removeSupportedToken` governance transaction.

---

### Recommendation

Replace the raw `balanceOf` check with a tracked internal accounting variable (e.g., `totalDeposited[token]` decremented on bridging), so that only protocol-originated balances are considered. Alternatively, allow `removeSupportedToken` to proceed regardless of balance and handle any residual balance by transferring it to a recovery address, rather than blocking the entire operation.

---

### Proof of Concept

1. The protocol supports wstETH in `RSETHPoolV3ExternalBridge` (or any pool variant).
2. Attacker calls `IERC20(wstETH).transfer(poolAddress, 1)` — costs ~1 wei of wstETH.
3. `IERC20(wstETH).balanceOf(poolAddress)` is now `>= 1`.
4. Admin (TIMELOCK_ROLE) calls `removeSupportedToken(wstETH, 0)`.
5. Execution reaches line 772 of `RSETHPoolV3ExternalBridge.sol`:
   ```solidity
   if (IERC20(token).balanceOf(address(this)) != 0) revert TokenBalanceNotZero();
   ```
6. The call reverts. The attacker can repeat step 2 after any bridging event that drains the balance, making the DoS permanent with negligible cost. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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

**File:** contracts/pools/RSETHPoolV3WithNativeChainBridge.sol (L606-616)
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
