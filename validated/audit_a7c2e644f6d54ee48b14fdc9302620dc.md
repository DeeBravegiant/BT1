### Title
Griefing `removeSupportedToken` via Direct Token Transfer Prevents Emergency Token Delisting - (File: contracts/pools/RSETHPoolNoWrapper.sol)

### Summary
`RSETHPoolNoWrapper.removeSupportedToken` enforces an exact-zero balance check before allowing a token to be delisted. Any unprivileged actor holding even 1 wei of the token can permanently block the `TIMELOCK_ROLE` from removing it by repeatedly sending dust to the contract, preventing emergency delistings of compromised or depegged tokens.

### Finding Description
`removeSupportedToken` contains the following guard: [1](#0-0) 

```solidity
function removeSupportedToken(address token, uint256 tokenIndex) external onlyRole(TIMELOCK_ROLE) {
    UtilLib.checkNonZeroAddress(token);
    if (supportedTokenList[tokenIndex] != token) revert TokenNotFoundError();
    if (IERC20(token).balanceOf(address(this)) != 0) revert TokenBalanceNotZero();
    ...
}
```

The check `IERC20(token).balanceOf(address(this)) != 0` requires the contract's balance to be **exactly zero**. Because ERC-20 tokens can be transferred to any address without the recipient's consent, any external actor can send 1 wei of the token to `RSETHPoolNoWrapper` at any time, causing the check to revert.

The only recovery path is `moveAssetsForBridging(token)`: [2](#0-1) 

which transfers `balanceOf - feeEarnedInToken` to `msg.sender`. However, the attacker can immediately re-send tokens after each sweep, sustaining the grief indefinitely at negligible cost (a single ERC-20 transfer per block).

### Impact Explanation
The `RSETHPoolNoWrapper` contract is the L2 deposit pool for chains such as Arbitrum and Unichain. Supported tokens are priced via oracles and swapped for rsETH. If a supported token becomes compromised (e.g., depegs, is exploited, or its oracle becomes stale), the protocol's emergency response is to call `removeSupportedToken` to halt further deposits of that token. An attacker can block this response indefinitely, keeping the compromised token in the supported list and allowing continued deposits at a potentially incorrect exchange rate. This maps to **temporary freezing of the admin's ability to protect the protocol**, with secondary risk of fund loss if the oracle does not immediately reflect the token's true value.

**Impact: Medium — Temporary freezing of a critical admin safety function.**

### Likelihood Explanation
The attack requires only:
1. Holding any nonzero amount of the target token (trivially achievable for any listed LST).
2. Sending 1 wei to `RSETHPoolNoWrapper` before or immediately after each admin sweep.

No special permissions, flash loans, or complex setup are needed. The cost to the attacker is negligible (gas + dust token amount). The attack is most damaging precisely when the protocol most needs to act quickly (token depeg or exploit), making it a realistic threat.

### Recommendation
Replace the exact-zero check with a `>=` comparison against a configurable negligible threshold (analogous to `maxNegligibleAmount` already used in `LRTDepositPool._checkResidueLSTBalance`): [3](#0-2) 

```solidity
// Instead of:
if (IERC20(token).balanceOf(address(this)) != 0) revert TokenBalanceNotZero();

// Use:
if (IERC20(token).balanceOf(address(this)) > maxNegligibleAmount) revert TokenBalanceNotZero();
```

Alternatively, allow `removeSupportedToken` to sweep any residual balance to a treasury address before delisting, removing the dependency on the balance being exactly zero.

### Proof of Concept
1. Admin calls `addSupportedToken(tokenA, oracle, bridge)` — `tokenA` is now a supported token.
2. `tokenA` depegs; admin prepares to call `removeSupportedToken(tokenA, 0)`.
3. Attacker calls `IERC20(tokenA).transfer(address(rsETHPoolNoWrapper), 1)` — costs only gas + 1 wei.
4. `removeSupportedToken` reverts with `TokenBalanceNotZero` because `balanceOf(address(this)) == 1 != 0`.
5. Admin calls `moveAssetsForBridging(tokenA)` to sweep the balance.
6. Attacker immediately repeats step 3.
7. `removeSupportedToken` continues to revert; `tokenA` remains listed and users can continue depositing the depegged token in exchange for rsETH.

### Citations

**File:** contracts/pools/RSETHPoolNoWrapper.sol (L416-428)
```text
    function moveAssetsForBridging(address token)
        external
        nonReentrant
        onlySupportedToken(token)
        onlyRole(BRIDGER_ROLE)
    {
        // withdraw token - fees
        uint256 tokenBalanceMinusFees = IERC20(token).balanceOf(address(this)) - feeEarnedInToken[token];

        IERC20(token).safeTransfer(msg.sender, tokenBalanceMinusFees);

        emit AssetsMovedForBridging(tokenBalanceMinusFees, token);
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

**File:** contracts/LRTDepositPool.sol (L638-643)
```text
            assetBalance = IERC20(supportedAssets[i]).balanceOf(nodeDelegatorAddress)
                + INodeDelegator(nodeDelegatorAddress).getAssetBalance(supportedAssets[i]);
            assetBalance += INodeDelegator(nodeDelegatorAddress).getAssetUnstaking(supportedAssets[i]);

            if (assetBalance > maxNegligibleAmount) {
                revert NodeDelegatorHasAssetBalance(supportedAssets[i], assetBalance);
```
