### Title
Attacker Can Permanently DOS `removeSupportedToken` via Dust Token Donation - (File: `contracts/pools/RSETHPool.sol`, `contracts/pools/RSETHPoolV3ExternalBridge.sol`, `contracts/pools/RSETHPoolNoWrapper.sol`, `contracts/pools/RSETHPoolV3.sol`, `contracts/pools/RSETHPoolV3WithNativeChainBridge.sol`)

---

### Summary

All five L2 pool variants share a `removeSupportedToken` function that guards removal with a strict `balanceOf != 0` check. Any unprivileged actor can send 1 wei of the target token directly to the pool contract, making the balance permanently non-zero and causing every future `removeSupportedToken` call to revert with `TokenBalanceNotZero`. The root cause is structurally identical to the Ajna M-9 report: a strict equality/inequality check on a live on-chain balance that any external party can manipulate.

---

### Finding Description

Every pool variant contains the following guard inside `removeSupportedToken`:

```solidity
// RSETHPool.sol  (line 663)
if (IERC20(token).balanceOf(address(this)) != 0) revert TokenBalanceNotZero();
```

The intent is to ensure all token inventory has been bridged to L1 before the token is delisted. However, `balanceOf` is a live, externally writable value: any address can call `IERC20(token).transfer(poolAddress, 1)` at any time. Once even 1 wei of the token sits in the pool, the strict `!= 0` check will always revert, and there is no recovery path — no sweep function, no override, no admin bypass — that clears the balance and retries the removal.

The same pattern appears verbatim in all five pool contracts:

- `contracts/pools/RSETHPool.sol` — line 663
- `contracts/pools/RSETHPoolV3ExternalBridge.sol` — line 772
- `contracts/pools/RSETHPoolNoWrapper.sol` — (same pattern, `TokenBalanceNotZero` error declared at line 98)
- `contracts/pools/RSETHPoolV3.sol` — same pattern
- `contracts/pools/RSETHPoolV3WithNativeChainBridge.sol` — same pattern

---

### Impact Explanation

**Low — Contract fails to deliver promised returns, but doesn't lose value.**

The protocol's token-management lifecycle promises that administrators can delist a supported token (e.g., when a token is deprecated, compromised, or its oracle is removed). With this DOS in place, that lifecycle is permanently broken for any token targeted by an attacker. The admin cannot delist the token, cannot update its oracle to a zero address, and cannot prevent future deposits of that token through the normal governance path. The only remaining mitigation is a full contract pause, which is a blunt instrument that also halts all other deposits.

---

### Likelihood Explanation

**High.** The attack requires no capital, no privileged access, and no timing precision. The attacker simply calls `IERC20(token).transfer(poolAddress, 1)` once. Any holder of even 1 wei of any supported token (wstETH, etc.) can execute this permanently and irreversibly. The cost is negligible (1 wei + gas).

---

### Recommendation

Replace the strict `!= 0` balance check with a check against the protocol-tracked balance (fees + bridgeable inventory), or add an admin-callable `sweepToken` function that can drain arbitrary ERC-20 dust before removal. For example:

```solidity
// Instead of:
if (IERC20(token).balanceOf(address(this)) != 0) revert TokenBalanceNotZero();

// Use the protocol-tracked balance only:
if (feeEarnedInToken[token] != 0) revert TokenBalanceNotZero();
// and separately ensure bridgeable balance is zero via getTokenBalanceMinusFees
```

Alternatively, add a `recoverToken(address token, address to, uint256 amount)` admin function so that dust donations can be swept before calling `removeSupportedToken`.

---

### Proof of Concept

1. Admin calls `addSupportedToken(wstETH, oracle, bridge)` on `RSETHPool`.
2. Attacker holds 1 wei of wstETH (trivially obtainable).
3. Attacker calls `IERC20(wstETH).transfer(RSETHPool, 1)`.
4. Admin bridges all legitimate wstETH inventory to L1 (`getTokenBalanceMinusFees` returns 0).
5. Admin calls `removeSupportedToken(wstETH, index)`.
6. `IERC20(wstETH).balanceOf(RSETHPool)` returns 1 (the donated wei).
7. `if (IERC20(token).balanceOf(address(this)) != 0) revert TokenBalanceNotZero()` — **reverts**.
8. The token can never be removed. The attacker can repeat step 3 indefinitely to maintain the DOS. [1](#0-0) [2](#0-1) [3](#0-2)

### Citations

**File:** contracts/pools/RSETHPool.sol (L660-669)
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
```

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

**File:** contracts/pools/RSETHPoolNoWrapper.sol (L96-99)
```text
    error AlreadySupportedToken();
    error TokenNotFoundError();
    error TokenBalanceNotZero();
    error EthDepositDisabled();
```
