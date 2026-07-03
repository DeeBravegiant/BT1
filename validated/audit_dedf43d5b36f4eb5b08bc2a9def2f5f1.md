### Title
Missing Received-Token Balance Check in `depositAsset()` Allows rsETH Over-Minting with Fee-on-Transfer Assets - (File: contracts/LRTDepositPool.sol)

### Summary
`LRTDepositPool.depositAsset()` mints rsETH based on the caller-supplied `depositAmount` parameter without verifying how many tokens were actually received after the `safeTransferFrom` call. If a supported LST asset charges a transfer fee (fee-on-transfer token), the protocol receives fewer tokens than `depositAmount` but mints rsETH for the full nominal amount, inflating rsETH supply beyond its real backing.

### Finding Description
In `depositAsset()`, the rsETH mint amount is computed by `_beforeDeposit()` using the caller-supplied `depositAmount` before any token transfer occurs:

```solidity
// contracts/LRTDepositPool.sol L111-115
uint256 rsethAmountToMint = _beforeDeposit(asset, depositAmount, minRSETHAmountExpected);

// interactions
IERC20(asset).safeTransferFrom(msg.sender, address(this), depositAmount);
_mintRsETH(rsethAmountToMint);
```

`_beforeDeposit()` calls `getRsETHAmountToMint(asset, depositAmount)` using the nominal `depositAmount`, not the actual tokens received:

```solidity
// contracts/LRTDepositPool.sol L665
rsethAmountToMint = getRsETHAmountToMint(asset, depositAmount);
```

No before/after balance snapshot is taken around the `safeTransferFrom`. If the asset is a fee-on-transfer token, the contract receives `depositAmount - fee` but mints rsETH as if it received the full `depositAmount`.

The same root cause exists in the pool contracts:
- `contracts/pools/RSETHPool.sol` L296–302: `safeTransferFrom(amount)` then `safeTransfer(wrsETH, rsETHAmount)` computed from `amount`
- `contracts/pools/RSETHPoolNoWrapper.sol` L262–268
- `contracts/pools/RSETHPoolV3.sol` L284–290
- `contracts/pools/RSETHPoolV3ExternalBridge.sol` L403–409
- `contracts/pools/RSETHPoolV3WithNativeChainBridge.sol` L320–326
- `contracts/L2/RsETHTokenWrapper.sol` L137–139: `safeTransferFrom(_amount)` then `_mint(_to, _amount)` 1:1

### Impact Explanation
**Critical — Protocol insolvency / direct theft of rsETH value.**

For `LRTDepositPool.depositAsset()`: each deposit with a fee-on-transfer LST mints more rsETH than the deposited collateral justifies. Over time, rsETH becomes undercollateralized. Existing rsETH holders suffer dilution and the protocol becomes insolvent when redemptions are processed against a smaller-than-expected asset base.

For the pool contracts: the pool transfers out wrsETH/rsETH computed on the nominal `amount`, but only holds `amount - fee` of the input token. The pool's reserves are drained faster than accounted, constituting direct theft of pool liquidity from other users.

### Likelihood Explanation
**Medium.** The protocol's `LRTConfig` restricts supported assets to those explicitly whitelisted by governance. However, the protocol is designed to support multiple LST tokens, and some LSTs (e.g., stETH in certain transfer paths, or future tokens) can exhibit fee-on-transfer or rebasing behavior. The pool contracts (`RSETHPool`, `RSETHPoolV3`, etc.) also accept a configurable `supportedTokenList`, widening the attack surface. Any depositor can trigger this by simply calling `depositAsset()` or `deposit()` with a qualifying token — no special privilege is required.

### Recommendation
Capture the contract's token balance before and after the `safeTransferFrom` call and use the actual received amount for all downstream calculations (rsETH minting, fee accounting, wrsETH transfer):

```solidity
uint256 balanceBefore = IERC20(asset).balanceOf(address(this));
IERC20(asset).safeTransferFrom(msg.sender, address(this), depositAmount);
uint256 actualReceived = IERC20(asset).balanceOf(address(this)) - balanceBefore;

uint256 rsethAmountToMint = _beforeDeposit(asset, actualReceived, minRSETHAmountExpected);
_mintRsETH(rsethAmountToMint);
```

Apply the same pattern to all pool `deposit(token, amount, ...)` functions and `RsETHTokenWrapper._deposit()`.

### Proof of Concept

1. Governance whitelists a fee-on-transfer LST (e.g., 1% fee per transfer) as a supported asset in `LRTConfig`.
2. Attacker calls `LRTDepositPool.depositAsset(feeToken, 1000e18, 0, "")`.
3. `_beforeDeposit` computes `rsethAmountToMint` based on `1000e18`.
4. `safeTransferFrom` executes; contract receives only `990e18` (1% fee deducted).
5. `_mintRsETH(rsethAmountToMint)` mints rsETH equivalent to `1000e18` of the asset.
6. The attacker holds rsETH backed by `1000e18` worth of value but the pool only holds `990e18`.
7. Repeated deposits progressively widen the insolvency gap; existing rsETH holders are diluted and the protocol cannot honor full redemptions.

**Root cause lines:** [1](#0-0) [2](#0-1) 

**Same pattern in pool contracts:** [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

**File:** contracts/LRTDepositPool.sol (L111-115)
```text
        uint256 rsethAmountToMint = _beforeDeposit(asset, depositAmount, minRSETHAmountExpected);

        // interactions
        IERC20(asset).safeTransferFrom(msg.sender, address(this), depositAmount);
        _mintRsETH(rsethAmountToMint);
```

**File:** contracts/LRTDepositPool.sol (L665-665)
```text
        rsethAmountToMint = getRsETHAmountToMint(asset, depositAmount);
```

**File:** contracts/pools/RSETHPool.sol (L296-302)
```text
        IERC20(token).safeTransferFrom(msg.sender, address(this), amount);

        (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount, token);

        feeEarnedInToken[token] += fee;

        IERC20(address(wrsETH)).safeTransfer(msg.sender, rsETHAmount);
```

**File:** contracts/pools/RSETHPoolV3.sol (L284-290)
```text
        IERC20(token).safeTransferFrom(msg.sender, address(this), amount);

        (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount, token);

        feeEarnedInToken[token] += fee;

        wrsETH.mint(msg.sender, rsETHAmount);
```

**File:** contracts/L2/RsETHTokenWrapper.sol (L137-139)
```text
        ERC20Upgradeable(_asset).safeTransferFrom(msg.sender, address(this), _amount);

        _mint(_to, _amount);
```
