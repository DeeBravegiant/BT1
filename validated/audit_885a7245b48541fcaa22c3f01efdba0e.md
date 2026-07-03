### Title
Inconsistent Fee Rate Application Between ETH and Token Deposits Allows Fee-Free Token Swaps - (File: contracts/pools/RSETHPool.sol)

### Summary
`RSETHPool.sol` applies fees inconsistently between ETH deposits and ERC20 token deposits. ETH deposits always use the initialized `feeBps` global rate, while token deposits use a per-token `tokenFeeBps[token]` mapping that defaults to `0` and is never set during token onboarding. Any depositor can swap supported tokens for rsETH with zero fee, permanently stealing the yield that should accrue to the protocol.

### Finding Description
In `RSETHPool.sol`, two overloaded `viewSwapRsETHAmountAndFee` functions compute fees differently:

**ETH path** — always uses the global `feeBps` set at initialization: [1](#0-0) 

**Token path** — uses `tokenFeeBps[token]`, a mapping that is never written during token registration: [2](#0-1) 

`addSupportedToken` registers the oracle and bridge for a new token but never touches `tokenFeeBps`: [3](#0-2) 

Because Solidity mapping values default to `0`, every newly added token has `tokenFeeBps[token] == 0`. The only way to correct this is a separate, optional admin call to `setTokenFeeBps`: [4](#0-3) 

This is the direct analog of the reported bug: the `add_liquidity` path (ETH deposit) correctly applies the fee rate, while the `remove_liquidity`-equivalent path (token deposit) silently uses a zero rate, providing no fee protection.

### Impact Explanation
Any user calling `deposit(token, amount, referralId)` while `tokenFeeBps[token] == 0` receives the full rsETH equivalent of their token with **zero fee deducted**. `feeEarnedInToken[token]` remains `0`, so the protocol treasury collects nothing. Because `RSETHPool` is the live Arbitrum pool and wstETH is a supported token, this is immediately reachable on mainnet. The stolen value equals `feeBps / 10_000` of every token deposit made while the fee is unset — a continuous, compounding loss of protocol yield.

**Impact: High — Theft of unclaimed yield.**

### Likelihood Explanation
`addSupportedToken` is the only onboarding path for new tokens and it never sets `tokenFeeBps`. The admin must remember to issue a separate `setTokenFeeBps` transaction. Any token added without that follow-up call is permanently fee-free until corrected. Given that wstETH was already added via `reinitialize` (which also does not set `tokenFeeBps`), the condition is already live. Any depositor who inspects the contract state can observe `tokenFeeBps[wstETH] == 0` and exploit it immediately.

**Likelihood: High** — the precondition (unset `tokenFeeBps`) is the default state for every token and is already present for at least one live asset.

### Recommendation
Initialize `tokenFeeBps[token]` inside `addSupportedToken` (and the corresponding `reinitialize` paths) to the current global `feeBps`, or require an explicit fee argument. Alternatively, fall back to `feeBps` when `tokenFeeBps[token]` is zero, mirroring the ETH path:

```solidity
uint256 feeBpsForToken = tokenFeeBps[token] != 0 ? tokenFeeBps[token] : feeBps;
fee = amount * feeBpsForToken / 10_000;
```

### Proof of Concept
1. Observe that `tokenFeeBps[wstETH]` is `0` in the deployed `RSETHPool` on Arbitrum (wstETH was added via `reinitialize` without a `setTokenFeeBps` call).
2. Call `deposit(wstETH, 10 ether, "")`.
3. `viewSwapRsETHAmountAndFee(10 ether, wstETH)` computes `fee = 10 ether * 0 / 10_000 = 0`.
4. Caller receives the full rsETH equivalent; `feeEarnedInToken[wstETH]` stays `0`.
5. Repeat for every subsequent token added via `addSupportedToken` before `setTokenFeeBps` is called. [5](#0-4) [6](#0-5)

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
