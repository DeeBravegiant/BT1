### Title
Inconsistent Fee Charging Between ETH and Token Deposits Allows Fee-Free Token Swaps - (File: contracts/pools/RSETHPool.sol)

### Summary
`RSETHPool.sol` uses two separate fee variables: a global `feeBps` for ETH deposits and a per-token `tokenFeeBps[token]` mapping for ERC20 token deposits. Because `addSupportedToken` never initializes `tokenFeeBps[token]`, every newly listed token defaults to a fee of 0 bps. ETH depositors always pay the configured fee; token depositors pay nothing until an admin separately calls `setTokenFeeBps`. Any user who deposits a supported token during this window receives more rsETH than intended, and the protocol collects zero fee revenue on those deposits.

### Finding Description
`RSETHPool.viewSwapRsETHAmountAndFee(uint256)` (ETH path) computes the fee as:

```solidity
fee = amount * feeBps / 10_000;   // line 312 – always uses the global feeBps
```

`RSETHPool.viewSwapRsETHAmountAndFee(uint256, address)` (token path) computes the fee as:

```solidity
uint256 feeBpsForToken = tokenFeeBps[token];   // line 335 – per-token mapping
fee = amount * feeBpsForToken / 10_000;         // line 336
```

`addSupportedToken` registers the oracle and bridge for a new token but never writes to `tokenFeeBps`:

```solidity
supportedTokenList.push(token);
supportedTokenOracle[token] = oracle;
tokenBridge[token] = bridge;
// tokenFeeBps[token] is never set → remains 0
```

The only way to set a non-zero token fee is a separate admin call to `setTokenFeeBps`. Until that call is made, `tokenFeeBps[token] == 0` and every token deposit is fee-free.

### Impact Explanation
**High – Theft of unclaimed yield.**

The fee collected on deposits is protocol revenue. When `tokenFeeBps[token] == 0`, the full deposit amount (rather than `amount - fee`) is used to calculate rsETH minted. The user receives more rsETH than they should, and the protocol collects no fee. Because the fee is tracked separately from bridgeable assets (`getTokenBalanceMinusFees`), the missing fee is never recovered. Over the window between `addSupportedToken` and `setTokenFeeBps`, all token depositors receive a fee-free subsidy at the protocol's expense.

### Likelihood Explanation
**High.**

The two-step pattern (add token, then separately set its fee) is not enforced by the contract. There is no require or default value in `addSupportedToken` that prevents a zero-fee state. Any operator who adds a token without immediately calling `setTokenFeeBps` — or who is unaware that the mapping defaults to 0 — leaves the pool in the vulnerable state. Because the pool is already live on Arbitrum with multiple supported tokens, the pattern is likely to recur with each new token listing.

### Recommendation
Initialize `tokenFeeBps[token]` inside `addSupportedToken` by accepting a `_feeBps` parameter, mirroring how `feeBps` is set at initialization for ETH:

```solidity
function addSupportedToken(
    address token,
    address oracle,
    address bridge,
    uint256 _feeBps          // add this parameter
) external onlyRole(TIMELOCK_ROLE) {
    ...
    if (_feeBps > 10_000) revert InvalidFeeAmount();
    tokenFeeBps[token] = _feeBps;
    ...
}
```

This eliminates the window during which token deposits are fee-free.

### Proof of Concept

1. Admin calls `addSupportedToken(wstETH, oracle, bridge)` on `RSETHPool`. `tokenFeeBps[wstETH]` is 0 (Solidity default).
2. Before admin calls `setTokenFeeBps(wstETH, 30)`, a user calls `deposit(wstETH, 100e18, "")`.
3. `viewSwapRsETHAmountAndFee(100e18, wstETH)` executes:
   - `feeBpsForToken = tokenFeeBps[wstETH]` → **0**
   - `fee = 100e18 * 0 / 10_000` → **0**
   - `amountAfterFee = 100e18`
   - User receives rsETH calculated on the full 100e18, paying **zero fee**.
4. An ETH depositor calling `deposit("")` with the same ETH value pays `feeBps` (e.g., 30 bps), receiving less rsETH.
5. The protocol collects no fee revenue from the token deposit; the user captures the fee amount as extra rsETH.

**Relevant code locations:** [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

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

**File:** contracts/pools/RSETHPool.sol (L583-593)
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
```

**File:** contracts/pools/RSETHPool.sol (L637-655)
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
```
