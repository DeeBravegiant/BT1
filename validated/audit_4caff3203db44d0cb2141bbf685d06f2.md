Audit Report

## Title
`addSupportedToken` Does Not Initialize `tokenFeeBps`, Allowing Fee-Free Token Deposits - (File: contracts/pools/RSETHPool.sol)

## Summary
`RSETHPool.sol` declares a per-token fee mapping `tokenFeeBps` that defaults to `0` for any newly added token. Because `addSupportedToken` never initializes this mapping and `deposit(token, amount, referralId)` is immediately callable after the token is added, any depositor can deposit with zero fees before the admin separately calls `setTokenFeeBps`. The protocol permanently loses fee revenue for all deposits made in this window.

## Finding Description
`tokenFeeBps` is declared at line 88 as a plain mapping with no default initialization:

```solidity
mapping(address token => uint256 feeBps) public tokenFeeBps;
``` [1](#0-0) 

`addSupportedToken` (lines 637–656) only sets `supportedTokenOracle[token]` and `tokenBridge[token]`; it never touches `tokenFeeBps[token]`, leaving it at the Solidity default of `0`: [2](#0-1) 

The fee computation in `viewSwapRsETHAmountAndFee` reads directly from this uninitialized mapping:

```solidity
uint256 feeBpsForToken = tokenFeeBps[token];
fee = amount * feeBpsForToken / 10_000;
``` [3](#0-2) 

`deposit(token, amount, referralId)` is gated only by `whenNotPaused` and `onlySupportedToken(token)` — both of which pass immediately after `addSupportedToken` succeeds — so any caller can deposit at zero fee: [4](#0-3) 

The only way to set a non-zero fee is a completely separate call to `setTokenFeeBps`, which requires `DEFAULT_ADMIN_ROLE` (a different role from `TIMELOCK_ROLE` used by `addSupportedToken`), making atomic initialization structurally impossible: [5](#0-4) 

No existing guard prevents deposits between `addSupportedToken` and `setTokenFeeBps`. The `onlySupportedToken` modifier only checks that `supportedTokenOracle[token] != address(0)`, which is satisfied the moment `addSupportedToken` completes. [6](#0-5) 

## Impact Explanation
Every deposit made while `tokenFeeBps[token] == 0` charges zero fee. `feeEarnedInToken[token]` accumulates nothing for those deposits, and the loss is permanent — there is no mechanism to retroactively collect fees on past deposits. Because `addSupportedToken` goes through a timelock (publicly visible on-chain), the window is predictable and exploitable by any observer. This constitutes **High — Theft of unclaimed yield**: protocol fee revenue (yield) that should accrue to the protocol is permanently diverted to depositors who receive rsETH on the full deposit amount rather than `amount - fee`.

## Likelihood Explanation
`addSupportedToken` is gated by `TIMELOCK_ROLE`, meaning its execution is announced on-chain before it takes effect. Any observer can monitor the timelock queue and submit a deposit transaction immediately after execution. Even without deliberate front-running, the structural separation of roles (`TIMELOCK_ROLE` for `addSupportedToken` vs. `DEFAULT_ADMIN_ROLE` for `setTokenFeeBps`) guarantees a non-zero window between the two calls. The attack requires no special privileges, no flash loans, and no oracle manipulation — only a standard ERC-20 `approve` + `deposit` call sequence. Likelihood is **Medium**.

## Recommendation
Pass the initial `feeBps` as a parameter to `addSupportedToken` and set it atomically:

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
}
```

Alternatively, add a guard in `deposit(token, ...)` that reverts if `tokenFeeBps[token]` has never been explicitly configured (e.g., a separate `isTokenFeeInitialized[token]` flag set by `setTokenFeeBps`).

## Proof of Concept
1. Admin schedules `addSupportedToken(wstETH, oracle, bridge)` via the timelock; `tokenFeeBps[wstETH]` is `0` upon execution.
2. Attacker observes the pending timelock transaction and prepares a deposit.
3. Immediately after `addSupportedToken` executes, attacker calls `deposit(wstETH, 100 ether, "ref")`.
4. `viewSwapRsETHAmountAndFee(100 ether, wstETH)` computes `fee = 100 ether * 0 / 10_000 = 0`; `rsETHAmount = 100 ether * tokenToETHRate / rsETHToETHrate`.
5. Attacker receives rsETH for the full `100 ether`; `feeEarnedInToken[wstETH]` remains `0`.
6. Admin later calls `setTokenFeeBps(wstETH, 30)` — fee revenue from step 3–5 is permanently lost.

**Foundry test sketch:**
```solidity
function test_feeFreDepositWindow() public {
    // 1. Add token (simulating timelock execution)
    vm.prank(timelockAdmin);
    pool.addSupportedToken(address(wstETH), address(oracle), address(bridge));

    // 2. Deposit before setTokenFeeBps
    uint256 amount = 100 ether;
    deal(address(wstETH), attacker, amount);
    vm.startPrank(attacker);
    wstETH.approve(address(pool), amount);
    pool.deposit(address(wstETH), amount, "ref");
    vm.stopPrank();

    // 3. Assert zero fee collected
    assertEq(pool.feeEarnedInToken(address(wstETH)), 0);

    // 4. Admin sets fee — too late for prior deposits
    vm.prank(admin);
    pool.setTokenFeeBps(address(wstETH), 30);
    assertEq(pool.feeEarnedInToken(address(wstETH)), 0); // still 0
}
```

### Citations

**File:** contracts/pools/RSETHPool.sol (L87-88)
```text
    /// @dev Mapping of token to fee basis points
    mapping(address token => uint256 feeBps) public tokenFeeBps;
```

**File:** contracts/pools/RSETHPool.sol (L100-103)
```text
    modifier onlySupportedToken(address token) {
        if (supportedTokenOracle[token] == address(0)) revert UnsupportedToken();
        _;
    }
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
