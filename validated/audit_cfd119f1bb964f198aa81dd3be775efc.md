### Title
Dust Donation Blocks Token Removal from L2 Pool - (File: contracts/pools/RSETHPoolV3.sol)

### Summary
`RSETHPoolV3.removeSupportedToken()` requires the pool's ERC-20 balance of the token to be exactly zero before removal. Any unprivileged user can send a dust amount of the token directly to the pool contract, permanently blocking the `TIMELOCK_ROLE` from removing that token from the supported list.

### Finding Description
`RSETHPoolV3.removeSupportedToken()` enforces a strict zero-balance precondition before deleting a token from the supported list:

```solidity
// contracts/pools/RSETHPoolV3.sol:559-568
function removeSupportedToken(address token, uint256 tokenIndex) external onlyRole(TIMELOCK_ROLE) {
    UtilLib.checkNonZeroAddress(token);
    if (supportedTokenList[tokenIndex] != token) revert TokenNotFoundError();
    if (IERC20(token).balanceOf(address(this)) != 0) revert TokenBalanceNotZero();  // ← strict zero check

    delete supportedTokenOracle[token];
    supportedTokenList[tokenIndex] = supportedTokenList[supportedTokenList.length - 1];
    supportedTokenList.pop();
    emit RemovedSupportedToken(token);
}
``` [1](#0-0) 

The check `IERC20(token).balanceOf(address(this)) != 0` has no negligible-amount threshold. Because `RSETHPoolV3` has an open `receive()` function and ERC-20 tokens can be transferred to any address without the recipient's consent, any external caller can send 1 wei of the token to the pool, making the balance permanently non-zero.

This is the exact same vulnerability class fixed in `LRTDepositPool._checkResidueEthBalance()` via `maxNegligibleAmount`:

```solidity
// contracts/LRTDepositPool.sol:619
|| address(nodeDelegatorAddress).balance > maxNegligibleAmount
``` [2](#0-1) 

`RSETHPoolV3.removeSupportedToken()` has no equivalent threshold — it uses a raw `!= 0` check with no mitigation. [3](#0-2) 

### Impact Explanation
The `TIMELOCK_ROLE` is permanently unable to remove a supported token from the L2 pool as long as the attacker keeps a non-zero balance in the contract. If a supported token must be urgently delisted (e.g., due to a token-level exploit, depeg, or oracle failure), the attacker can front-run every removal attempt with a 1-wei transfer, preventing the protocol from protecting users who continue to deposit the compromised token. This constitutes a **medium-severity temporary freeze** of a critical administrative operation, with potential downstream impact on user funds if the blocked token becomes unsafe.

### Likelihood Explanation
The attack requires only a single ERC-20 `transfer` call costing negligible gas and 1 wei of the target token. No special permissions, flash loans, or complex setup are needed. Any holder of the token — including the token contract itself if it has a faucet — can execute this. The attacker can repeat the transfer after every admin sweep attempt, making the block effectively indefinite.

### Recommendation
Apply the same `maxNegligibleAmount` pattern already used in `LRTDepositPool._checkResidueLSTBalance()`:

```solidity
// contracts/LRTDepositPool.sol:642
if (assetBalance > maxNegligibleAmount) {
    revert NodeDelegatorHasAssetBalance(supportedAssets[i], assetBalance);
}
``` [4](#0-3) 

In `RSETHPoolV3`, replace the strict zero check with a configurable negligible threshold:

```solidity
uint256 public maxNegligibleTokenAmount; // set by admin

function removeSupportedToken(address token, uint256 tokenIndex) external onlyRole(TIMELOCK_ROLE) {
    UtilLib.checkNonZeroAddress(token);
    if (supportedTokenList[tokenIndex] != token) revert TokenNotFoundError();
    if (IERC20(token).balanceOf(address(this)) > maxNegligibleTokenAmount) revert TokenBalanceNotZero();
    ...
}
```

Alternatively, allow the admin to sweep dust before removal, or use a rescue function to drain the residual balance prior to calling `removeSupportedToken`.

### Proof of Concept

1. `RSETHPoolV3` is deployed on an L2 with `tokenA` as a supported token.
2. The protocol decides to delist `tokenA` and the `TIMELOCK_ROLE` calls `removeSupportedToken(tokenA, 0)`.
3. Before (or after) the call, attacker executes: `IERC20(tokenA).transfer(address(RSETHPoolV3), 1)`.
4. `removeSupportedToken` evaluates `IERC20(tokenA).balanceOf(address(this)) != 0` → `1 != 0` → `true` → reverts with `TokenBalanceNotZero`.
5. Attacker repeats step 3 after every admin attempt. The token can never be removed.

The attacker-controlled entry path is a plain ERC-20 `transfer` to the pool address — no protocol interaction required. [1](#0-0)

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

**File:** contracts/LRTDepositPool.sol (L616-624)
```text
    function _checkResidueEthBalance(address nodeDelegatorAddress) internal view {
        if (
            INodeDelegator(nodeDelegatorAddress).getEffectivePodShares() != 0
                || address(nodeDelegatorAddress).balance > maxNegligibleAmount
                || INodeDelegator(nodeDelegatorAddress).getAssetUnstaking(LRTConstants.ETH_TOKEN) > 0
        ) {
            revert NodeDelegatorHasETH();
        }
    }
```

**File:** contracts/LRTDepositPool.sol (L638-644)
```text
            assetBalance = IERC20(supportedAssets[i]).balanceOf(nodeDelegatorAddress)
                + INodeDelegator(nodeDelegatorAddress).getAssetBalance(supportedAssets[i]);
            assetBalance += INodeDelegator(nodeDelegatorAddress).getAssetUnstaking(supportedAssets[i]);

            if (assetBalance > maxNegligibleAmount) {
                revert NodeDelegatorHasAssetBalance(supportedAssets[i], assetBalance);
            }
```
