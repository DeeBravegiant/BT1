Audit Report

## Title
Zero-Output Deposit Silently Consumes ETH Without Minting wrsETH - (File: `contracts/pools/RSETHPoolV2NBA.sol`)

## Summary
`RSETHPoolV2NBA.deposit()` guards only against `amount == 0` but not against `rsETHAmount == 0` after integer division in `viewSwapRsETHAmountAndFee`. For dust ETH inputs, the division truncates to zero, `wrsETH.mint` succeeds silently, the depositor receives no wrsETH, and the ETH is later swept to L1 by the bridger via `moveAssetsForBridging()`, permanently removing the depositor's claim.

## Finding Description
In `deposit()`, the only input guard is `if (amount == 0) revert InvalidAmount()` at line 109. [1](#0-0) 

`viewSwapRsETHAmountAndFee` computes `rsETHAmount = amountAfterFee * 1e18 / rsETHToETHrate` using plain integer division. When `amountAfterFee * 1e18 < rsETHToETHrate` (e.g., `amount = 1 wei`, rate `~1.05e18`), the result truncates to `0`. [2](#0-1) 

`RsETHTokenWrapper.mint` delegates directly to OZ `_mint` with no zero-amount guard: [3](#0-2) 

The deposited ETH is untracked (fee is also 0 for 1-wei inputs, so `feeEarnedInETH` is not incremented). When `moveAssetsForBridging()` is called, it sends `address(this).balance - feeEarnedInETH` to the bridger, which includes the depositor's ETH, permanently removing any claim. [4](#0-3) 

## Impact Explanation
**Medium — Temporary (escalating to permanent) freezing of user funds.** The depositor's ETH is accepted and held in the contract with no wrsETH issued and no refund mechanism. Before the bridger sweeps, the funds are frozen. After `moveAssetsForBridging()` executes, the depositor's ETH is permanently gone with no recourse. The `SwapOccurred` event emits `rsETHAmount = 0`, which may mislead off-chain monitoring into treating the transaction as a successful swap.

## Likelihood Explanation
Preconditions are entirely user-controlled and require no privileged access. Any deposit where `amountAfterFee * 1e18 < rsETHToETHrate` triggers the bug. With a typical rate of `~1.05e18` and `feeBps = 0`, a 1-wei deposit suffices. With any non-zero `feeBps`, the threshold is slightly higher but still in the single-digit wei range. This can be triggered accidentally by any user sending a dust amount, or deliberately by a griefing actor.

## Recommendation
Add a post-calculation zero-output guard in `deposit()` immediately after the `viewSwapRsETHAmountAndFee` call:

```solidity
(uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount);
if (rsETHAmount == 0) revert InvalidAmount();
```

This ensures the invariant that any accepted ETH deposit produces a non-zero wrsETH amount, or the transaction reverts and ETH is returned to the caller. [5](#0-4) 

## Proof of Concept
Concrete arithmetic with `feeBps = 100`, `rsETHToETHrate = 1.05e18`, `amount = 1 wei`:

```
fee            = 1 * 100 / 10_000 = 0
amountAfterFee = 1 - 0            = 1
rsETHAmount    = 1 * 1e18 / 1.05e18 = 0   ← truncated to zero
```

`wrsETH.mint(msg.sender, 0)` succeeds. The depositor loses 1 wei, receives 0 wrsETH, and no revert occurs. A Foundry fuzz test targeting `deposit` with `amount` in `[1, rsETHToETHrate / 1e18]` would confirm `rsETHAmount == 0` and `wrsETH.balanceOf(depositor) == 0` post-call, while `address(pool).balance` increases by `amount`.

### Citations

**File:** contracts/pools/RSETHPoolV2NBA.sol (L106-118)
```text
    function deposit(string memory referralId) external payable nonReentrant whenNotPaused {
        uint256 amount = msg.value;

        if (amount == 0) revert InvalidAmount();

        (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount);

        feeEarnedInETH += fee;

        wrsETH.mint(msg.sender, rsETHAmount);

        emit SwapOccurred(msg.sender, rsETHAmount, fee, referralId);
    }
```

**File:** contracts/pools/RSETHPoolV2NBA.sol (L124-133)
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

**File:** contracts/pools/RSETHPoolV2NBA.sol (L151-159)
```text
    function moveAssetsForBridging() external nonReentrant onlyRole(BRIDGER_ROLE) {
        // withdraw ETH - fees
        uint256 ethBalanceMinusFees = address(this).balance - feeEarnedInETH;

        (bool success,) = msg.sender.call{ value: ethBalanceMinusFees }("");
        if (!success) revert TransferFailed();

        emit AssetsMovedForBridging(ethBalanceMinusFees);
    }
```

**File:** contracts/L2/RsETHTokenWrapper.sol (L190-192)
```text
    function mint(address _to, uint256 _amount) external onlyRole(MINTER_ROLE) {
        _mint(_to, _amount);
    }
```
