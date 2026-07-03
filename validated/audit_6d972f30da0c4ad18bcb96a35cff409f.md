### Title
Zero rsETH Minted on Deposit Due to Rounding Truncation — (`contracts/LRTDepositPool.sol`)

### Summary

`LRTDepositPool.getRsETHAmountToMint` computes the rsETH amount to mint via integer division. When the numerator is smaller than the denominator, Solidity truncates the result to zero. If `_beforeDeposit` does not explicitly revert on a zero result, a depositor's assets are transferred to the protocol while zero rsETH is minted, permanently locking the deposited funds with no recovery path.

### Finding Description

`getRsETHAmountToMint` at line 520 of `LRTDepositPool.sol` computes:

```solidity
rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
``` [1](#0-0) 

This is a plain integer division. If `amount * assetPrice < rsETHPrice`, the result truncates to zero.

`depositAsset` calls `_beforeDeposit` to obtain `rsethAmountToMint`, then unconditionally transfers the user's tokens and calls `_mintRsETH(rsethAmountToMint)`:

```solidity
uint256 rsethAmountToMint = _beforeDeposit(asset, depositAmount, minRSETHAmountExpected);
IERC20(asset).safeTransferFrom(msg.sender, address(this), depositAmount);
_mintRsETH(rsethAmountToMint);
``` [2](#0-1) 

The only guard against receiving zero rsETH is the caller-supplied `minRSETHAmountExpected` slippage parameter. When a caller passes `minRSETHAmountExpected = 0` (a common default in integrations and scripts), the check `rsethAmountToMint >= minRSETHAmountExpected` evaluates to `0 >= 0` and passes. The asset transfer executes, zero rsETH is minted, and the depositor has no token with which to initiate a withdrawal.

The `rsETHPrice` starts at `1e18` and grows monotonically as yield accrues: [3](#0-2) 

The oracle sanity check permits asset prices as low as `1e16`: [4](#0-3) 

For an asset with `assetPrice = 1e16` and `rsETHPrice = 1e18`, any deposit of fewer than 100 units rounds to zero rsETH. For ETH-pegged assets (`assetPrice ≈ 1e18`), the rounding to zero occurs for deposits of 0 wei, but as `rsETHPrice` grows (e.g., to `1.001e18`), deposits of 1 wei also round to zero.

### Impact Explanation

A depositor who calls `depositAsset` or `depositETH` with a small amount and `minRSETHAmountExpected = 0` will have their assets permanently transferred to the protocol while receiving zero rsETH. Since rsETH is the sole redemption token for all withdrawal paths (`initiateWithdrawal`, `instantWithdrawal`), the deposited assets are irrecoverable. This constitutes a direct, permanent loss of user funds. [5](#0-4) 

### Likelihood Explanation

The condition is reachable by any unprivileged depositor. It requires:
1. A deposit amount small enough that `amount * assetPrice < rsETHPrice` (trivially true for 1-wei deposits of low-value assets, or any deposit when `rsETHPrice` has grown significantly above `assetPrice`).
2. The caller passes `minRSETHAmountExpected = 0`, which is the default in many integration patterns, scripts, and front-ends that omit slippage protection.

Both conditions are realistic in production. The protocol does not enforce a non-zero rsETH output independently of the caller-supplied slippage parameter.

### Recommendation

Add an explicit zero-check on `rsethAmountToMint` inside `_beforeDeposit` (or at the top of `depositAsset`/`depositETH`) that reverts unconditionally, regardless of `minRSETHAmountExpected`:

```solidity
if (rsethAmountToMint == 0) revert ZeroRsETHMinted();
```

This mirrors the fix recommended in the referenced Union Protocol report: always revert when the computed share/token amount is zero, independent of any caller-supplied slippage parameter. [6](#0-5) 

### Proof of Concept

1. Suppose `rsETHPrice = 1e18` and a supported asset has `assetPrice = 1e16` (permitted by the oracle sanity check).
2. Attacker (or any user) calls `depositAsset(asset, 99, 0, "")`.
3. `getRsETHAmountToMint` computes `(99 * 1e16) / 1e18 = 0` (integer truncation).
4. `_beforeDeposit` checks `0 >= 0` (minRSETHAmountExpected = 0) — passes.
5. `IERC20(asset).safeTransferFrom(msg.sender, address(this), 99)` executes — 99 units leave the user.
6. `_mintRsETH(0)` executes — zero rsETH is minted to the user.
7. The user has permanently lost 99 units of the asset with no rsETH to redeem them.
8. Repeating this in a loop drains small depositors or accumulates protocol-owned assets at zero cost to the protocol, at the expense of users who omit slippage protection.

### Citations

**File:** contracts/LRTDepositPool.sol (L111-115)
```text
        uint256 rsethAmountToMint = _beforeDeposit(asset, depositAmount, minRSETHAmountExpected);

        // interactions
        IERC20(asset).safeTransferFrom(msg.sender, address(this), depositAmount);
        _mintRsETH(rsethAmountToMint);
```

**File:** contracts/LRTDepositPool.sol (L506-521)
```text
    function getRsETHAmountToMint(
        address asset,
        uint256 amount
    )
        public
        view
        override
        returns (uint256 rsethAmountToMint)
    {
        // setup oracle contract
        address lrtOracleAddress = lrtConfig.getContract(LRTConstants.LRT_ORACLE);
        ILRTOracle lrtOracle = ILRTOracle(lrtOracleAddress);

        // calculate rseth amount to mint based on asset amount and asset exchange rate
        rsethAmountToMint = (amount * lrtOracle.getAssetPrice(asset)) / lrtOracle.rsETHPrice();
    }
```

**File:** contracts/LRTOracle.sol (L103-105)
```text
        uint256 price = IPriceFetcher(priceOracle).getAssetPrice(asset);
        if (price > 1e19 || price < 1e16) {
            revert InvalidPriceOracle();
```

**File:** contracts/LRTOracle.sol (L218-221)
```text
        if (rsethSupply == 0) {
            rsETHPrice = 1 ether;
            highestRsethPrice = 1 ether;
            return;
```

**File:** contracts/LRTWithdrawalManager.sol (L162-163)
```text
        if (rsETHUnstaked == 0 || rsETHUnstaked < minRsEthAmountToWithdraw[asset]) {
            revert InvalidAmountToWithdraw();
```
