Audit Report

## Title
Zero `rsETHAmount` Minted on Dust Deposits Due to Integer Division Truncation - (File: contracts/pools/RSETHPoolV3ExternalBridge.sol)

## Summary
The `deposit()` functions across multiple L2 pool contracts check that the input `amount` is non-zero but never verify that the computed `rsETHAmount` is non-zero before minting. Due to integer division truncation in `viewSwapRsETHAmountAndFee`, a depositor sending a dust ETH amount receives 0 wrsETH while their deposited ETH is permanently absorbed into the pool with no recourse.

## Finding Description
In `RSETHPoolV3ExternalBridge.sol`, `RSETHPoolV3.sol`, `RSETHPoolV3WithNativeChainBridge.sol`, `RSETHPoolNoWrapper.sol`, `RSETHPoolV2ExternalBridge.sol`, and `RSETHPoolV2NBA.sol`, the ETH deposit path is:

```solidity
uint256 amount = msg.value;
if (amount == 0) revert InvalidAmount();
(uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount);
feeEarnedInETH += fee;
wrsETH.mint(msg.sender, rsETHAmount);  // rsETHAmount can be 0
``` [1](#0-0) 

The rate computation truncates to zero when `amountAfterFee * 1e18 < rsETHToETHrate`:

```solidity
fee = amount * feeBps / 10_000;
uint256 amountAfterFee = amount - fee;
uint256 rsETHToETHrate = getRate();
rsETHAmount = amountAfterFee * 1e18 / rsETHToETHrate;
``` [2](#0-1) 

With a realistic `rsETHToETHrate = 1.05e18` (rsETH accrues yield over time), any deposit where `amountAfterFee = 1 wei` produces `rsETHAmount = 1 * 1e18 / 1.05e18 = 0`. The `amount == 0` guard does not protect against this because `amount = 1` passes the check while the derived `rsETHAmount = 0`.

The `wrsETH.mint` implementation calls OpenZeppelin's `_mint(_to, _amount)`: [3](#0-2) 

Standard ERC20 `_mint` with `amount = 0` does not revert — it succeeds silently, emitting a `Transfer` event for 0 tokens. The depositor's ETH is credited to the pool's bridgeable balance and eventually bridged to L1, but the depositor holds no wrsETH and has no claim on it.

The same truncation applies to the token deposit path where `rsETHAmount = amountAfterFee * tokenToETHRate / rsETHToETHrate`: [4](#0-3) 

In `RSETHPoolNoWrapper.sol`, the same truncation applies but the call is `rsETH.safeTransfer(msg.sender, rsETHAmount)` with `rsETHAmount = 0`, which also succeeds silently under OpenZeppelin's SafeERC20: [5](#0-4) 

The identical pattern is confirmed across all six pool variants: [6](#0-5) [7](#0-6) [8](#0-7) 

## Impact Explanation
A depositor who sends a dust ETH amount (e.g., 1 wei) receives 0 wrsETH. The deposited ETH is retained by the pool and eventually bridged to L1, but the depositor holds no receipt tokens and has no mechanism to recover the funds. This matches the allowed impact: **Low — Contract fails to deliver promised returns**. The monetary loss per call is negligible (dust amounts), but the contract definitively fails to deliver the promised wrsETH receipt tokens for a valid non-zero deposit.

## Likelihood Explanation
Any unprivileged external caller can trigger this by calling `deposit{value: 1}()` on any of the six affected pool contracts while the pool is unpaused. No special role, precondition, or privileged access is required. The condition is deterministic and reproducible on every L2 pool variant. Likelihood is **Low** because the monetary loss per call is negligible (1 wei), but the code path is unconditionally reachable by any external caller.

## Recommendation
Add a post-computation zero-check on `rsETHAmount` in every `deposit()` function, immediately after the `viewSwapRsETHAmountAndFee` call:

```solidity
(uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount);
if (rsETHAmount == 0) revert InvalidAmount();
```

This must be applied to both the ETH and token deposit paths in all six affected contracts: `RSETHPoolV3ExternalBridge.sol`, `RSETHPoolV3.sol`, `RSETHPoolV3WithNativeChainBridge.sol`, `RSETHPoolNoWrapper.sol`, `RSETHPoolV2ExternalBridge.sol`, and `RSETHPoolV2NBA.sol`.

## Proof of Concept
1. Deploy `RSETHPoolV3ExternalBridge` (or any of the six variants) with an oracle returning `rsETHToETHrate = 1.05e18` (realistic post-yield value).
2. Call `deposit{value: 1}("")` — `amount = 1`, passes the `amount == 0` guard.
3. `viewSwapRsETHAmountAndFee(1)` computes: `fee = 0` (with `feeBps = 0`), `amountAfterFee = 1`, `rsETHAmount = 1 * 1e18 / 1.05e18 = 0`.
4. `wrsETH.mint(msg.sender, 0)` executes successfully; depositor receives 0 wrsETH.
5. The 1 wei ETH is credited to the pool's bridgeable balance (`address(this).balance` increases by 1), permanently inaccessible to the depositor.

Foundry fuzz test sketch:
```solidity
function testFuzz_dustDepositMintsZero(uint256 dustAmount) public {
    dustAmount = bound(dustAmount, 1, pool.getRate() / 1e18); // amounts that truncate to 0
    uint256 balBefore = wrsETH.balanceOf(user);
    vm.prank(user);
    pool.deposit{value: dustAmount}("");
    assertEq(wrsETH.balanceOf(user), balBefore); // user received 0 wrsETH
    assertGt(address(pool).balance, 0);           // ETH is stuck in pool
}
```

### Citations

**File:** contracts/pools/RSETHPoolV3ExternalBridge.sol (L373-381)
```text
        uint256 amount = msg.value;

        if (amount == 0) revert InvalidAmount();

        (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount);

        feeEarnedInETH += fee;

        wrsETH.mint(msg.sender, rsETHAmount);
```

**File:** contracts/pools/RSETHPoolV3ExternalBridge.sol (L418-427)
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

**File:** contracts/pools/RSETHPoolV3ExternalBridge.sol (L442-452)
```text
        fee = amount * feeBps / 10_000;
        uint256 amountAfterFee = amount - fee;

        // rate of rsETH in ETH
        uint256 rsETHToETHrate = getRate();

        // rate of token in ETH
        uint256 tokenToETHRate = IOracle(supportedTokenOracle[token]).getRate();

        // Calculate the final rsETH amount
        rsETHAmount = amountAfterFee * tokenToETHRate / rsETHToETHrate;
```

**File:** contracts/L2/RsETHTokenWrapper.sol (L190-192)
```text
    function mint(address _to, uint256 _amount) external onlyRole(MINTER_ROLE) {
        _mint(_to, _amount);
    }
```

**File:** contracts/pools/RSETHPoolNoWrapper.sol (L237-241)
```text
        (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount);

        feeEarnedInETH += fee;

        rsETH.safeTransfer(msg.sender, rsETHAmount);
```

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

**File:** contracts/pools/RSETHPoolV3.sol (L254-264)
```text
        uint256 amount = msg.value;

        if (amount == 0) revert InvalidAmount();

        (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount);

        feeEarnedInETH += fee;

        wrsETH.mint(msg.sender, rsETHAmount);

        emit SwapOccurred(msg.sender, rsETHAmount, fee, referralId);
```

**File:** contracts/pools/RSETHPoolV3WithNativeChainBridge.sol (L289-300)
```text
        if (!isEthDepositEnabled) revert EthDepositDisabled();
        uint256 amount = msg.value;

        if (amount == 0) revert InvalidAmount();

        (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount);

        feeEarnedInETH += fee;

        wrsETH.mint(msg.sender, rsETHAmount);

        emit SwapOccurred(msg.sender, rsETHAmount, fee, referralId);
```
