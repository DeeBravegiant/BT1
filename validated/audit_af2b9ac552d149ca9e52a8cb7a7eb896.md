### Title
Zero wrsETH/rsETH Minted on Dust Token Deposits Due to Integer Division Truncation - (File: contracts/pools/RSETHPoolNoWrapper.sol, contracts/pools/RSETHPoolV3.sol, contracts/pools/RSETHPool.sol, contracts/pools/RSETHPoolV3ExternalBridge.sol, contracts/pools/RSETHPoolV3WithNativeChainBridge.sol)

---

### Summary

Every L2 pool contract that accepts ERC-20 token deposits computes the output rsETH/wrsETH amount with a single integer division that can truncate to zero. When this happens the user's deposited tokens are already held by the pool, but zero rsETH/wrsETH is minted or transferred back, permanently losing the user's funds.

---

### Finding Description

All five L2 pool variants share the same token-deposit pattern. Taking `RSETHPoolNoWrapper` as the canonical example:

```solidity
// contracts/pools/RSETHPoolNoWrapper.sol  lines 260-270
function deposit(address token, uint256 amount, string memory referralId)
    external nonReentrant whenNotPaused onlySupportedToken(token)
{
    if (amount == 0) revert InvalidAmount();          // only zero-check

    IERC20(token).safeTransferFrom(msg.sender, address(this), amount); // ← tokens leave user

    (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount, token);

    feeEarnedInToken[token] += fee;

    rsETH.safeTransfer(msg.sender, rsETHAmount);      // ← 0 if truncated
    emit SwapOccurred(msg.sender, rsETHAmount, fee, referralId);
}
```

The output is computed in `viewSwapRsETHAmountAndFee`:

```solidity
// contracts/pools/RSETHPoolNoWrapper.sol  lines 300-311
fee          = amount * feeBps / 10_000;
amountAfterFee = amount - fee;
rsETHToETHrate = getRate();                          // ~1.05e18 (rsETH accrues yield)
tokenToETHRate = IOracle(supportedTokenOracle[token]).getRate();

rsETHAmount = amountAfterFee * tokenToETHRate / rsETHToETHrate;  // ← truncates to 0
```

Identical logic appears in:

| Contract | Lines |
|---|---|
| `RSETHPoolNoWrapper.sol` | 292–311 |
| `RSETHPoolV3.sol` | 315–334 |
| `RSETHPool.sol` | 326–346 |
| `RSETHPoolV3ExternalBridge.sol` | 433–452 |
| `RSETHPoolV3WithNativeChainBridge.sol` | 351–370 |

When `amountAfterFee * tokenToETHRate < rsETHToETHrate`, Solidity integer division yields `rsETHAmount = 0`. The guard `if (amount == 0) revert InvalidAmount()` does **not** catch this case because `amount` is non-zero; only the *output* is zero.

After truncation:
- `RSETHPoolNoWrapper`: `rsETH.safeTransfer(msg.sender, 0)` — succeeds silently (OpenZeppelin ERC-20 allows zero-amount transfers).
- `RSETHPoolV3` and others: `wrsETH.mint(msg.sender, 0)` — mints nothing.

In both paths the deposited tokens remain in the pool and are swept into the bridging balance, irrecoverably lost to the user.

---

### Impact Explanation

**Severity: Low — Contract fails to deliver promised returns.**

The threshold below which output truncates to zero:

| Token decimals | Approximate threshold |
|---|---|
| 18 (stETH, wstETH, ETH) | < 2 wei |
| 6 (USDC-like) | < ~3 000 wei (≈ 0.003 USDC) |

For standard 18-decimal LSTs the exploitable amount is sub-wei and therefore practically unreachable. For 6-decimal tokens the threshold is still dust. The user loses their deposited tokens, but the monetary loss is negligible. The contract silently accepts the deposit and emits a `SwapOccurred` event with `rsETHAmount = 0`, which is misleading but not catastrophic.

---

### Likelihood Explanation

Any unprivileged depositor can trigger this by calling `deposit(token, dustAmount, referralId)` with an amount below the truncation threshold. No special role or precondition is required beyond the token being supported. The likelihood of accidental triggering is low (users rarely send dust), but deliberate triggering is trivially easy.

---

### Recommendation

Add a post-computation guard in every pool's token-deposit function (and ETH-deposit function for consistency):

```solidity
(uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount, token);
if (rsETHAmount == 0) revert InvalidAmount();   // ← add this
```

Alternatively, enforce a per-token minimum deposit amount analogous to `minAmountToDeposit` in `LRTDepositPool` (`contracts/LRTDepositPool.sol` line 657).

---

### Proof of Concept

Assume `RSETHPoolNoWrapper` is deployed on a chain where wstETH (18 decimals) is supported, `feeBps = 0`, and `rsETHToETHrate = 1.05e18`.

1. Attacker (or naive user) calls `deposit(wstETH, 1, "")` — depositing 1 wei of wstETH.
2. `fee = 1 * 0 / 10_000 = 0`; `amountAfterFee = 1`.
3. `tokenToETHRate ≈ 1.15e18` (wstETH/ETH rate).
4. `rsETHAmount = 1 * 1.15e18 / 1.05e18 = 1` (this case is fine).

Now with a 6-decimal token (e.g., USDC) at `tokenToETHRate = 3.33e14` (1 USDC ≈ 1/3000 ETH):

1. User calls `deposit(USDC, 3000, "")` — depositing 3000 wei (0.003 USDC).
2. `amountAfterFee = 3000`.
3. `rsETHAmount = 3000 * 3.33e14 / 1.05e18 = 999e15 / 1.05e18 = 0` (truncated).
4. `IERC20(USDC).safeTransferFrom(user, pool, 3000)` — succeeds, 3000 wei USDC leaves user.
5. `rsETH.safeTransfer(user, 0)` — succeeds, user receives nothing.
6. `SwapOccurred(user, 0, 0, "")` emitted — user has lost 3000 wei USDC with no recourse.

Root cause confirmed at: [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5) [7](#0-6) [8](#0-7) [9](#0-8) [10](#0-9)

### Citations

**File:** contracts/pools/RSETHPoolNoWrapper.sol (L260-270)
```text
        if (amount == 0) revert InvalidAmount();

        IERC20(token).safeTransferFrom(msg.sender, address(this), amount);

        (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount, token);

        feeEarnedInToken[token] += fee;

        rsETH.safeTransfer(msg.sender, rsETHAmount);

        emit SwapOccurred(msg.sender, rsETHAmount, fee, referralId); // Add token address?
```

**File:** contracts/pools/RSETHPoolNoWrapper.sol (L292-311)
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
        fee = amount * feeBps / 10_000;
        uint256 amountAfterFee = amount - fee;

        // rate of rsETH in ETH
        uint256 rsETHToETHrate = getRate();

        // rate of token in ETH
        uint256 tokenToETHRate = IOracle(supportedTokenOracle[token]).getRate();

        // Calculate the final rsETH amount
        rsETHAmount = amountAfterFee * tokenToETHRate / rsETHToETHrate;
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

**File:** contracts/pools/RSETHPoolV3.sol (L315-334)
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
        fee = amount * feeBps / 10_000;
        uint256 amountAfterFee = amount - fee;

        // rate of rsETH in ETH
        uint256 rsETHToETHrate = getRate();

        // rate of token in ETH
        uint256 tokenToETHRate = IOracle(supportedTokenOracle[token]).getRate();

        // Calculate the final rsETH amount
        rsETHAmount = amountAfterFee * tokenToETHRate / rsETHToETHrate;
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

**File:** contracts/pools/RSETHPool.sol (L326-346)
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
```

**File:** contracts/pools/RSETHPoolV3ExternalBridge.sol (L390-412)
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

**File:** contracts/pools/RSETHPoolV3ExternalBridge.sol (L433-452)
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
        fee = amount * feeBps / 10_000;
        uint256 amountAfterFee = amount - fee;

        // rate of rsETH in ETH
        uint256 rsETHToETHrate = getRate();

        // rate of token in ETH
        uint256 tokenToETHRate = IOracle(supportedTokenOracle[token]).getRate();

        // Calculate the final rsETH amount
        rsETHAmount = amountAfterFee * tokenToETHRate / rsETHToETHrate;
```

**File:** contracts/pools/RSETHPoolV3WithNativeChainBridge.sol (L307-329)
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

**File:** contracts/pools/RSETHPoolV3WithNativeChainBridge.sol (L351-370)
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
        fee = amount * feeBps / 10_000;
        uint256 amountAfterFee = amount - fee;

        // rate of rsETH in ETH
        uint256 rsETHToETHrate = getRate();

        // rate of token in ETH
        uint256 tokenToETHRate = IOracle(supportedTokenOracle[token]).getRate();

        // Calculate the final rsETH amount
        rsETHAmount = amountAfterFee * tokenToETHRate / rsETHToETHrate;
```
