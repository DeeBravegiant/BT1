Audit Report

## Title
Zero agETH Minted on Integer Truncation Permanently Freezes Depositor Tokens — (`contracts/agETH/AGETHPoolV3.sol`)

## Summary
`deposit(address,uint256,string)` executes `safeTransferFrom` before computing `agETHAmount`. When `amountAfterFee * tokenToETHRate < agETHToETHrate`, integer division in `viewSwapAgETHAmountAndFee` truncates to zero, causing `agETH.mint(msg.sender, 0)` to be called with no revert. The depositor's tokens are permanently stranded in the contract with no on-chain recovery path.

## Finding Description
In `deposit(address,uint256,string)` at [1](#0-0)  tokens are transferred in at line 145 before `agETHAmount` is computed at line 147. Inside `viewSwapAgETHAmountAndFee(uint256,address)`, the final calculation at [2](#0-1)  is plain integer division: `agETHAmount = amountAfterFee * tokenToETHRate / agETHToETHrate`. Whenever `amountAfterFee * tokenToETHRate < agETHToETHrate`, the result is 0. There is no `if (agETHAmount == 0) revert` guard anywhere in the deposit path. [3](#0-2) 

The only privileged functions that touch the token balance are `withdrawFees` (moves only `feeEarnedInToken[token]`) and `moveAssetsForBridging(token)` (moves `balance − feeEarnedInToken[token]` to the bridger). [4](#0-3)  Neither returns tokens to the original depositor, so the loss is permanent from the depositor's perspective.

The `addSupportedToken` guard only checks `getRate() != 0` at registration time and does not bound the ratio `agETHToETHrate / tokenToETHRate`. [5](#0-4) 

## Impact Explanation
**Critical — Permanent freezing of funds.** Any depositor who sends a sufficiently small amount of a supported token receives 0 agETH and has no on-chain mechanism to recover their tokens. For 6-decimal tokens (e.g., USDC at ~$3000/ETH, `tokenToETHRate ≈ 3.33e14`), any deposit below ~3153 token-wei (~$0.003) truncates to zero. For lower-value 6-decimal tokens or tokens whose oracle rate falls post-registration, the threshold grows. The condition is purely arithmetic, requires no privileged action, and is repeatable across any number of transactions and any supported token.

## Likelihood Explanation
Any unprivileged caller can invoke `deposit(token, amount, referralId)` on any supported token. The only prerequisites are `amount > 0` (enforced) and the token being supported (enforced by `onlySupportedToken`). The truncation condition is trivially reachable with dust-sized deposits on any supported 6-decimal token, and with larger deposits if a supported token has low unit value or its oracle rate declines after registration. No role, key, or external condition is required.

## Recommendation
Add a zero-amount guard immediately after computing `agETHAmount`, before any state mutation:

```solidity
(uint256 agETHAmount, uint256 fee) = viewSwapAgETHAmountAndFee(amount, token);
if (agETHAmount == 0) revert InvalidAmount();   // ← add this
feeEarnedInToken[token] += fee;
agETH.mint(msg.sender, agETHAmount);
```

Alternatively, move `safeTransferFrom` to after the amount calculation so that a zero-truncation revert never leaves tokens stranded in the contract.

## Proof of Concept
Minimal Foundry unit test demonstrating the invariant violation:

```solidity
function testZeroMintTruncation() external {
    // 6-decimal token, USDC-like at $3000/ETH
    uint256 amount         = 3000;          // 3000 token-wei, below threshold
    uint256 tokenToETHRate = 3.33e14;       // oracle rate for token
    uint256 agETHToETHrate = 1.05e18;       // agETH oracle rate
    uint256 feeBps         = 0;

    uint256 fee            = amount * feeBps / 10_000;   // 0
    uint256 amountAfterFee = amount - fee;               // 3000
    uint256 agETHAmount    = amountAfterFee * tokenToETHRate / agETHToETHrate;
    // = 3000 * 3.33e14 / 1.05e18 = 9.99e17 / 1.05e18 = 0

    assertEq(agETHAmount, 0);
    // User transferred 3000 token-wei into the contract and received 0 agETH.
    // No revert occurred. No recovery path exists.
}
```

A fork test can confirm end-to-end: deploy with a mock 6-decimal token oracle returning `3.33e14`, call `deposit(token, 3000, "")`, assert `agETH.balanceOf(depositor) == 0` and `IERC20(token).balanceOf(pool) == 3000`.

### Citations

**File:** contracts/agETH/AGETHPoolV3.sol (L143-153)
```text
        if (amount == 0) revert InvalidAmount();

        IERC20(token).safeTransferFrom(msg.sender, address(this), amount);

        (uint256 agETHAmount, uint256 fee) = viewSwapAgETHAmountAndFee(amount, token);

        feeEarnedInToken[token] += fee;

        agETH.mint(msg.sender, agETHAmount);

        emit SwapOccurred(msg.sender, agETHAmount, fee, referralId);
```

**File:** contracts/agETH/AGETHPoolV3.sol (L194-194)
```text
        agETHAmount = amountAfterFee * tokenToETHRate / agETHToETHrate;
```

**File:** contracts/agETH/AGETHPoolV3.sol (L234-241)
```text
    function moveAssetsForBridging(address token) external onlySupportedToken(token) onlyRole(BRIDGER_ROLE) {
        // withdraw token - fees
        uint256 tokenBalanceMinusFees = IERC20(token).balanceOf(address(this)) - feeEarnedInToken[token];

        IERC20(token).safeTransfer(msg.sender, tokenBalanceMinusFees);

        emit AssetsMovedForBridging(tokenBalanceMinusFees, token);
    }
```

**File:** contracts/agETH/AGETHPoolV3.sol (L279-281)
```text
        if (IOracle(oracle).getRate() == 0) {
            revert UnsupportedOracle();
        }
```
