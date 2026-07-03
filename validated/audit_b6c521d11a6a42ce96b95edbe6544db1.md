### Title
`feeEarnedInToken` Balances Don't Account for Rebasing Token Slashings, Enabling Disproportionate Fee Extraction at Depositors' Expense - (File: contracts/pools/RSETHPoolV3.sol)

### Summary
The L2 pool contracts accumulate protocol fees as raw token amounts in `feeEarnedInToken[token]`. If a rebasing LST (e.g., stETH) is added as a supported token and a slashing event reduces the contract's actual token balance, the stored fee counter remains at its pre-slash nominal value. When the bridger withdraws fees first, they extract a disproportionately large share of the slashed pool, leaving depositors' bridged assets undercollateralised.

### Finding Description
In `RSETHPoolV3`, `RSETHPool`, `RSETHPoolV3ExternalBridge`, `RSETHPoolV3WithNativeChainBridge`, and `AGETHPoolV3`, every token deposit accrues a fee:

```solidity
// RSETHPoolV3.sol – deposit(address token, ...)
feeEarnedInToken[token] += fee;   // raw nominal amount, never rebased
``` [1](#0-0) 

The fee withdrawal function blindly transfers the full stored counter:

```solidity
uint256 amountToSendInToken = feeEarnedInToken[token];
feeEarnedInToken[token] = 0;
IERC20(token).safeTransfer(receiver, amountToSendInToken);
``` [2](#0-1) 

The user-facing bridging function computes the bridgeable balance as:

```solidity
return IERC20(token).balanceOf

### Citations

**File:** contracts/pools/RSETHPoolV3.sol (L286-289)
```text
        (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount, token);

        feeEarnedInToken[token] += fee;

```

**File:** contracts/pools/RSETHPoolV3.sol (L473-479)
```text
        // withdraw fees in token
        uint256 amountToSendInToken = feeEarnedInToken[token];
        feeEarnedInToken[token] = 0;
        IERC20(token).safeTransfer(receiver, amountToSendInToken);

        emit FeesWithdrawn(amountToSendInToken, token);
    }
```
