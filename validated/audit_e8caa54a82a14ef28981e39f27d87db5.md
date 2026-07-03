### Title
Attacker Can Permanently Grief `removeSupportedToken` via Direct Token Transfer - (File: contracts/pools/RSETHPoolV3.sol, contracts/pools/RSETHPool.sol, contracts/pools/RSETHPoolNoWrapper.sol)

### Summary
The `removeSupportedToken` function in `RSETHPoolV3`, `RSETHPool`, and `RSETHPoolNoWrapper` enforces a strict `balanceOf(address(this)) == 0` invariant before allowing a token to be delisted. Because any ERC20 token can be transferred directly to the contract by any holder, an unprivileged attacker can permanently prevent the protocol from removing any supported token by maintaining a dust balance in the pool.

### Finding Description
In all three pool contracts, `removeSupportedToken` contains the following guard:

`RSETHPoolV3.sol` line 562:
```solidity
if (IERC20(token).balanceOf(address(this)) != 0) revert TokenBalanceNotZero();
```

`RSETHPool.sol` line 663:
```solidity
if (IERC20(token).balanceOf(address(this)) != 0) revert TokenBalanceNotZero();
```

`RSETHPoolNoWrapper.sol` line 599:
```solidity
if (IERC20(token).balanceOf(address(this)) != 0) revert TokenBalanceNotZero();
```

The check uses the live `balanceOf(address(this))` rather than an internal accounting variable. Any holder of the supported token can call `token.transfer(poolAddress, 1)` at any time — including in the same block as the admin's `removeSupportedToken` call — to ensure the balance is never zero. Because the admin's only tool to drain the balance (`moveAssetsForBridging` / `bridgeTokens`) is a separate transaction, the attacker can front-run the removal call every time, making the invariant permanently unbreakable without a multicall or atomic wrapper that does not exist in the current codebase.

### Impact Explanation
The protocol cannot delist a supported token. If a token becomes malicious, deprecated, or exploitable (e.g., a rebasing token that inflates `feeEarnedInToken` accounting, or a token whose oracle is compromised), the admin is unable to remove it from the accepted asset list. Users can continue depositing the compromised token and receiving `wrsETH` / `rsETH` in return, leading to protocol insolvency or theft of yield from honest depositors. The impact is **permanent freezing of a governance operation** that is the only on-chain mechanism to delist a bad asset.

### Likelihood Explanation
The attack requires only a single ERC20 `transfer` call of 1 wei of any supported token. Any holder of a supported token (e.g., wstETH, stETH, ETHx) can execute this. The attacker must repeat the transfer each time the admin drains the balance, which is trivially automatable via a bot watching the mempool. No special permissions, capital, or complex setup are required.

### Recommendation
Replace the live `balanceOf` check with an internal accounting variable that tracks only protocol-received deposits, analogous to the delta-balance pattern recommended in the original CNote report:

```solidity
// Track internal balance separately
mapping(address token => uint256 internalBalance) public internalTokenBalance;

// In deposit():
internalTokenBalance[token] += amount;

// In moveAssetsForBridging() / bridgeTokens():
internalTokenBalance[token] -= amountBridged;

// In removeSupportedToken():
if (internalTokenBalance[token] != 0) revert TokenBalanceNotZero();
```

This ensures that tokens forcibly transferred to the contract by an attacker do not affect the removal guard.

### Proof of Concept
1. Protocol has `wstETH` as a supported token in `RSETHPoolV3`.
2. Admin decides to delist `wstETH` and calls `moveAssetsForBridging(wstETH, balance)` to drain the pool balance to zero.
3. Attacker observes the drain transaction in the mempool and submits `wstETH.transfer(RSETHPoolV3Address, 1)` with a higher gas price, front-running the subsequent `removeSupportedToken(wstETH, index)` call.
4. When `removeSupportedToken` executes, `IERC20(wstETH).balanceOf(address(this)) == 1 != 0`, so it reverts with `TokenBalanceNotZero`.
5. The attacker repeats step 3 indefinitely. `wstETH` can never be removed from the supported token list. [1](#0-0) [2](#0-1) [3](#0-2)

### Citations

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
