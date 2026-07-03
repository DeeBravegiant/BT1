Audit Report

## Title
Zero Fee Applied to Token Deposits Due to Uninitialized `tokenFeeBps` Mapping - (File: contracts/pools/RSETHPool.sol)

## Summary

`RSETHPool.sol` uses a per-token `tokenFeeBps[token]` mapping for LST token deposit fees, but `addSupportedToken` never initializes this mapping entry, leaving it at the Solidity default of zero. Any user depositing a newly listed token pays zero protocol fees, while ETH depositors pay the intended `feeBps` rate. The protocol loses all fee revenue on token deposits until a separate `setTokenFeeBps` admin call is made.

## Finding Description

Two deposit paths exist in `RSETHPool.sol`:

**ETH deposit** (`deposit(string referralId)`) calls `viewSwapRsETHAmountAndFee(amount)`, which reads the global `feeBps` set at initialization: [1](#0-0) 

**Token deposit** (`deposit(address token, uint256 amount, string referralId)`) calls `viewSwapRsETHAmountAndFee(amount, token)`, which reads `tokenFeeBps[token]`: [2](#0-1) 

`addSupportedToken` registers a token as usable but never sets `tokenFeeBps[token]`: [3](#0-2) 

`setTokenFeeBps` exists as a separate, independent admin call with no on-chain enforcement requiring it to be called before or atomically with `addSupportedToken`: [4](#0-3) 

The token deposit function is immediately accessible to any unprivileged user after `addSupportedToken` is called, gated only by `onlySupportedToken(token)`: [5](#0-4) 

`RSETHPoolNoWrapper.sol` does not share this bug — its token deposit path uses the global `feeBps` directly: [6](#0-5) 

## Impact Explanation

Every token deposit made while `tokenFeeBps[token] == 0` pays zero protocol fees. `feeEarnedInToken[token]` remains at 0 for all such deposits. The protocol loses all fee revenue on token deposits — **theft of unclaimed yield** — for the entire window between `addSupportedToken` and a follow-up `setTokenFeeBps` call. This matches the allowed High impact: **Theft of unclaimed yield**.

## Likelihood Explanation

`addSupportedToken` is a routine operational action. There is no on-chain guard, require statement, or event that enforces `setTokenFeeBps` must be called first. The ETH deposit path works correctly with no analogous issue, so the omission is non-obvious. Any depositor — not just an attacker — can trigger zero-fee deposits immediately after token listing. The condition is realistic and repeatable across every newly listed token.

## Recommendation

Initialize `tokenFeeBps[token]` inside `addSupportedToken`, either defaulting to the global `feeBps` or requiring an explicit fee parameter:

```solidity
function addSupportedToken(
    address token,
    address oracle,
    address bridge,
    uint256 _feeBps
) external onlyRole(TIMELOCK_ROLE) {
    if (_feeBps > 10_000) revert InvalidFeeAmount();
    // ... existing checks ...
    supportedTokenList.push(token);
    supportedTokenOracle[token] = oracle;
    tokenBridge[token] = bridge;
    tokenFeeBps[token] = _feeBps;
    emit AddSupportedToken(token, oracle, bridge);
    emit TokenFeeBpsSet(token, _feeBps);
}
```

This ensures the fee is always set atomically with token listing, mirroring how `feeBps` is set at initialization for ETH deposits.

## Proof of Concept

1. Admin calls `addSupportedToken(wstETH, oracle, bridge)` — `tokenFeeBps[wstETH]` remains `0`.
2. Any user calls `deposit(wstETH, 100e18, "")`.
3. `viewSwapRsETHAmountAndFee(100e18, wstETH)` computes `feeBpsForToken = tokenFeeBps[wstETH] = 0`, so `fee = 100e18 * 0 / 10_000 = 0`.
4. `amountAfterFee = 100e18` — full amount converted, zero fee collected.
5. `feeEarnedInToken[wstETH]` stays at 0; protocol receives no fee revenue.
6. An ETH depositor of equivalent value pays `feeBps / 10_000` of their deposit as fee.

**Foundry test plan:**
```solidity
function test_zeroFeeOnTokenDeposit() public {
    vm.prank(timelockAdmin);
    pool.addSupportedToken(address(wstETH), address(oracle), address(bridge));

    // tokenFeeBps[wstETH] == 0 at this point
    assertEq(pool.tokenFeeBps(address(wstETH)), 0);

    deal(address(wstETH), user, 100e18);
    vm.startPrank(user);
    wstETH.approve(address(pool), 100e18);
    pool.deposit(address(wstETH), 100e18, "");
    vm.stopPrank();

    // Fee earned should be 0
    assertEq(pool.feeEarnedInToken(address(wstETH)), 0);
    // Compare: ETH depositor with equivalent value pays feeBps > 0
}
```

### Citations

**File:** contracts/pools/RSETHPool.sol (L284-305)
```text
    function deposit(
        address token,
        uint256 amount,
        string memory referralId
    )
        external
        nonReentrant
        whenNotPaused
        onlySupportedToken(token)
    {
        if (amount == 0) revert InvalidAmount();

        IERC20(token).safeTransferFrom(msg.sender, address(this), amount);

        (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount, token);

        feeEarnedInToken[token] += fee;

        IERC20(address(wrsETH)).safeTransfer(msg.sender, rsETHAmount);

        emit SwapOccurred(msg.sender, rsETHAmount, fee, referralId); // Add token address?
    }
```

**File:** contracts/pools/RSETHPool.sol (L311-312)
```text
    function viewSwapRsETHAmountAndFee(uint256 amount) public view returns (uint256 rsETHAmount, uint256 fee) {
        fee = amount * feeBps / 10_000;
```

**File:** contracts/pools/RSETHPool.sol (L335-336)
```text
        uint256 feeBpsForToken = tokenFeeBps[token];
        fee = amount * feeBpsForToken / 10_000;
```

**File:** contracts/pools/RSETHPool.sol (L583-594)
```text
    function setTokenFeeBps(
        address token,
        uint256 _feeBps
    )
        external
        onlyRole(DEFAULT_ADMIN_ROLE)
        onlySupportedToken(token)
    {
        if (_feeBps > 10_000) revert InvalidFeeAmount();
        tokenFeeBps[token] = _feeBps;
        emit TokenFeeBpsSet(token, _feeBps);
    }
```

**File:** contracts/pools/RSETHPool.sol (L637-656)
```text
    function addSupportedToken(address token, address oracle, address bridge) external onlyRole(TIMELOCK_ROLE) {
        UtilLib.checkNonZeroAddress(token);
        UtilLib.checkNonZeroAddress(oracle);
        UtilLib.checkNonZeroAddress(bridge);

        if (supportedTokenOracle[token] != address(0)) {
            revert AlreadySupportedToken();
        }
        if (tokenBridge[token] != address(0)) {
            revert AlreadySupportedToken();
        }
        if (IOracle(oracle).getRate() == 0) {
            revert UnsupportedOracle();
        }
        supportedTokenList.push(token);
        supportedTokenOracle[token] = oracle;
        tokenBridge[token] = bridge;

        emit AddSupportedToken(token, oracle, bridge);
    }
```

**File:** contracts/pools/RSETHPoolNoWrapper.sol (L301-301)
```text
        fee = amount * feeBps / 10_000;
```
