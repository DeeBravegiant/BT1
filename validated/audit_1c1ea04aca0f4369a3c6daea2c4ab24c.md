### Title
`feeBps` State Variable Unused for Token Deposits; `tokenFeeBps` Defaults to Zero - (File: contracts/pools/RSETHPool.sol)

### Summary

`RSETHPool.sol` maintains two fee state variables: `feeBps` (the global ETH-deposit fee rate) and `tokenFeeBps[token]` (a per-token fee mapping). When a new token is added via `addSupportedToken`, `tokenFeeBps[token]` is never initialized and defaults to `0`. The `feeBps` state variable — which holds the intended protocol fee rate — is entirely ignored for token deposits. Any depositor can swap supported tokens for rsETH with zero fee until an admin explicitly calls `setTokenFeeBps`.

### Finding Description

`RSETHPool.sol` declares two fee state variables:

- `feeBps` — the global fee rate, set at initialization and used only for ETH deposits.
- `tokenFeeBps[token]` — a per-token fee mapping, added later as a new variable. [1](#0-0) [2](#0-1) 

The ETH deposit path correctly reads `feeBps`: [3](#0-2) 

The token deposit path, however, reads only `tokenFeeBps[token]`, which is a Solidity mapping and therefore defaults to `0` for any token that has never had `setTokenFeeBps` called: [4](#0-3) 

`addSupportedToken` never initializes `tokenFeeBps[token]`: [5](#0-4) 

The token deposit function calls `viewSwapRsETHAmountAndFee(amount, token)`, which returns `fee = 0` for any token whose per-token fee has not been explicitly set: [6](#0-5) 

### Impact Explanation

Any depositor calling `deposit(token, amount, referralId)` for a supported token whose `tokenFeeBps` has not been explicitly set receives rsETH calculated on the full `amount` with no fee deducted. The protocol collects zero fee revenue on those token deposits. `feeEarnedInToken[token]` remains `0`, and the intended fee (as expressed by the non-zero `feeBps`) is silently bypassed. This matches the **Low** impact tier: the contract fails to deliver its promised fee-collection behavior without losing user principal. [7](#0-6) 

### Likelihood Explanation

This is the default state for every token added via `addSupportedToken`. There is no on-chain enforcement requiring `setTokenFeeBps` to be called before or atomically with token addition. Any depositor can observe the zero fee on-chain and deposit tokens during the window before the admin sets the fee.

### Recommendation

Initialize `tokenFeeBps[token]` to `feeBps` (or a caller-supplied fee parameter) inside `addSupportedToken`, so that newly added tokens inherit the global fee rate by default rather than defaulting to zero:

```solidity
function addSupportedToken(address token, address oracle, address bridge) external onlyRole(TIMELOCK_ROLE) {
    // ... existing checks ...
    tokenFeeBps[token] = feeBps; // initialize to global fee rate
    // ...
}
```

### Proof of Concept

1. Admin calls `addSupportedToken(wstETH, oracle, bridge)` — `tokenFeeBps[wstETH]` is `0`.
2. Depositor calls `deposit(wstETH, 10 ether, "ref")`.
3. `viewSwapRsETHAmountAndFee(10 ether, wstETH)` computes `fee = 10 ether * 0 / 10_000 = 0`.
4. Depositor receives rsETH for the full `10 ether` with no fee deducted; `feeEarnedInToken[wstETH]` stays `0`.
5. The non-zero `feeBps` state variable — the intended fee rate — was never consulted. [8](#0-7) [9](#0-8)

### Citations

**File:** contracts/pools/RSETHPool.sol (L43-43)
```text
    uint256 public feeBps; // Basis points for fees for ETH deposits
```

**File:** contracts/pools/RSETHPool.sol (L88-88)
```text
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

**File:** contracts/pools/RSETHPool.sol (L311-313)
```text
    function viewSwapRsETHAmountAndFee(uint256 amount) public view returns (uint256 rsETHAmount, uint256 fee) {
        fee = amount * feeBps / 10_000;
        uint256 amountAfterFee = amount - fee;
```

**File:** contracts/pools/RSETHPool.sol (L335-337)
```text
        uint256 feeBpsForToken = tokenFeeBps[token];
        fee = amount * feeBpsForToken / 10_000;
        uint256 amountAfterFee = amount - fee;
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
