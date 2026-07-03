### Title
Zero Fee Applied to Token Deposits Due to Uninitialized `tokenFeeBps` Mapping - (File: contracts/pools/RSETHPool.sol)

### Summary
`RSETHPool.sol` maintains a per-token fee mapping `tokenFeeBps` that defaults to `0` for any token not explicitly configured. The `viewSwapRsETHAmountAndFee(amount, token)` function reads directly from this mapping, so any token added to the pool without a subsequent `tokenFeeBps` setter call results in zero fees being charged on all deposits of that token, while ETH deposits always pay the global `feeBps`.

### Finding Description
`RSETHPool.sol` declares two separate fee variables:

- `feeBps` — a global basis-point fee applied to native ETH deposits.
- `tokenFeeBps` — a per-token mapping applied to ERC-20 token deposits. [1](#0-0) [2](#0-1) 

The ETH deposit path correctly uses `feeBps`:

```solidity
function viewSwapRsETHAmountAndFee(uint256 amount) public view returns (uint256 rsETHAmount, uint256 fee) {
    fee = amount * feeBps / 10_000;
    ...
}
``` [3](#0-2) 

The token deposit path reads from `tokenFeeBps[token]`, which is a Solidity mapping and therefore defaults to `0` for any key that has never been written:

```solidity
function viewSwapRsETHAmountAndFee(uint256 amount, address token) public view ... {
    uint256 feeBpsForToken = tokenFeeBps[token];   // defaults to 0 if never set
    fee = amount * feeBpsForToken / 10_000;        // fee == 0
    ...
}
``` [4](#0-3) 

The existence of a dedicated `TokenFeeBpsSet` event confirms that `tokenFeeBps` is intended to be set via a separate admin call after a token is added: [5](#0-4) 

If that setter call is omitted (or delayed), every call to `deposit(token, amount, referralId)` computes `fee = 0`, transfers the full token amount into the pool, and mints rsETH to the depositor with no fee deducted: [6](#0-5) 

### Impact Explanation
Protocol fee revenue on token deposits is silently lost. Any user who deposits a supported ERC-20 token while `tokenFeeBps[token] == 0` receives the full rsETH equivalent with zero fee charged, whereas ETH depositors always pay `feeBps`. The protocol treasury receives no fee income from those token deposits for the entire window between token addition and fee configuration. This constitutes **theft of unclaimed yield** (protocol fees that should have been collected but were not).

### Likelihood Explanation
**High.** The pool already supports multiple ERC-20 tokens (e.g., wstETH on Arbitrum). Every time a new token is added, `tokenFeeBps[token]` starts at `0`. Any depositor — including a sophisticated user who monitors the mempool for `addSupportedToken` transactions — can immediately deposit large amounts of that token at zero cost until the admin separately calls the fee setter. There is no on-chain enforcement that `tokenFeeBps` must be non-zero before deposits are accepted.

### Recommendation
Initialize `tokenFeeBps[token]` inside `addSupportedToken` (or whatever function registers a new token), requiring the caller to supply the intended fee in basis points. Alternatively, fall back to the global `feeBps` when `tokenFeeBps[token]` is zero, mirroring the behavior of the ETH deposit path. Add a guard in `viewSwapRsETHAmountAndFee(amount, token)` that reverts or uses the global fee if the per-token fee has not been explicitly set.

### Proof of Concept
1. Admin calls `addSupportedToken(tokenX, oracleX, bridgeX)` — `tokenFeeBps[tokenX]` remains `0`.
2. Before the admin calls the fee setter, a user calls `deposit(tokenX, 1_000e18, "ref")`.
3. `viewSwapRsETHAmountAndFee(1_000e18, tokenX)` computes `fee = 1_000e18 * 0 / 10_000 = 0`.
4. The user receives rsETH equivalent to the full `1_000e18` token value with no fee deducted.
5. `feeEarnedInToken[tokenX]` remains `0`; the protocol treasury receives nothing.
6. Contrast: an ETH depositor sending the same value would pay `feeBps` (e.g., 5 bps) to the protocol. [7](#0-6) [8](#0-7)

### Citations

**File:** contracts/pools/RSETHPool.sol (L43-43)
```text
    uint256 public feeBps; // Basis points for fees for ETH deposits
```

**File:** contracts/pools/RSETHPool.sol (L88-88)
```text
    mapping(address token => uint256 feeBps) public tokenFeeBps;
```

**File:** contracts/pools/RSETHPool.sol (L134-134)
```text
    event TokenFeeBpsSet(address indexed token, uint256 feeBps);
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
