### Title
Fee-on-Transfer Token Deposit Causes wrsETH Over-Minting and Protocol Insolvency - (File: contracts/pools/RSETHPoolV3.sol, contracts/pools/RSETHPoolV3ExternalBridge.sol, contracts/pools/RSETHPoolV3WithNativeChainBridge.sol, contracts/pools/RSETHPoolNoWrapper.sol)

---

### Summary

All four L2 pool deposit functions compute the `wrsETH` mint amount and fee accounting from the caller-supplied `amount` parameter rather than from the actual tokens received by the contract. For any fee-on-transfer token that is whitelisted as a supported asset, every depositor will receive more `wrsETH` than the collateral actually deposited, making the pool permanently undercollateralized and causing protocol insolvency.

---

### Finding Description

In `RSETHPoolV3.deposit(address token, uint256 amount, string referralId)`:

```solidity
IERC20(token).safeTransferFrom(msg.sender, address(this), amount);   // actual received < amount

(uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount, token); // uses `amount`, not actual received

feeEarnedInToken[token] += fee;   // inflated by transfer-fee delta

wrsETH.mint(msg.sender, rsETHAmount);  // over-minted
``` [1](#0-0) 

The identical pattern is present in `RSETHPoolV3ExternalBridge.deposit`: [2](#0-1) 

In `RSETHPoolV3WithNativeChainBridge.deposit`: [3](#0-2) 

And in `RSETHPoolNoWrapper.deposit`: [4](#0-3) 

Because `viewSwapRsETHAmountAndFee` is a pure calculation on the input `amount`: [5](#0-4) 

…the contract never observes the actual balance delta. For a token with a 1 % transfer fee, a deposit of `1000e18` causes the pool to receive `990e18` tokens but mint `wrsETH` equivalent to `1000e18` tokens.

A secondary consequence is that `feeEarnedInToken[token]` is inflated by the same delta. `getTokenBalanceMinusFees` subtracts this inflated value from the real balance: [6](#0-5) 

If the cumulative inflation of `feeEarnedInToken[token]` exceeds the actual token balance, this subtraction underflows (Solidity 0.8 checked arithmetic), causing `moveAssetsForBridging` and `bridgeTokens` to revert permanently for that token.

Notably, the same codebase already applies the correct balance-before/after pattern in `KernelDepositPool.notifyRewardAmount`, demonstrating developer awareness of the issue for reward tokens but not for pool deposits: [7](#0-6) 

---

### Impact Explanation

Every deposit of a fee-on-transfer token mints more `wrsETH` than the collateral held. The pool bridges fewer tokens to L1 than the outstanding `wrsETH` supply implies. `wrsETH` holders cannot all redeem at par, constituting **protocol insolvency**. Additionally, if `feeEarnedInToken[token]` grows beyond the real balance, all bridging calls for that token revert, permanently freezing the non-fee portion of deposited assets in the pool.

---

### Likelihood Explanation

The `addSupportedToken` function (gated to `TIMELOCK_ROLE`) allows any ERC-20 token with a valid oracle to be whitelisted. Fee-on-transfer tokens (e.g., tokens with a built-in burn or redistribution mechanism) are a well-known token class. Once such a token is added, every ordinary depositor—without any special privilege or front-running—triggers the accounting error on each call to `deposit`. No attacker action is required beyond making a normal deposit. [8](#0-7) 

---

### Recommendation

Replace the input-`amount`-based accounting with a balance-before/after pattern in all four pool `deposit` functions, mirroring the pattern already used in `KernelDepositPool.notifyRewardAmount`:

```solidity
uint256 balanceBefore = IERC20(token).balanceOf(address(this));
IERC20(token).safeTransferFrom(msg.sender, address(this), amount);
uint256 actualReceived = IERC20(token).balanceOf(address(this)) - balanceBefore;

(uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(actualReceived, token);
feeEarnedInToken[token] += fee;
wrsETH.mint(msg.sender, rsETHAmount);
```

Apply the same fix to `RSETHPoolV3ExternalBridge`, `RSETHPoolV3WithNativeChainBridge`, and `RSETHPoolNoWrapper`.

---

### Proof of Concept

1. Admin calls `addSupportedToken(feeToken, oracle, bridge)` where `feeToken` deducts a 1 % transfer fee.
2. Alice calls `RSETHPoolV3.deposit(feeToken, 1000e18, "ref")`.
3. The contract receives `990e18` tokens (1 % fee burned/redirected).
4. `viewSwapRsETHAmountAndFee(1000e18, feeToken)` is called with the original `1000e18`.
5. Alice is minted `wrsETH` equivalent to `1000e18` tokens worth of rsETH.
6. The pool holds only `990e18` tokens but has issued `wrsETH` backed by `1000e18`.
7. Repeated across all depositors, the pool's token balance is perpetually `N × 1 %` short of the outstanding `wrsETH` supply, making full redemption impossible — protocol insolvency.
8. Simultaneously, `feeEarnedInToken[feeToken]` accumulates inflated fee values; once it exceeds the real balance, `getTokenBalanceMinusFees` underflows and all calls to `moveAssetsForBridging` / `bridgeTokens` for `feeToken` revert, permanently freezing those assets.

### Citations

**File:** contracts/pools/RSETHPoolV3.sol (L284-292)
```text
        IERC20(token).safeTransferFrom(msg.sender, address(this), amount);

        (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount, token);

        feeEarnedInToken[token] += fee;

        wrsETH.mint(msg.sender, rsETHAmount);

        emit SwapOccurred(msg.sender, rsETHAmount, fee, referralId, token);
```

**File:** contracts/pools/RSETHPoolV3.sol (L315-335)
```text
    function viewSwapRsETHAmountAndFee(
        uint256 amount,
        address token
    )
        public
        view
        onlySupportedToken(token)
        returns (uint256 rsETHAmount, uint256 fee)
    {
        fee = amount * feeBps / 10_000;
        uint256 amountAfterFee = amount - fee;

        // rate of rsETH in ETH
        uint256 rsETHToETHrate = getRate();

        // rate of token in ETH
        uint256 tokenToETHRate = IOracle(supportedTokenOracle[token]).getRate();

        // Calculate the final rsETH amount
        rsETHAmount = amountAfterFee * tokenToETHRate / rsETHToETHrate;
    }
```

**File:** contracts/pools/RSETHPoolV3.sol (L371-373)
```text
    function getTokenBalanceMinusFees(address token) public view returns (uint256) {
        return IERC20(token).balanceOf(address(this)) - feeEarnedInToken[token];
    }
```

**File:** contracts/pools/RSETHPoolV3.sol (L541-555)
```text
    function addSupportedToken(address token, address oracle) external onlyRole(TIMELOCK_ROLE) {
        UtilLib.checkNonZeroAddress(token);
        UtilLib.checkNonZeroAddress(oracle);

        if (supportedTokenOracle[token] != address(0)) {
            revert AlreadySupportedToken();
        }
        if (IOracle(oracle).getRate() == 0) {
            revert UnsupportedOracle();
        }
        supportedTokenList.push(token);
        supportedTokenOracle[token] = oracle;

        emit AddSupportedToken(token);
    }
```

**File:** contracts/pools/RSETHPoolV3ExternalBridge.sol (L403-411)
```text
        IERC20(token).safeTransferFrom(msg.sender, address(this), amount);

        (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount, token);

        feeEarnedInToken[token] += fee;

        wrsETH.mint(msg.sender, rsETHAmount);

        emit SwapOccurred(msg.sender, rsETHAmount, fee, referralId, token);
```

**File:** contracts/pools/RSETHPoolV3WithNativeChainBridge.sol (L320-328)
```text
        IERC20(token).safeTransferFrom(msg.sender, address(this), amount);

        (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount, token);

        feeEarnedInToken[token] += fee;

        wrsETH.mint(msg.sender, rsETHAmount);

        emit SwapOccurred(msg.sender, rsETHAmount, fee, referralId, token);
```

**File:** contracts/pools/RSETHPoolNoWrapper.sol (L262-270)
```text
        IERC20(token).safeTransferFrom(msg.sender, address(this), amount);

        (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount, token);

        feeEarnedInToken[token] += fee;

        rsETH.safeTransfer(msg.sender, rsETHAmount);

        emit SwapOccurred(msg.sender, rsETHAmount, fee, referralId); // Add token address?
```

**File:** contracts/KERNEL/KernelDepositPool.sol (L572-577)
```text
        // Transfer reward tokens into the contract
        uint256 balanceBefore = rewardsToken.balanceOf(address(this));
        rewardsToken.safeTransferFrom(msg.sender, address(this), _amount);
        uint256 balanceAfter = rewardsToken.balanceOf(address(this));
        // Calculate the actual amount of tokens received in case of a transfer fee (tax)
        uint256 receivedAmount = balanceAfter - balanceBefore;
```
