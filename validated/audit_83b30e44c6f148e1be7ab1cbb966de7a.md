Audit Report

## Title
Missing Output Amount Validation Allows Zero-rsETH Deposits That Permanently Absorb User Funds - (File: contracts/pools/RSETHPoolV3.sol)

## Summary

Every L2 pool `deposit()` function validates only that the raw input `amount != 0`, but never checks that the computed output `rsETHAmount` is also non-zero. Because `rsETHAmount = amountAfterFee * 1e18 / rsETHToETHrate` uses integer division, any deposit where `amountAfterFee * 1e18 < rsETHToETHrate` silently truncates to zero. The depositor's ETH or tokens are transferred into the pool and permanently absorbed, while the depositor receives nothing in return.

## Finding Description

In `RSETHPoolV3.viewSwapRsETHAmountAndFee()`, the output amount is computed as:

```solidity
rsETHAmount = amountAfterFee * 1e18 / rsETHToETHrate;
``` [1](#0-0) 

The only guard in `deposit()` is `if (amount == 0) revert InvalidAmount()`, which checks the raw input, not the output: [2](#0-1) 

When `rsETHAmount == 0`, `wrsETH.mint(msg.sender, 0)` executes without reverting, and the depositor's ETH is permanently held by the pool. The `limitDailyMint` modifier also computes `rsETHAmount` the same way and adds 0 to `dailyMintAmount`, so it does not revert either: [3](#0-2) 

The identical pattern exists across all pool variants:

- `RSETHPool.sol` ETH path line 319, token path line 346 [4](#0-3) 

- `RSETHPoolNoWrapper.sol` ETH path line 285, token path line 311 [5](#0-4) 

- `RSETHPoolV3ExternalBridge.sol` ETH path line 426, token path line 452 [6](#0-5) 

- `RSETHPoolV3WithNativeChainBridge.sol` ETH path line 343, token path line 370 [7](#0-6) 

For `RSETHPool` and `RSETHPoolNoWrapper`, the impact is worse: these pools use `safeTransfer` of pre-held rsETH rather than minting. A `safeTransfer` of 0 succeeds silently on standard ERC20 tokens, so the same zero-output path is reachable. [8](#0-7) 

## Impact Explanation

A depositor whose `rsETHAmount` rounds to zero has their ETH or tokens transferred into the pool irreversibly from their side, receives 0 wrsETH/rsETH, and cannot recover the deposit. The ETH is eventually bridged to L1 and credited to the protocol treasury. This is a direct, permanent loss of user funds with no recourse. This matches the allowed impact: **Low — Contract fails to deliver promised returns, but doesn't lose value** (from the depositor's perspective, value is lost; from the protocol's perspective, the pool absorbs it).

## Likelihood Explanation

The rounding threshold for ETH deposits is `amountAfterFee < rsETHToETHrate / 1e18`. Since `rsETHToETHrate` is expressed in 18-decimal fixed point (e.g., `1.05e18`), the threshold is approximately 1 wei of ETH — meaning any single-wei ETH deposit triggers this path today. For ERC20 token deposits, the threshold is `amountAfterFee < rsETHToETHrate / tokenToETHRate`, which can be larger for tokens with lower ETH value per unit. Any unprivileged depositor can trigger this without any special conditions, privileges, or victim cooperation. The path is fully reachable via a normal public `deposit()` call.

## Recommendation

Add a post-calculation guard in every `deposit()` function and in `viewSwapRsETHAmountAndFee` to revert when the computed output is zero:

```solidity
if (rsETHAmount == 0) revert InvalidAmount();
```

This should be placed immediately after `rsETHAmount` is computed in `viewSwapRsETHAmountAndFee`, so all callers (including `limitDailyMint`) benefit from the check. Apply this fix to all five pool contracts.

## Proof of Concept

1. `rsETHToETHrate = 1.05e18` (5% yield accrual, realistic mainnet value).
2. Depositor calls `RSETHPoolV3.deposit{value: 1}("")` — 1 wei ETH.
3. `amount = 1` passes `if (amount == 0)` guard.
4. `feeBps = 0` (or any value ≤ 1000), so `fee = 0`, `amountAfterFee = 1`.
5. `rsETHAmount = 1 * 1e18 / 1.05e18 = 0` (integer truncation).
6. `limitDailyMint` adds 0 to `dailyMintAmount` — no revert.
7. `wrsETH.mint(msg.sender, 0)` executes — depositor receives 0 wrsETH.
8. Depositor's 1 wei ETH is permanently absorbed by the pool.

**Foundry test sketch:**
```solidity
function test_zeroRsETHMintedForDustDeposit() public {
    // Set rsETHToETHrate = 1.05e18 via mock oracle
    mockOracle.setRate(1.05e18);
    uint256 balBefore = wrsETH.balanceOf(alice);
    vm.prank(alice);
    pool.deposit{value: 1}("");
    uint256 balAfter = wrsETH.balanceOf(alice);
    assertEq(balAfter - balBefore, 0); // alice received nothing
    assertEq(address(pool).balance, 1); // pool absorbed the wei
}
```

### Citations

**File:** contracts/pools/RSETHPoolV3.sol (L119-123)
```text
        if (dailyMintAmount + rsETHAmount > dailyMintLimit) {
            revert DailyMintLimitExceeded();
        }

        dailyMintAmount += rsETHAmount;
```

**File:** contracts/pools/RSETHPoolV3.sol (L254-262)
```text
        uint256 amount = msg.value;

        if (amount == 0) revert InvalidAmount();

        (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount);

        feeEarnedInETH += fee;

        wrsETH.mint(msg.sender, rsETHAmount);
```

**File:** contracts/pools/RSETHPoolV3.sol (L299-307)
```text
    function viewSwapRsETHAmountAndFee(uint256 amount) public view returns (uint256 rsETHAmount, uint256 fee) {
        fee = amount * feeBps / 10_000;
        uint256 amountAfterFee = amount - fee;

        // rate of rsETH in ETH
        uint256 rsETHToETHrate = getRate();

        // Calculate the final rsETH amount
        rsETHAmount = amountAfterFee * 1e18 / rsETHToETHrate;
```

**File:** contracts/pools/RSETHPool.sol (L265-278)
```text
    function deposit(string memory referralId) external payable nonReentrant whenNotPaused {
        if (!isEthDepositEnabled) revert EthDepositDisabled();
        uint256 amount = msg.value;

        if (amount == 0) revert InvalidAmount();

        (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount);

        feeEarnedInETH += fee;

        IERC20(address(wrsETH)).safeTransfer(msg.sender, rsETHAmount);

        emit SwapOccurred(msg.sender, rsETHAmount, fee, referralId);
    }
```

**File:** contracts/pools/RSETHPool.sol (L311-319)
```text
    function viewSwapRsETHAmountAndFee(uint256 amount) public view returns (uint256 rsETHAmount, uint256 fee) {
        fee = amount * feeBps / 10_000;
        uint256 amountAfterFee = amount - fee;

        // rate of rsETH in ETH
        uint256 rsETHToETHrate = getRate();

        // Calculate the final rsETH amount
        rsETHAmount = amountAfterFee * 1e18 / rsETHToETHrate;
```

**File:** contracts/pools/RSETHPoolNoWrapper.sol (L277-285)
```text
    function viewSwapRsETHAmountAndFee(uint256 amount) public view returns (uint256 rsETHAmount, uint256 fee) {
        fee = amount * feeBps / 10_000;
        uint256 amountAfterFee = amount - fee;

        // rate of rsETH in ETH
        uint256 rsETHToETHrate = getRate();

        // Calculate the final rsETH amount
        rsETHAmount = amountAfterFee * 1e18 / rsETHToETHrate;
```

**File:** contracts/pools/RSETHPoolV3ExternalBridge.sol (L418-426)
```text
    function viewSwapRsETHAmountAndFee(uint256 amount) public view returns (uint256 rsETHAmount, uint256 fee) {
        fee = amount * feeBps / 10_000;
        uint256 amountAfterFee = amount - fee;

        // rate of rsETH in ETH
        uint256 rsETHToETHrate = getRate();

        // Calculate the final rsETH amount
        rsETHAmount = amountAfterFee * 1e18 / rsETHToETHrate;
```

**File:** contracts/pools/RSETHPoolV3WithNativeChainBridge.sol (L335-343)
```text
    function viewSwapRsETHAmountAndFee(uint256 amount) public view returns (uint256 rsETHAmount, uint256 fee) {
        fee = amount * feeBps / 10_000;
        uint256 amountAfterFee = amount - fee;

        // rate of rsETH in ETH
        uint256 rsETHToETHrate = getRate();

        // Calculate the final rsETH amount
        rsETHAmount = amountAfterFee * 1e18 / rsETHToETHrate;
```
