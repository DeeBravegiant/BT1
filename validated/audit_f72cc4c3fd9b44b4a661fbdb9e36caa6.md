Audit Report

## Title
Pool Deposit Functions Allow Zero rsETH Minting When Deposit Amount Rounds to Zero - (File: contracts/pools/RSETHPoolV3.sol)

## Summary
The `deposit` functions in `RSETHPoolV3` and all sibling pool contracts guard only against `amount == 0` but never validate that the computed `rsETHAmount` is non-zero. Because `viewSwapRsETHAmountAndFee` performs integer division, a sufficiently small deposit produces `rsETHAmount = 0`, causing the pool to accept and permanently retain the user's ETH or tokens while minting nothing in return.

## Finding Description
In `RSETHPoolV3.deposit(string)` ( [1](#0-0) ) and `deposit(address,uint256,string)` ( [2](#0-1) ), the only input guard is `if (amount == 0) revert InvalidAmount()`. No check is performed on the computed output.

`viewSwapRsETHAmountAndFee` computes:

```solidity
rsETHAmount = amountAfterFee * 1e18 / rsETHToETHrate;
``` [3](#0-2) 

When `amountAfterFee * 1e18 < rsETHToETHrate`, Solidity integer division truncates the result to `0`. With `feeBps = 0` and `rsETHToETHrate = 1.1e18` (a realistic post-accrual value), depositing `1 wei` yields `rsETHAmount = 0`.

For the ETH path, the `msg.value` is already in the contract before the mint executes, so `wrsETH.mint(msg.sender, 0)` runs and the ETH is irrecoverable by the user. [4](#0-3) 

For the token path, `safeTransferFrom` pulls the tokens from the user *before* the output amount is computed, so the tokens are transferred and then 0 rsETH is minted. [5](#0-4) 

The same pattern is replicated verbatim in `RSETHPoolV3ExternalBridge.sol` ( [6](#0-5) ), `AGETHPoolV3.sol` ( [7](#0-6) ), and all other pool variants listed in the claim.

The token-path variant in `viewSwapRsETHAmountAndFee(uint256,address)` has the same truncation risk: [8](#0-7) 

## Impact Explanation
A depositor calling `deposit` with a non-zero but sub-threshold amount has their ETH or ERC-20 tokens accepted and permanently retained by the pool contract while receiving 0 rsETH/wrsETH. The deposited value is not tracked in any user-recoverable accounting slot; it is swept to L1 by the `BRIDGER_ROLE`. The user has no on-chain recourse. This constitutes a permanent loss of deposited funds for the caller, matching **Low — Contract fails to deliver promised returns**.

## Likelihood Explanation
Low. The threshold is sub-wei for ETH at realistic rates, making accidental triggering by a human user extremely unlikely. It can be triggered by a contract integration that omits output validation before calling `deposit`, an automated dust-sweeping script, or a deliberate griefing actor. No privileged access is required; any external caller can reach the vulnerable path.

## Recommendation
Add a post-computation zero-output check in every `deposit` function across all pool variants:

```solidity
(uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount);
if (rsETHAmount == 0) revert InvalidAmount();
```

For the token deposit path, this check must be placed *before* `safeTransferFrom` to avoid pulling tokens that will never be matched with a mint.

## Proof of Concept
1. Deploy `RSETHPoolV3` with `feeBps = 0` and an oracle returning `rsETHToETHrate = 1.1e18`.
2. Call `deposit("")` with `msg.value = 1 wei`.
3. `viewSwapRsETHAmountAndFee(1)` returns `(rsETHAmount=0, fee=0)` due to `1 * 1e18 / 1.1e18 = 0`.
4. `feeEarnedInETH += 0` — the 1 wei is not credited anywhere user-accessible.
5. `wrsETH.mint(msg.sender, 0)` executes — user receives 0 wrsETH.
6. The 1 wei remains in the contract balance, recoverable only by `BRIDGER_ROLE`.

Foundry fuzz test sketch:
```solidity
function testFuzz_zeroMintOnTinyDeposit(uint256 amount) public {
    vm.assume(amount > 0 && amount < rsETHToETHrate / 1e18);
    vm.deal(user, amount);
    vm.prank(user);
    pool.deposit{value: amount}("");
    assertEq(wrsETH.balanceOf(user), 0);
    assertEq(address(pool).balance, amount); // ETH retained, user has nothing
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

**File:** contracts/pools/RSETHPoolV3.sol (L282-290)
```text
        if (amount == 0) revert InvalidAmount();

        IERC20(token).safeTransferFrom(msg.sender, address(this), amount);

        (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount, token);

        feeEarnedInToken[token] += fee;

        wrsETH.mint(msg.sender, rsETHAmount);
```

**File:** contracts/pools/RSETHPoolV3.sol (L307-307)
```text
        rsETHAmount = amountAfterFee * 1e18 / rsETHToETHrate;
```

**File:** contracts/pools/RSETHPoolV3.sol (L334-334)
```text
        rsETHAmount = amountAfterFee * tokenToETHRate / rsETHToETHrate;
```

**File:** contracts/pools/RSETHPoolV3ExternalBridge.sol (L373-383)
```text
        uint256 amount = msg.value;

        if (amount == 0) revert InvalidAmount();

        (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount);

        feeEarnedInETH += fee;

        wrsETH.mint(msg.sender, rsETHAmount);

        emit SwapOccurred(msg.sender, rsETHAmount, fee, referralId);
```

**File:** contracts/agETH/AGETHPoolV3.sol (L115-128)
```text
    function deposit(string memory referralId) external payable nonReentrant {
        if (!isEthDepositEnabled) revert EthDepositDisabled();
        uint256 amount = msg.value;

        if (amount == 0) revert InvalidAmount();

        (uint256 agETHAmount, uint256 fee) = viewSwapAgETHAmountAndFee(amount);

        feeEarnedInETH += fee;

        agETH.mint(msg.sender, agETHAmount);

        emit SwapOccurred(msg.sender, agETHAmount, fee, referralId);
    }
```
