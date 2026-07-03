Audit Report

## Title
Missing Zero-Output Guard in L2 Pool `deposit()` Functions Allows Silent Loss of Dust Deposits - (File: contracts/pools/RSETHPoolV3.sol, contracts/pools/RSETHPoolV3ExternalBridge.sol, contracts/pools/RSETHPool.sol, contracts/pools/RSETHPoolNoWrapper.sol)

## Summary
All four L2 deposit pool contracts check that the input `amount` is non-zero but never verify that the computed `rsETHAmount` is also non-zero before minting or transferring. Because `viewSwapRsETHAmountAndFee` uses integer division, a dust-sized deposit (e.g., 1 wei ETH when `rsETHToETHrate ≈ 1.05e18`) silently truncates to `rsETHAmount = 0`. The deposited asset is retained by the pool while the user receives nothing, with no revert.

## Finding Description
Every L2 pool's ETH deposit path follows the same pattern:

```solidity
if (amount == 0) revert InvalidAmount();   // guards only zero input
(uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount);
feeEarnedInETH += fee;
wrsETH.mint(msg.sender, rsETHAmount);      // rsETHAmount can be 0
``` [1](#0-0) 

The rate computation inside `viewSwapRsETHAmountAndFee` is:

```solidity
rsETHAmount = amountAfterFee * 1e18 / rsETHToETHrate;
``` [2](#0-1) 

When `amountAfterFee < rsETHToETHrate / 1e18` (i.e., `amountAfterFee < ~1` for a rate near `1e18`), integer truncation yields `rsETHAmount = 0`. The `amount == 0` guard does not protect against this because the input can be 1 wei (non-zero) while the output is still 0.

`RsETHTokenWrapper.mint` delegates to OpenZeppelin's `_mint`, which accepts a zero amount without reverting. [3](#0-2) 

The same pattern is confirmed in all four cited contracts:
- `RSETHPoolV3ExternalBridge.sol` lines 377–384, 418–427 [4](#0-3) 
- `RSETHPool.sol` lines 271–278 [5](#0-4) 
- `RSETHPoolNoWrapper.sol` lines 237–243 [6](#0-5) 

By contrast, the L1 `LRTDepositPool` enforces a caller-supplied `minRSETHAmountExpected` and reverts if the computed mint amount falls below it: [7](#0-6) 

The L2 pools have no equivalent guard.

## Impact Explanation
A depositor who sends a dust ETH or token amount receives 0 rsETH while the pool permanently retains the deposited asset. The user cannot recover the deposit. This matches the allowed Low impact: **"Contract fails to deliver promised returns, but doesn't lose value"** — the protocol retains the deposited value; the user does not receive the promised rsETH output.

## Likelihood Explanation
The zero-output condition requires only that `amountAfterFee * 1e18 < rsETHToETHrate`, which is satisfied by any deposit of 1 wei ETH at any realistic rsETH/ETH rate above 1e18. Any user — whether by mistake, through a buggy integration, or via a scripted dust-deposit — can trigger this with a single public call. No special privileges or external conditions are required. Accidental occurrence is rare but the absence of any on-chain guard means the contract silently finalises such transactions.

## Recommendation
Add an explicit zero-output check immediately after computing `rsETHAmount` in every L2 pool `deposit()` function, mirroring the L1 pattern:

```solidity
(uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount);
if (rsETHAmount == 0) revert InvalidAmount();
```

Alternatively, expose a `minRsETHAmountExpected` parameter (as `LRTDepositPool` does) so callers can enforce their own slippage tolerance.

## Proof of Concept
1. Deploy `RSETHPoolV3` with `feeBps = 0` and an oracle returning `rsETHToETHrate = 1.05e18`.
2. Call `deposit{value: 1}("")` (1 wei ETH).
3. `viewSwapRsETHAmountAndFee(1)` computes: `fee = 0`, `amountAfterFee = 1`, `rsETHAmount = 1 * 1e18 / 1.05e18 = 0`.
4. `wrsETH.mint(msg.sender, 0)` succeeds silently; user receives 0 wrsETH.
5. The 1 wei ETH is held by the pool; `feeEarnedInETH` is unchanged. The user's ETH is unrecoverable.

Foundry fuzz test sketch:
```solidity
function testFuzz_dustDepositYearsZeroRsETH(uint96 amount) public {
    vm.assume(amount > 0 && amount < rsETHToETHrate / 1e18);
    uint256 balBefore = wrsETH.balanceOf(user);
    pool.deposit{value: amount}("");
    assertEq(wrsETH.balanceOf(user), balBefore); // user received nothing
}
```

### Citations

**File:** contracts/pools/RSETHPoolV3.sol (L256-262)
```text
        if (amount == 0) revert InvalidAmount();

        (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount);

        feeEarnedInETH += fee;

        wrsETH.mint(msg.sender, rsETHAmount);
```

**File:** contracts/pools/RSETHPoolV3.sol (L299-308)
```text
    function viewSwapRsETHAmountAndFee(uint256 amount) public view returns (uint256 rsETHAmount, uint256 fee) {
        fee = amount * feeBps / 10_000;
        uint256 amountAfterFee = amount - fee;

        // rate of rsETH in ETH
        uint256 rsETHToETHrate = getRate();

        // Calculate the final rsETH amount
        rsETHAmount = amountAfterFee * 1e18 / rsETHToETHrate;
    }
```

**File:** contracts/L2/RsETHTokenWrapper.sol (L190-192)
```text
    function mint(address _to, uint256 _amount) external onlyRole(MINTER_ROLE) {
        _mint(_to, _amount);
    }
```

**File:** contracts/pools/RSETHPoolV3ExternalBridge.sol (L377-384)
```text
        (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount);

        feeEarnedInETH += fee;

        wrsETH.mint(msg.sender, rsETHAmount);

        emit SwapOccurred(msg.sender, rsETHAmount, fee, referralId);
    }
```

**File:** contracts/pools/RSETHPool.sol (L271-278)
```text
        (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount);

        feeEarnedInETH += fee;

        IERC20(address(wrsETH)).safeTransfer(msg.sender, rsETHAmount);

        emit SwapOccurred(msg.sender, rsETHAmount, fee, referralId);
    }
```

**File:** contracts/pools/RSETHPoolNoWrapper.sol (L237-243)
```text
        (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount);

        feeEarnedInETH += fee;

        rsETH.safeTransfer(msg.sender, rsETHAmount);

        emit SwapOccurred(msg.sender, rsETHAmount, fee, referralId);
```

**File:** contracts/LRTDepositPool.sol (L665-669)
```text
        rsethAmountToMint = getRsETHAmountToMint(asset, depositAmount);

        if (rsethAmountToMint < minRSETHAmountExpected) {
            revert MinimumAmountToReceiveNotMet();
        }
```
