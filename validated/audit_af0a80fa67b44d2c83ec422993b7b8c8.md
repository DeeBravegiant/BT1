### Title
Token Deposits Always Bypass Protocol Fees Due to Uninitialized `tokenFeeBps` - (File: contracts/pools/RSETHPool.sol)

### Summary
In `RSETHPool.sol`, the `viewSwapRsETHAmountAndFee(amount, token)` function reads fee basis points from the per-token mapping `tokenFeeBps[token]`. Because `addSupportedToken` never initializes this mapping entry, it permanently defaults to `0` for every newly added token. Any unprivileged depositor calling `deposit(token, amount, referralId)` therefore pays zero fees on token deposits, stealing all fee yield that the protocol is designed to collect.

### Finding Description
`RSETHPool.sol` maintains two separate fee variables:
- `feeBps` — a global rate used for ETH deposits, set during `initialize`.
- `tokenFeeBps[token]` — a per-token mapping used for ERC-20 deposits, **never set during `addSupportedToken`**. [1](#0-0) 

```solidity
uint256 feeBpsForToken = tokenFeeBps[token]; // always 0 for any newly added token
fee = amount * feeBpsForToken / 10_000;      // fee == 0
uint256 amountAfterFee = amount - fee;       // full amount passed through
```

`addSupportedToken` registers the oracle and bridge for a token but never touches `tokenFeeBps`: [2](#0-1) 

A separate admin function `setTokenFeeBps` exists but is not called atomically with `addSupportedToken`, leaving an indefinite window (or permanent state if the admin never calls it) where all token deposits are fee-free: [3](#0-2) 

The deposit function unconditionally accepts the zero-fee result: [4](#0-3) 

### Impact Explanation
**High — Theft of unclaimed yield.**

The protocol's fee revenue from token deposits (e.g., wstETH on Arbitrum) is entirely lost. `feeEarnedInToken[token]` will always remain `0`, so `withdrawFees` for that token yields nothing. Every depositor receives the full token-equivalent rsETH amount with no fee deducted, permanently depriving the protocol of its intended yield on all token deposit volume.

### Likelihood Explanation
**High.**

The condition is triggered automatically by every token deposit after `addSupportedToken` is called. No special knowledge, timing, or privileged access is required. Any depositor using a supported ERC-20 token (the primary use case on Arbitrum, where wstETH is the canonical supported token) exploits this by default.

### Recommendation
Initialize `tokenFeeBps[token]` inside `addSupportedToken`, or require a non-zero fee parameter:

```diff
function addSupportedToken(
    address token,
    address oracle,
-   address bridge
+   address bridge,
+   uint256 _feeBps
) external onlyRole(TIMELOCK_ROLE) {
    ...
+   if (_feeBps > 10_000) revert InvalidFeeAmount();
+   tokenFeeBps[token] = _feeBps;
    ...
}
```

Alternatively, add a guard in `viewSwapRsETHAmountAndFee` to revert when `tokenFeeBps[token] == 0`, preventing deposits until the admin explicitly configures the fee.

### Proof of Concept
1. Admin calls `addSupportedToken(wstETH, oracle, bridge)` on `RSETHPool` (Arbitrum).
2. `tokenFeeBps[wstETH]` is `0` (Solidity mapping default). `setTokenFeeBps` is never called (or not yet called).
3. Attacker calls `deposit(wstETH, 100e18, "")`.
4. `viewSwapRsETHAmountAndFee(100e18, wstETH)` computes:
   - `feeBpsForToken = tokenFeeBps[wstETH] = 0`
   - `fee = 100e18 * 0 / 10_000 = 0`
   - `amountAfterFee = 100e18`
5. Attacker receives rsETH equivalent to the full `100e18` wstETH with zero fee deducted.
6. `feeEarnedInToken[wstETH]` remains `0`; the protocol collects nothing. [5](#0-4)

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
