### Title
User deposits ETH/tokens but receives zero wrsETH due to rounding — (File: contracts/pools/RSETHPoolV3.sol)

### Summary
All L2 pool deposit functions compute `rsETHAmount` via integer division that can silently round to zero. When this occurs the deposit is accepted, the user's ETH or tokens are retained by the contract, and zero wrsETH is minted — an exact structural analog of the Notional H-8 finding.

### Finding Description
`viewSwapRsETHAmountAndFee` in every L2 pool variant computes the wrsETH amount to mint as:

```solidity
// ETH path
rsETHAmount = amountAfterFee * 1e18 / rsETHToETHrate;

// Token path
rsETHAmount = amountAfterFee * tokenToETHRate / rsETHToETHrate;
```

If the numerator is smaller than the denominator, Solidity integer division produces `rsETHAmount = 0`. None of the deposit functions check for this before proceeding:

```solidity
// RSETHPoolV3.sol – ETH deposit
(uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount);
feeEarnedInETH += fee;
wrsETH.mint(msg.sender, rsETHAmount);   // mints 0, no revert
```

```solidity
// RSETHPoolV3.sol – token deposit
(uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount, token);
feeEarnedInToken[token] += fee;
wrsETH.mint(msg.sender, rsETHAmount);   // mints 0, no revert
```

The only guard present is `if (amount == 0) revert InvalidAmount()`, which checks the *input* amount, not the *output* wrsETH amount. A non-zero input can still produce a zero output.

The same pattern is replicated across every pool variant:

- `RSETHPoolV3.sol` lines 307, 334 / deposit lines 262, 290 [1](#0-0) 
- `RSETHPoolV2.sol` line 233 / deposit line 216 [2](#0-1) 
- `RSETHPool.sol` lines 319, 346 / deposit lines 275, 302 [3](#0-2) 
- `RSETHPoolNoWrapper.sol` lines 285, 311 [4](#0-3) 
- `RSETHPoolV3ExternalBridge.sol` lines 426, 452 [5](#0-4) 
- `RSETHPoolV3WithNativeChainBridge.sol` lines 343, 370 [6](#0-5) 

### Impact Explanation
A user who triggers the rounding-to-zero condition sends ETH or ERC-20 tokens to the pool contract and receives zero wrsETH. The deposited assets are held by the pool and will be bridged to L1 as part of the collective pool balance, but the user holds no wrsETH and therefore has no claim on those assets. The funds are permanently unrecoverable by the depositor. This matches **Low — Contract fails to deliver promised returns** from the allowed impact scope. [7](#0-6) 

### Likelihood Explanation
Low. For the ETH path the condition triggers when `amountAfterFee * 1e18 < rsETHToETHrate`. With rsETH priced at ~1.05 ETH (`rsETHToETHrate ≈ 1.05e18`), this requires `amountAfterFee ≤ 1 wei`. For the token path the threshold is slightly higher when `tokenToETHRate < rsETHToETHrate`, but still only a few wei. A user would have to send an extremely small deposit for this to trigger, making accidental loss rare. Deliberate griefing (e.g., a contract that repeatedly sends 1-wei deposits) is possible but the per-call loss is negligible. [1](#0-0) 

### Recommendation
Add a zero-output guard in every deposit function, mirroring the fix recommended in the original report:

```solidity
(uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount);
require(rsETHAmount > 0, "zero rsETH amount");
feeEarnedInETH += fee;
wrsETH.mint(msg.sender, rsETHAmount);
```

Apply the same guard to the token-deposit overload and to every pool variant listed above. [8](#0-7) 

### Proof of Concept
1. rsETH oracle returns `rsETHToETHrate = 1.05e18` (rsETH worth 1.05 ETH).
2. Attacker (or ordinary user) calls `RSETHPoolV3.deposit("")` with `msg.value = 1 wei`.
3. `fee = 1 * feeBps / 10_000 = 0` (assuming `feeBps < 10_000`).
4. `amountAfterFee = 1`.
5. `rsETHAmount = 1 * 1e18 / 1.05e18 = 0` (integer division truncates).
6. `wrsETH.mint(msg.sender, 0)` executes without revert — user receives 0 wrsETH.
7. The 1 wei ETH sits in the pool contract, is later bridged to L1 with the rest of the pool balance, and the user has no token to reclaim it.

This is structurally identical to the Notional H-8 root cause: a share-to-asset conversion rounds to zero, the function does not revert, and the caller loses their input without receiving any output. [9](#0-8)

### Citations

**File:** contracts/pools/RSETHPoolV3.sol (L256-264)
```text
        if (amount == 0) revert InvalidAmount();

        (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount);

        feeEarnedInETH += fee;

        wrsETH.mint(msg.sender, rsETHAmount);

        emit SwapOccurred(msg.sender, rsETHAmount, fee, referralId);
```

**File:** contracts/pools/RSETHPoolV3.sol (L299-308)
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

**File:** contracts/pools/RSETHPoolV2.sol (L225-234)
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

**File:** contracts/pools/RSETHPoolNoWrapper.sol (L277-312)
```text
    function viewSwapRsETHAmountAndFee(uint256 amount) public view returns (uint256 rsETHAmount, uint256 fee) {
        fee = amount * feeBps / 10_000;
        uint256 amountAfterFee = amount - fee;

        // rate of rsETH in ETH
        uint256 rsETHToETHrate = getRate();

        // Calculate the final rsETH amount
        rsETHAmount = amountAfterFee * 1e18 / rsETHToETHrate;
    }

    /// @dev view function to get the rsETH amount for a given amount of token
    /// @param amount The amount of token
    /// @return rsETHAmount The amount of rsETH that will be received
    /// @return fee The fee that will be charged
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

**File:** contracts/pools/RSETHPoolV3ExternalBridge.sol (L418-453)
```text
    function viewSwapRsETHAmountAndFee(uint256 amount) public view returns (uint256 rsETHAmount, uint256 fee) {
        fee = amount * feeBps / 10_000;
        uint256 amountAfterFee = amount - fee;

        // rate of rsETH in ETH
        uint256 rsETHToETHrate = getRate();

        // Calculate the final rsETH amount
        rsETHAmount = amountAfterFee * 1e18 / rsETHToETHrate;
    }

    /// @dev view function to get the rsETH amount for a given amount of token
    /// @param amount The amount of token
    /// @return rsETHAmount The amount of rsETH that will be received
    /// @return fee The fee that will be charged
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

**File:** contracts/pools/RSETHPoolV3WithNativeChainBridge.sol (L335-371)
```text
    function viewSwapRsETHAmountAndFee(uint256 amount) public view returns (uint256 rsETHAmount, uint256 fee) {
        fee = amount * feeBps / 10_000;
        uint256 amountAfterFee = amount - fee;

        // rate of rsETH in ETH
        uint256 rsETHToETHrate = getRate();

        // Calculate the final rsETH amount
        rsETHAmount = amountAfterFee * 1e18 / rsETHToETHrate;
    }

    /// @dev view function to get the rsETH amount for a given amount of token
    /// @param amount The amount of token
    /// @param token The token address
    /// @return rsETHAmount The amount of rsETH that will be received
    /// @return fee The fee that will be charged
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
