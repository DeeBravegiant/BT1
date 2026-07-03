### Title
Token Deposit Path Bypasses Protocol Fee via Zero-Initialized `tokenFeeBps` - (File: contracts/pools/RSETHPool.sol)

### Summary
In `RSETHPool.sol` (Arbitrum), two deposit paths exist to obtain `wrsETH`: depositing ETH directly and depositing a supported token (e.g., wstETH). The ETH path applies the global `feeBps`, while the token path applies `tokenFeeBps[token]`, which is never initialized in `addSupportedToken` and therefore defaults to `0`. Any depositor can avoid paying the protocol fee entirely by depositing a supported token instead of ETH, causing permanent loss of fee revenue to the protocol.

### Finding Description
`RSETHPool.sol` exposes two overloaded `deposit` functions. The ETH variant calls `viewSwapRsETHAmountAndFee(amount)`, which computes the fee using the global `feeBps`:

```solidity
// RSETHPool.sol line 312
fee = amount * feeBps / 10_000;
```

The token variant calls `viewSwapRsETHAmountAndFee(amount, token)`, which instead reads from the per-token mapping `tokenFeeBps[token]`:

```solidity
// RSETHPool.sol line 335-336
uint256 feeBpsForToken = tokenFeeBps[token];
fee = amount * feeBpsForToken / 10_000;
```

The `addSupportedToken` function registers a token's oracle and bridge but never sets `tokenFeeBps[token]`:

```solidity
// RSETHPool.sol lines 637-656
supportedTokenList.push(token);
supportedTokenOracle[token] = oracle;
tokenBridge[token] = bridge;
// tokenFeeBps[token] is never written → remains 0
```

The only way to set a non-zero token fee is via `setTokenFeeBps`, which requires `DEFAULT_ADMIN_ROLE` and is a separate, optional call. Until that call is made, every token deposit on Arbitrum is fee-free.

This is structurally identical to the BPool analog: two paths to the same economic outcome (receiving `wrsETH`) apply fees differently. ETH depositors pay `feeBps`; token depositors pay nothing.

### Impact Explanation
Every token deposit on `RSETHPool.sol` (Arbitrum) silently charges 0 fee regardless of the configured `feeBps`. A user who would otherwise pay, for example, 50 bps on an ETH deposit can instead acquire wstETH on a DEX and deposit it for 0 bps. The protocol permanently loses the fee revenue that should have been collected. This constitutes **theft of unclaimed yield** (High severity).

### Likelihood Explanation
The condition is always true for any token added via `addSupportedToken` unless `setTokenFeeBps` is separately called. Any depositor can observe the `tokenFeeBps` mapping on-chain (it returns 0 by default), confirm the fee-free path, and exploit it immediately. No special privileges, timing, or front-running are required.

### Recommendation
Initialize `tokenFeeBps[token]` inside `addSupportedToken` to the current global `feeBps` (or to an explicit parameter), so that newly added tokens inherit a non-zero fee rate by default. Alternatively, in `viewSwapRsETHAmountAndFee(amount, token)`, fall back to `feeBps` when `tokenFeeBps[token]` is 0:

```solidity
uint256 feeBpsForToken = tokenFeeBps[token] != 0 ? tokenFeeBps[token] : feeBps;
```

Add an invariant check or event emission in `addSupportedToken` to make the fee rate explicit and auditable.

### Proof of Concept
1. Observe that `RSETHPool.sol` on Arbitrum has `feeBps = 50` (0.5%) and wstETH is a supported token with `tokenFeeBps[wstETH] == 0`.
2. User A calls `deposit{value: 1 ETH}("")` → pays 0.005 ETH fee, receives `wrsETH` for 0.995 ETH.
3. User B acquires 1 ETH worth of wstETH on Uniswap (paying ~0.05% swap fee), then calls `deposit(wstETH, amount, "")` → pays 0 protocol fee, receives `wrsETH` for the full wstETH value.
4. User B receives materially more `wrsETH` than User A for the same economic input. The protocol collects 0 fee from User B.

**Relevant code locations:** [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

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

**File:** contracts/pools/RSETHPool.sol (L326-347)
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
        uint256 feeBpsForToken = tokenFeeBps[token];
        fee = amount * feeBpsForToken / 10_000;
        uint256 amountAfterFee = amount - fee;

        // rate of rsETH in ETH
        uint256 rsETHToETHrate = getRate();

        // rate of token in ETH
        uint256 tokenToETHRate = IOracle(supportedTokenOracle[token]).getRate();

        // Calculate the final rsETH amount
        rsETHAmount = amountAfterFee * tokenToETHRate / rsETHToETHrate;
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
