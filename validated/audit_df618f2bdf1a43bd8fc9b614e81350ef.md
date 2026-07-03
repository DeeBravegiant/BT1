Audit Report

## Title
Inconsistent Fee Rate Application Between ETH and Token Deposits Allows Fee-Free Token Swaps - (File: contracts/pools/RSETHPool.sol)

## Summary
`RSETHPool.sol` applies fees inconsistently: ETH deposits use the global `feeBps` variable, while token deposits use `tokenFeeBps[token]`, a per-token mapping that defaults to `0` and is never initialized during token onboarding via `addSupportedToken` or `reinitialize`. Any depositor can swap supported tokens for rsETH with zero fee, causing the protocol to collect no fee revenue on token deposits until an admin separately calls `setTokenFeeBps`.

## Finding Description
The ETH deposit path in `viewSwapRsETHAmountAndFee(uint256 amount)` at L312 computes `fee = amount * feeBps / 10_000`, always using the globally initialized `feeBps`. The token deposit path in `viewSwapRsETHAmountAndFee(uint256 amount, address token)` at L335–336 instead reads `tokenFeeBps[token]`, which is a Solidity mapping that defaults to `0` for every key never explicitly written.

`addSupportedToken` (L637–656) registers `supportedTokenOracle[token]` and `tokenBridge[token]` but never writes `tokenFeeBps[token]`. The `reinitialize` function at L163–185 similarly sets `tokenBridge[_token]` without touching `tokenFeeBps`. The only write path is the separate admin function `setTokenFeeBps` (L583–594), which requires `DEFAULT_ADMIN_ROLE` and must be called as a distinct follow-up transaction.

Because wstETH was onboarded via `reinitialize` (L163–185) without a subsequent `setTokenFeeBps` call, `tokenFeeBps[wstETH]` is `0` in the live deployed contract. Any call to `deposit(token, amount, referralId)` (L284–305) invokes `viewSwapRsETHAmountAndFee(amount, token)`, computes `fee = 0`, and transfers the full rsETH equivalent to the caller. `feeEarnedInToken[token]` remains `0`, so the protocol treasury collects nothing.

No existing guard prevents this: `onlySupportedToken` only checks that `supportedTokenOracle[token] != address(0)`, which is satisfied for wstETH. `whenNotPaused` and `nonReentrant` are irrelevant to fee computation.

## Impact Explanation
Every token deposit made while `tokenFeeBps[token] == 0` results in zero fee accrual to the protocol. The value lost equals `feeBps / 10_000` of each deposited token amount — a continuous, compounding loss of protocol yield. This matches the allowed impact: **High — Theft of unclaimed yield**.

## Likelihood Explanation
The precondition (`tokenFeeBps[token] == 0`) is the Solidity default and is already present for at least wstETH on the live Arbitrum deployment. Any depositor who reads the contract state can observe this and immediately exploit it by calling `deposit(wstETH, amount, "")`. No special privileges, flash loans, or oracle manipulation are required. The condition persists for every future token added via `addSupportedToken` until the admin issues a separate `setTokenFeeBps` call.

## Recommendation
Initialize `tokenFeeBps[token]` inside `addSupportedToken` and all `reinitialize` paths to the current global `feeBps`, or require an explicit fee argument. Alternatively, fall back to `feeBps` when `tokenFeeBps[token]` is zero in `viewSwapRsETHAmountAndFee`:

```solidity
uint256 feeBpsForToken = tokenFeeBps[token] != 0 ? tokenFeeBps[token] : feeBps;
fee = amount * feeBpsForToken / 10_000;
```

## Proof of Concept
1. Confirm `tokenFeeBps[wstETH] == 0` on the deployed Arbitrum `RSETHPool` (wstETH was added via `reinitialize` at L163–185, which never calls `setTokenFeeBps`).
2. Call `deposit(wstETH, 10 ether, "")`.
3. Internally, `viewSwapRsETHAmountAndFee(10 ether, wstETH)` executes: `feeBpsForToken = tokenFeeBps[wstETH] = 0`, `fee = 10 ether * 0 / 10_000 = 0`.
4. Caller receives the full rsETH equivalent of 10 wstETH; `feeEarnedInToken[wstETH]` remains `0`.
5. Repeat for any subsequent token added via `addSupportedToken` before `setTokenFeeBps` is called.

Foundry fork test outline:
```solidity
function test_zeroFeeTokenDeposit() public fork {
    uint256 amount = 10 ether;
    deal(wstETH, attacker, amount);
    vm.startPrank(attacker);
    IERC20(wstETH).approve(address(pool), amount);
    pool.deposit(wstETH, amount, "");
    vm.stopPrank();
    assertEq(pool.feeEarnedInToken(wstETH), 0); // fee never collected
}
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

**File:** contracts/pools/RSETHPool.sol (L163-185)
```text
    function reinitialize(
        address _l2Bridge,
        address _messenger,
        address _token,
        address _tokenBridge
    )
        external
        reinitializer(4)
        onlyRole(DEFAULT_ADMIN_ROLE)
        onlySupportedToken(_token)
    {
        UtilLib.checkNonZeroAddress(_l2Bridge);
        UtilLib.checkNonZeroAddress(_messenger);
        UtilLib.checkNonZeroAddress(_tokenBridge);

        l2Bridge = _l2Bridge;
        messenger = _messenger;
        tokenBridge[_token] = _tokenBridge;

        emit L2BridgeSet(_l2Bridge);
        emit MessengerSet(_messenger);
        emit TokenBridgeSet(_token, _tokenBridge);
    }
```

**File:** contracts/pools/RSETHPool.sol (L311-320)
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
