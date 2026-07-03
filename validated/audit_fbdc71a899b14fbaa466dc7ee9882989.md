### Title
Zero Fee Charged on Token Deposits Due to Uninitialized `tokenFeeBps` — (File: contracts/pools/RSETHPool.sol)

### Summary

`RSETHPool.sol` (deployed on Arbitrum) uses two separate fee sources for its two deposit paths: ETH deposits use the global `feeBps` variable, while token deposits use a per-token `tokenFeeBps[token]` mapping. Because `addSupportedToken` never initializes `tokenFeeBps[token]`, every newly added token defaults to a fee of 0. Users depositing tokens pay no fee, while ETH depositors pay the intended `feeBps` rate.

### Finding Description

`RSETHPool.sol` maintains two distinct fee variables:

- `feeBps` — set at initialization, used for ETH deposits.
- `tokenFeeBps` — a per-token mapping, used for token deposits.

The ETH deposit path: [1](#0-0) 

The token deposit path: [2](#0-1) 

When a new token is added via `addSupportedToken`, only the oracle and bridge are stored — `tokenFeeBps[token]` is never set: [3](#0-2) 

Because Solidity mappings default to zero, `tokenFeeBps[token]` is `0` for every newly added token. The fee calculation therefore yields `fee = amount * 0 / 10_000 = 0`. The admin must separately call `setTokenFeeBps` to correct this: [4](#0-3) 

This is the direct analog to the reported Solana issue: one code path (ETH deposit) uses a correctly initialized fee source, while the other (token deposit) uses a broader/different source that can silently be zero, resulting in inconsistent fee collection.

### Impact Explanation

Any user depositing a supported token (e.g., wstETH on Arbitrum) through `RSETHPool.sol` pays **zero protocol fees** until an admin explicitly calls `setTokenFeeBps`. The protocol loses all fee revenue from token deposits during this window. This constitutes **theft of unclaimed yield** (protocol fee revenue) — classified as **High** impact.

### Likelihood Explanation

Likelihood is **High**. The `addSupportedToken` function is the only entry point for enabling token deposits, and it never sets `tokenFeeBps`. Every token added to the pool is immediately exploitable. Any user who calls `deposit(token, amount, referralId)` before the admin separately calls `setTokenFeeBps` benefits from zero fees. There is no on-chain enforcement or default value preventing this.

### Recommendation

Initialize `tokenFeeBps[token]` inside `addSupportedToken` to a sensible default (e.g., the global `feeBps`), or require the caller to supply a fee value:

```solidity
function addSupportedToken(
    address token,
    address oracle,
    address bridge,
    uint256 _feeBps          // <-- add this parameter
) external onlyRole(TIMELOCK_ROLE) {
    ...
    tokenFeeBps[token] = _feeBps;
    ...
}
```

Alternatively, fall back to `feeBps` when `tokenFeeBps[token]` is zero, mirroring the ETH deposit behavior.

### Proof of Concept

1. Admin calls `addSupportedToken(wstETH, oracle, bridge)` — `tokenFeeBps[wstETH]` remains `0`.
2. User calls `deposit(wstETH, 10 ether, "ref")`.
3. `viewSwapRsETHAmountAndFee(10 ether, wstETH)` executes:
   - `feeBpsForToken = tokenFeeBps[wstETH]` → `0`
   - `fee = 10 ether * 0 / 10_000` → `0`
   - `amountAfterFee = 10 ether`
4. User receives rsETH equivalent to the full `10 ether` of wstETH with **zero fee deducted**.
5. `feeEarnedInToken[wstETH]` remains `0`; the protocol collects nothing.
6. Compare: an ETH depositor of the same value pays `feeBps` (e.g., 10 bps = 0.1%), losing `0.01 ether` to fees. [5](#0-4) [2](#0-1) [3](#0-2)

### Citations

**File:** contracts/pools/RSETHPool.sol (L88-88)
```text
    mapping(address token => uint256 feeBps) public tokenFeeBps;
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
