### Title
Missing Slippage Protection in L2 Pool Deposit Functions - (`contracts/pools/RSETHPoolV3.sol`, `contracts/pools/RSETHPool.sol`, `contracts/pools/RSETHPoolNoWrapper.sol`, `contracts/pools/RSETHPoolV3ExternalBridge.sol`)

---

### Summary
All L2 pool `deposit()` functions accept ETH or ERC-20 tokens from users and mint/transfer rsETH (or wrsETH) in return, with the output amount computed from a live oracle rate. None of these functions accept a `minRsETHAmountExpected` parameter, so users have no way to enforce a minimum acceptable output. If the oracle rate changes between transaction submission and execution, users silently receive fewer rsETH than they simulated, with no on-chain protection.

---

### Finding Description

The L1 deposit path in `LRTDepositPool` correctly implements slippage protection. Both `depositETH` and `depositAsset` accept a `minRSETHAmountExpected` argument and enforce it inside `_beforeDeposit`:

```solidity
// LRTDepositPool.sol
function depositETH(uint256 minRSETHAmountExpected, string calldata referralId) external payable ...
function depositAsset(address asset, uint256 depositAmount, uint256 minRSETHAmountExpected, ...) external ...
```

```solidity
// _beforeDeposit check
if (rsethAmountToMint < minRSETHAmountExpected) {
    revert MinimumAmountToReceiveNotMet();
}
```

The L2 pool equivalents — `RSETHPoolV3.deposit(string)`, `RSETHPoolV3.deposit(address,uint256,string)`, and their counterparts in `RSETHPool`, `RSETHPoolNoWrapper`, and `RSETHPoolV3ExternalBridge` — have no such parameter:

```solidity
// RSETHPoolV3.sol
function deposit(string memory referralId) external payable ... {
    (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount);
    feeEarnedInETH += fee;
    wrsETH.mint(msg.sender, rsETHAmount);   // no minimum check
}

function deposit(address token, uint256 amount, string memory referralId) external ... {
    (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount, token);
    feeEarnedInToken[token] += fee;
    wrsETH.mint(msg.sender, rsETHAmount);   // no minimum check
}
```

The output amount is derived entirely from `getRate()`, which reads a live oracle:

```solidity
function viewSwapRsETHAmountAndFee(uint256 amount) public view returns (uint256 rsETHAmount, uint256 fee) {
    fee = amount * feeBps / 10_000;
    uint256 amountAfterFee = amount - fee;
    uint256 rsETHToETHrate = getRate();          // live oracle read
    rsETHAmount = amountAfterFee * 1e18 / rsETHToETHrate;
}
```

If the oracle rate is updated (rsETH appreciates in ETH terms) between when the user submits the transaction and when it is included in a block, the user receives fewer rsETH than they expected, with no recourse.

The same pattern is present identically in:
- `RSETHPool.sol` — `deposit(string)` and `deposit(address,uint256,string)`
- `RSETHPoolNoWrapper.sol` — `deposit(string)` and `deposit(address,uint256,string)`
- `RSETHPoolV3ExternalBridge.sol` — `deposit(string)` and `deposit(address,uint256,string)`

---

### Impact Explanation
A user who previews the transaction off-chain (e.g., via `viewSwapRsETHAmountAndFee`) and expects `X` wrsETH may receive `X - ΔX` wrsETH if the oracle rate increases before their transaction is mined. Their input ETH/token is already transferred and cannot be recovered. The contract fails to deliver the promised return. This maps to **Low — Contract fails to deliver promised returns, but doesn't lose value**.

---

### Likelihood Explanation
The rsETH oracle rate is updated periodically by the protocol. On L2 networks with short block times, a rate update can land in the same block or the block immediately before a user's deposit. No adversarial actor is required; ordinary protocol operation is sufficient to trigger the discrepancy. Any user depositing on any of the four affected L2 pool contracts is exposed on every deposit.

---

### Recommendation
Add a `minRsETHAmountExpected` parameter to every `deposit()` overload in all L2 pool contracts, mirroring the L1 pattern in `LRTDepositPool._beforeDeposit`. After computing `rsETHAmount`, revert if it is below the caller-supplied minimum:

```solidity
function deposit(string memory referralId, uint256 minRsETHAmountExpected) external payable ... {
    (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount);
    if (rsETHAmount < minRsETHAmountExpected) revert MinimumAmountToReceiveNotMet();
    feeEarnedInETH += fee;
    wrsETH.mint(msg.sender, rsETHAmount);
}
```

---

### Proof of Concept

1. Oracle reports `rsETHToETHrate = 1.05e18` (1 rsETH = 1.05 ETH).
2. User calls `viewSwapRsETHAmountAndFee(1 ether)` off-chain, expects `≈0.952 wrsETH` (after fee).
3. User submits `deposit{value: 1 ether}("ref")`.
4. Before the tx is mined, the protocol updates the oracle to `rsETHToETHrate = 1.10e18`.
5. `deposit()` executes: `rsETHAmount = 1e18 * 1e18 / 1.10e18 ≈ 0.909 wrsETH`.
6. User receives `≈0.909 wrsETH` instead of `≈0.952 wrsETH` — a ~4.5% shortfall — with no revert and no recourse.

**Affected entry points (all publicly callable, no role required):** [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5) [7](#0-6) 

**Contrast with the protected L1 path:** [8](#0-7)

### Citations

**File:** contracts/pools/RSETHPoolV3.sol (L246-265)
```text
    function deposit(string memory referralId)
        external
        payable
        nonReentrant
        whenNotPaused
        limitDailyMint(msg.value, ETH_IDENTIFIER)
    {
        if (!isEthDepositEnabled) revert EthDepositDisabled();
        uint256 amount = msg.value;

        if (amount == 0) revert InvalidAmount();

        (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount);

        feeEarnedInETH += fee;

        wrsETH.mint(msg.sender, rsETHAmount);

        emit SwapOccurred(msg.sender, rsETHAmount, fee, referralId);
    }
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

**File:** contracts/pools/RSETHPool.sol (L265-278)
```text
    function deposit(string memory referralId) external payable nonReentrant whenNotPaused {
        if (!isEthDepositEnabled) revert EthDepositDisabled();
        uint256 amount = msg.value;

        if (amount == 0) revert InvalidAmount();

        (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount);

        feeEarnedInETH += fee;

        IERC20(address(wrsETH)).safeTransfer(msg.sender, rsETHAmount);

        emit SwapOccurred(msg.sender, rsETHAmount, fee, referralId);
    }
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

**File:** contracts/pools/RSETHPoolNoWrapper.sol (L231-244)
```text
    function deposit(string memory referralId) external payable nonReentrant whenNotPaused {
        if (!isEthDepositEnabled) revert EthDepositDisabled();
        uint256 amount = msg.value;

        if (amount == 0) revert InvalidAmount();

        (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount);

        feeEarnedInETH += fee;

        rsETH.safeTransfer(msg.sender, rsETHAmount);

        emit SwapOccurred(msg.sender, rsETHAmount, fee, referralId);
    }
```

**File:** contracts/pools/RSETHPoolNoWrapper.sol (L250-271)
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

        rsETH.safeTransfer(msg.sender, rsETHAmount);

        emit SwapOccurred(msg.sender, rsETHAmount, fee, referralId); // Add token address?
    }
```

**File:** contracts/LRTDepositPool.sol (L648-669)
```text
    function _beforeDeposit(
        address asset,
        uint256 depositAmount,
        uint256 minRSETHAmountExpected
    )
        private
        view
        returns (uint256 rsethAmountToMint)
    {
        if (depositAmount == 0 || depositAmount < minAmountToDeposit) {
            revert InvalidAmountToDeposit();
        }

        if (_checkIfDepositAmountExceedesCurrentLimit(asset, depositAmount)) {
            revert MaximumDepositLimitReached();
        }

        rsethAmountToMint = getRsETHAmountToMint(asset, depositAmount);

        if (rsethAmountToMint < minRSETHAmountExpected) {
            revert MinimumAmountToReceiveNotMet();
        }
```
