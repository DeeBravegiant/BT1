Audit Report

## Title
`RSETHPool` Token Deposits Collect Zero Protocol Fees Due to Uninitialized `tokenFeeBps` - (File: contracts/pools/RSETHPool.sol)

## Summary
`RSETHPool.sol` maintains a per-token fee mapping `tokenFeeBps[token]` used exclusively for ERC-20 token deposit fee calculation. Because `addSupportedToken()` never initialises `tokenFeeBps[token]`, Solidity's zero-default means every token deposit on Arbitrum permanently pays zero protocol fees. The protocol's entire token-deposit fee revenue is structurally foregone unless an admin separately calls `setTokenFeeBps()` after each token addition.

## Finding Description
`RSETHPool.sol` declares two distinct fee variables:

- `feeBps` — global basis-point fee for ETH deposits, set at `initialize()` time.
- `tokenFeeBps[token]` — per-token basis-point fee for ERC-20 deposits, **never set** during token registration. [1](#0-0) [2](#0-1) 

The token deposit fee path in `viewSwapRsETHAmountAndFee(amount, token)` reads exclusively from `tokenFeeBps[token]`: [3](#0-2) 

Because `tokenFeeBps` is a Solidity mapping, every entry defaults to `0`. `addSupportedToken()` registers the oracle and bridge but never touches `tokenFeeBps[token]`: [4](#0-3) 

The public `deposit(token, amount, referralId)` function is callable by any user and accumulates `feeEarnedInToken[token] += fee`, but since `fee == 0` always, nothing ever accrues: [5](#0-4) 

The admin-only `setTokenFeeBps()` exists to correct this post-hoc, but `addSupportedToken()` never calls it: [6](#0-5) 

Consequently, `withdrawFees(receiver, token)` will always transfer `0` tokens regardless of deposit volume: [7](#0-6) 

By contrast, `RSETHPoolV3.viewSwapRsETHAmountAndFee(amount, token)` uses the global `feeBps` for token deposits, so it always charges the configured fee: [8](#0-7) 

`RSETHPool.sol` is the only pool variant with the divergent `tokenFeeBps` design, and it is the only one where token deposits structurally yield zero fees.

## Impact Explanation
**High — Theft of unclaimed yield.**

The protocol is structurally entitled to fee revenue on every token deposit (wstETH and any future supported tokens) on Arbitrum. Because `tokenFeeBps[token]` is never initialised, 100% of that fee revenue is permanently diverted to depositors: each depositor receives rsETH calculated on the full deposit amount rather than the post-fee amount. `withdrawFees(receiver, token)` will always transfer `0`. This is not a temporary misconfiguration — it is the structural default state of the contract from the moment any token is added.

## Likelihood Explanation
**High.** The condition is unconditionally true: `tokenFeeBps[token] == 0` for every token unless an admin has explicitly called `setTokenFeeBps()` after the fact. Any ordinary user calling `deposit(token, amount, referralId)` on `RSETHPool.sol` exercises the zero-fee path. No special attacker capability, timing, or precondition is required — every token deposit since deployment has paid zero fees.

## Recommendation
Modify `addSupportedToken()` to accept a `_feeBps` parameter and set `tokenFeeBps[token] = _feeBps` at token registration time, mirroring how `feeBps` is set at `initialize()`. Alternatively, fall back to the global `feeBps` when `tokenFeeBps[token]` is zero, consistent with the behaviour of all other pool variants (`RSETHPoolV3`, etc.).

## Proof of Concept
1. Admin calls `addSupportedToken(wstETH, oracle, bridge)` on `RSETHPool.sol`. `tokenFeeBps[wstETH]` is `0` (never set).
2. User calls `deposit(wstETH, 10 ether, "ref")`.
3. `viewSwapRsETHAmountAndFee(10 ether, wstETH)` executes: `feeBpsForToken = tokenFeeBps[wstETH] = 0`, so `fee = 10 ether * 0 / 10_000 = 0`.
4. `feeEarnedInToken[wstETH] += 0` — no fee accrued.
5. User receives rsETH computed on the full `10 ether` with zero fee deducted.
6. Admin calls `withdrawFees(receiver, wstETH)` — transfers `0` tokens regardless of total deposit volume.

**Foundry fork test plan:**
```solidity
function test_tokenDepositZeroFee() public fork {
    // Precondition: feeBps > 0, tokenFeeBps[wstETH] == 0
    assertEq(pool.tokenFeeBps(wstETH), 0);
    assertGt(pool.feeBps(), 0);

    uint256 amount = 10 ether;
    deal(wstETH, user, amount);
    vm.prank(user);
    IERC20(wstETH).approve(address(pool), amount);
    vm.prank(user);
    pool.deposit(wstETH, amount, "ref");

    // Fee earned must be 0
    assertEq(pool.feeEarnedInToken(wstETH), 0);
}
```

### Citations

**File:** contracts/pools/RSETHPool.sol (L43-44)
```text
    uint256 public feeBps; // Basis points for fees for ETH deposits
    uint256 public feeEarnedInETH;
```

**File:** contracts/pools/RSETHPool.sol (L87-88)
```text
    /// @dev Mapping of token to fee basis points
    mapping(address token => uint256 feeBps) public tokenFeeBps;
```

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

**File:** contracts/pools/RSETHPool.sol (L335-337)
```text
        uint256 feeBpsForToken = tokenFeeBps[token];
        fee = amount * feeBpsForToken / 10_000;
        uint256 amountAfterFee = amount - fee;
```

**File:** contracts/pools/RSETHPool.sol (L427-443)
```text
    /// @dev Withdraws fees earned by the pool
    function withdrawFees(
        address receiver,
        address token
    )
        external
        nonReentrant
        onlySupportedToken(token)
        onlyRole(BRIDGER_ROLE)
    {
        // withdraw fees in ETH
        uint256 amountToSendInToken = feeEarnedInToken[token];
        feeEarnedInToken[token] = 0;
        IERC20(token).safeTransfer(receiver, amountToSendInToken);

        emit FeesWithdrawn(amountToSendInToken, token);
    }
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
