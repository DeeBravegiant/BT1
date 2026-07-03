### Title
Unchecked Subtraction in `getTokenBalanceMinusFees` Causes Arithmetic Underflow When a Rebasing Token (stETH) Rebases Negatively, Temporarily Freezing All Token Bridging Operations - (File: contracts/pools/RSETHPool.sol)

---

### Summary

Multiple L2 pool contracts compute the bridgeable token balance by subtracting accumulated protocol fees from the live token balance. This subtraction unconditionally assumes `balance >= feeEarned`. If stETH is a supported token and Lido is slashed (triggering a negative rebase), the stETH balance held by the pool can fall below the accumulated `feeEarnedInToken[stETH]`, causing an arithmetic underflow that reverts every bridging call for that token until the balance recovers.

---

### Finding Description

In `RSETHPool.sol`, `RSETHPoolV3.sol`, `RSETHPoolNoWrapper.sol`, `RSETHPoolV3ExternalBridge.sol`, and `RSETHPoolV3WithNativeChainBridge.sol`, the helper `getTokenBalanceMinusFees` performs an unchecked subtraction:

```solidity
function getTokenBalanceMinusFees(address token) public view returns (uint256) {
    return IERC20(token).balanceOf(address(this)) - feeEarnedInToken[token];
}
```

`feeEarnedInToken[token]` is a monotonically increasing counter: every user deposit adds to it, and it is only zeroed when `withdrawFees` is called. The live `balanceOf` is not monotonically increasing — for a rebasing token like stETH, it can decrease without any withdrawal, purely due to a validator-slashing event on Lido.

When `balanceOf(pool) < feeEarnedInToken[stETH]`, the subtraction underflows under Solidity 0.8 checked arithmetic and reverts. Every downstream caller of this function then also reverts:

- `moveAssetsForBridging(address token, uint256 amount)` — the BRIDGER_ROLE's primary path to move stETH from L2 to L1.
- `bridgeTokens(address token)` — the full-balance bridge path.
- `swapAssetToPremintedRsETH` — the OPERATOR_ROLE reverse-swap path.

The pool's stETH balance is frozen in place; no bridging to L1 is possible until the balance naturally recovers above the fee counter (i.e., until Lido's slashing penalty is absorbed and the rebase turns positive again).

The same unchecked pattern is replicated identically across all five pool variants.

---

### Impact Explanation

All stETH tokens held in the affected L2 pool become temporarily un-bridgeable. The BRIDGER_ROLE cannot move them to L1 for restaking, and the OPERATOR_ROLE cannot perform reverse swaps. The tokens are not lost, but they are frozen in the pool for the duration of the negative-rebase period. This matches the **Medium — Temporary freezing of funds** impact tier.

---

### Likelihood Explanation

- stETH is a core supported asset in the Kelp DAO protocol on L1 (`ST_ETH_TOKEN` is explicitly seeded in `LRTWithdrawalManager.initialize2`). The `addSupportedToken` function in every pool variant accepts any token with a valid oracle, making stETH a natural candidate for L2 pool support.
- Lido validator slashing is a known, non-negligible risk that has occurred historically and is the exact scenario the Lido documentation warns about.
- The fee counter `feeEarnedInToken[stETH]` grows with every deposit and is only reset by `withdrawFees`. If fees are not swept frequently, even a modest slash (a few basis points) is sufficient to push `balanceOf` below the counter.
- No attacker action is required; the trigger is a protocol-level external event (slashing), not a user-controlled input.

---

### Recommendation

Mirror the fix applied in STETHVault PR#100: guard the subtraction so that a negative rebase returns 0 rather than reverting.

```solidity
function getTokenBalanceMinusFees(address token) public view returns (uint256) {
    uint256 balance = IERC20(token).balanceOf(address(this));
    uint256 fees    = feeEarnedInToken[token];
    return balance > fees ? balance - fees : 0;
}
```

Apply the same change to all five pool contracts. Additionally, consider sweeping fees more frequently to keep `feeEarnedInToken` close to zero, reducing the window during which a slash can push the balance below the counter.

---

### Proof of Concept

1. Admin calls `addSupportedToken(stETH, stETHOracle, stETHBridge)` on `RSETHPool` (Arbitrum).
2. Users deposit stETH over time; `feeEarnedInToken[stETH]` accumulates to, say, 0.5 stETH.
3. A Lido slashing event reduces every holder's stETH balance by 0.1 %. The pool held 100 stETH; it now holds 99.9 stETH. `feeEarnedInToken[stETH]` is still 0.5 stETH — no change.
4. BRIDGER_ROLE calls `moveAssetsForBridging(stETH, 99 ether)`.
5. Internally, `getTokenBalanceMinusFees(stETH)` executes `99.9e18 - 0.5e18` — this succeeds here, but if the slash were larger (e.g., 1 %), the pool holds 99 stETH while fees are 0.5 stETH — still fine. However, if fees have grown to, say, 1.5 stETH and the slash brings the balance to 1.4 stETH, the subtraction `1.4e18 - 1.5e18` underflows and reverts.
6. Every subsequent call to `moveAssetsForBridging`, `bridgeTokens`, or `swapAssetToPremintedRsETH` for stETH reverts with an arithmetic underflow until the stETH balance naturally recovers above 1.5 stETH.
7. stETH is frozen in the L2 pool for the duration of the negative-rebase period. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5)

### Citations

**File:** contracts/pools/RSETHPool.sol (L396-398)
```text
    function getTokenBalanceMinusFees(address token) public view returns (uint256) {
        return IERC20(token).balanceOf(address(this)) - feeEarnedInToken[token];
    }
```

**File:** contracts/pools/RSETHPool.sol (L459-478)
```text
    /// @dev Withdraws assets from the contract for bridging
    function moveAssetsForBridging(
        address token,
        uint256 amount
    )
        external
        nonReentrant
        onlySupportedToken(token)
        onlyRole(BRIDGER_ROLE)
    {
        if (amount == 0) revert InvalidAmount();

        // withdraw up to token - fees
        uint256 tokenBalanceMinusFees = getTokenBalanceMinusFees(token);
        if (amount > tokenBalanceMinusFees) revert InsufficientBalanceInPool();

        IERC20(token).safeTransfer(msg.sender, amount);

        emit AssetsMovedForBridging(amount, token);
    }
```

**File:** contracts/pools/RSETHPoolV3.sol (L371-373)
```text
    function getTokenBalanceMinusFees(address token) public view returns (uint256) {
        return IERC20(token).balanceOf(address(this)) - feeEarnedInToken[token];
    }
```

**File:** contracts/pools/RSETHPoolNoWrapper.sol (L361-363)
```text
    function getTokenBalanceMinusFees(address token) public view returns (uint256) {
        return IERC20(token).balanceOf(address(this)) - feeEarnedInToken[token];
    }
```

**File:** contracts/pools/RSETHPoolV3ExternalBridge.sol (L502-504)
```text
    function getTokenBalanceMinusFees(address token) public view returns (uint256) {
        return IERC20(token).balanceOf(address(this)) - feeEarnedInToken[token];
    }
```

**File:** contracts/pools/RSETHPoolV3WithNativeChainBridge.sol (L384-386)
```text
    function getTokenBalanceMinusFees(address token) public view returns (uint256) {
        return IERC20(token).balanceOf(address(this)) - feeEarnedInToken[token];
    }
```
