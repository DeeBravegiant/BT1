### Title
Absolute `feeEarnedInToken` Accumulator Causes Arithmetic Underflow in `getTokenBalanceMinusFees`, Permanently Freezing Bridgeable Token Funds - (File: contracts/pools/RSETHPoolV3.sol, RSETHPoolV3ExternalBridge.sol, RSETHPoolV3WithNativeChainBridge.sol, RSETHPool.sol, RSETHPoolNoWrapper.sol)

---

### Summary

Across all L2 pool variants, protocol fees for ERC20 token deposits are tracked as an absolute accumulated counter (`feeEarnedInToken[token]`). The helper `getTokenBalanceMinusFees` computes `balanceOf(address(this)) - feeEarnedInToken[token]`. If the pool holds a rebasing token (e.g., stETH) whose balance decreases below the accumulated fee counter — as can happen during an EigenLayer slashing event — the subtraction underflows in Solidity 0.8+, reverting every call that depends on this function. Because all token bridging paths and the fee-withdrawal path both revert, the deposited tokens are permanently locked in the L2 pool with no recovery path short of a contract upgrade.

---

### Finding Description

Every L2 pool contract accumulates swap fees as an absolute token amount:

```solidity
// RSETHPoolV3.sol – deposit(address token, ...)
feeEarnedInToken[token] += fee;   // absolute counter, never decreases except on withdrawFees
``` [1](#0-0) 

The available-balance helper subtracts this counter from the live `balanceOf`:

```solidity
function getTokenBalanceMinusFees(address token) public view returns (uint256) {
    return IERC20(token).balanceOf(address(this)) - feeEarnedInToken[token];
}
``` [2](#0-1) 

The identical pattern exists in every pool variant: [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5) 

All token-bridging functions gate on this helper:

```solidity
// RSETHPoolV3.sol – moveAssetsForBridging(address token, uint256 amount)
uint256 tokenBalanceMinusFees = getTokenBalanceMinusFees(token);
if (amount > tokenBalanceMinusFees) revert InsufficientBalanceInPool();
``` [7](#0-6) 

```solidity
// RSETHPoolV3WithNativeChainBridge.sol – bridgeTokens(address token, uint256 amount)
uint256 tokenBalanceMinusFees = getTokenBalanceMinusFees(token);
if (amount > tokenBalanceMinusFees) revert InsufficientBalance();
``` [8](#0-7) 

The `withdrawFees` path for tokens also reads `feeEarnedInToken[token]` directly and attempts to transfer that full amount, which will also revert if the actual balance is lower:

```solidity
uint256 amountToSendInToken = feeEarnedInToken[token];
feeEarnedInToken[token] = 0;
IERC20(token).safeTransfer(receiver, amountToSendInToken);
``` [9](#0-8) 

**Trigger condition:** Any supported ERC20 token whose `balanceOf` can decrease after deposit — most notably stETH (a rebasing LST) or any token subject to slashing-driven balance reduction. When `balanceOf(address(this)) < feeEarnedInToken[token]`, the unchecked subtraction in `getTokenBalanceMinusFees` underflows and reverts (Solidity 0.8 checked arithmetic). Every downstream call — bridging and fee withdrawal — becomes permanently bricked for that token.

---

### Impact Explanation

**Critical — Permanent freezing of funds.**

All user-deposited tokens of the affected type are stranded in the L2 pool. The bridging path to L1 (`moveAssetsForBridging` / `bridgeTokens`) is blocked. The fee-withdrawal path (`withdrawFees`) is also blocked because it tries to transfer the full accumulated fee amount that now exceeds the actual balance. There is no in-protocol recovery mechanism; a proxy upgrade is required. Users who deposited and received wrsETH cannot have their underlying assets bridged to L1 for backing, creating a backing deficit.

---

### Likelihood Explanation

**Medium.** The protocol's purpose is liquid restaking of LSTs. stETH — the most widely held LST — is a rebasing token. If stETH (or any other rebasing LST) is added to `supportedTokenList` via `addSupportedToken`, the vulnerability is live. An EigenLayer slashing event, which reduces stETH balances protocol-wide, is the realistic trigger. The `addSupportedToken` function is admin-controlled but the protocol's design intent is to support LSTs broadly. [10](#0-9) 

---

### Recommendation

Replace the absolute fee counter with a **basis-point percentage** stored at deposit time, and compute the fee owed at withdrawal/bridging time as a fraction of the current balance. Alternatively, if an absolute counter must be kept, add a floor guard in `getTokenBalanceMinusFees`:

```solidity
function getTokenBalanceMinusFees(address token) public view returns (uint256) {
    uint256 bal = IERC20(token).balanceOf(address(this));
    uint256 fee = feeEarnedInToken[token];
    return bal > fee ? bal - fee : 0;
}
```

And cap `withdrawFees` to `min(feeEarnedInToken[token], balanceOf(address(this)))` to avoid the transfer revert. The deeper fix is to track fees as a percentage of the deposit amount rather than an absolute token quantity, so that any balance change is automatically reflected proportionally.

---

### Proof of Concept

1. Pool supports stETH as a token (`addSupportedToken(stETH, oracle, bridge)`).
2. User calls `deposit(stETH, 100e18, "")`. Fee at 10 bps = `0.1e18`. `feeEarnedInToken[stETH] = 0.1e18`. Pool holds `100e18` stETH.
3. EigenLayer slashing event reduces all stETH balances by 5%. Pool's `balanceOf` drops to `95e18`.
4. Bridger calls `moveAssetsForBridging(stETH, 94.9e18)`.
5. `getTokenBalanceMinusFees(stETH)` computes `95e18 - 0.1e18 = 94.9e18` — still works here.
6. Now consider a larger slash: pool holds `0.05e18` stETH (extreme slash or many fees accumulated). `feeEarnedInToken[stETH] = 0.1e18`.
7. `getTokenBalanceMinusFees(stETH)` → `0.05e18 - 0.1e18` → **arithmetic underflow → revert**.
8. `moveAssetsForBridging` reverts. `bridgeTokens` reverts. `withdrawFees` reverts (tries to transfer `0.1e18` but only `0.05e18` available). All token funds are permanently frozen in the L2 pool. [11](#0-10) [2](#0-1) [12](#0-11)

### Citations

**File:** contracts/pools/RSETHPoolV3.sol (L40-42)
```text
    mapping(address token => uint256 feeEarned) public feeEarnedInToken;
    mapping(address token => address oracle) public supportedTokenOracle;
    address[] public supportedTokenList;
```

**File:** contracts/pools/RSETHPoolV3.sol (L271-293)
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
        limitDailyMint(amount, token)
    {
        if (amount == 0) revert InvalidAmount();

        IERC20(token).safeTransferFrom(msg.sender, address(this), amount);

        (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount, token);

        feeEarnedInToken[token] += fee;

        wrsETH.mint(msg.sender, rsETHAmount);

        emit SwapOccurred(msg.sender, rsETHAmount, fee, referralId, token);
    }
```

**File:** contracts/pools/RSETHPoolV3.sol (L371-373)
```text
    function getTokenBalanceMinusFees(address token) public view returns (uint256) {
        return IERC20(token).balanceOf(address(this)) - feeEarnedInToken[token];
    }
```

**File:** contracts/pools/RSETHPoolV3.sol (L474-476)
```text
        uint256 amountToSendInToken = feeEarnedInToken[token];
        feeEarnedInToken[token] = 0;
        IERC20(token).safeTransfer(receiver, amountToSendInToken);
```

**File:** contracts/pools/RSETHPoolV3.sol (L496-510)
```text
    function moveAssetsForBridging(
        address token,
        uint256 amount
    )
        external
        nonReentrant
        onlySupportedToken(token)
        onlyRole(BRIDGER_ROLE)
    {
        if (amount == 0) revert InvalidAmount();

        // withdraw up to token - fees
        uint256 tokenBalanceMinusFees = getTokenBalanceMinusFees(token);
        if (amount > tokenBalanceMinusFees) revert InsufficientBalanceInPool();

```

**File:** contracts/pools/RSETHPoolV3ExternalBridge.sol (L502-504)
```text
    function getTokenBalanceMinusFees(address token) public view returns (uint256) {
        return IERC20(token).balanceOf(address(this)) - feeEarnedInToken[token];
    }
```

**File:** contracts/pools/RSETHPoolV3WithNativeChainBridge.sol (L384-386)
```text
    function getTokenBalanceMinusFees(address token) public view returns (uint256) {
        return IERC20(token).balanceOf(address(this)) - feeEarnedInToken[token];
    }
```

**File:** contracts/pools/RSETHPoolV3WithNativeChainBridge.sol (L567-568)
```text
        uint256 tokenBalanceMinusFees = getTokenBalanceMinusFees(token);
        if (amount > tokenBalanceMinusFees) revert InsufficientBalance();
```

**File:** contracts/pools/RSETHPool.sol (L396-398)
```text
    function getTokenBalanceMinusFees(address token) public view returns (uint256) {
        return IERC20(token).balanceOf(address(this)) - feeEarnedInToken[token];
    }
```

**File:** contracts/pools/RSETHPoolNoWrapper.sol (L361-363)
```text
    function getTokenBalanceMinusFees(address token) public view returns (uint256) {
        return IERC20(token).balanceOf(address(this)) - feeEarnedInToken[token];
    }
```
