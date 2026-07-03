### Title
Unbounded `referralId` String in `deposit()` Functions Causes Unbounded Gas Consumption - (File: contracts/pools/RSETHPoolV3.sol)

---

### Summary

All L2 pool `deposit()` functions accept a caller-controlled `string memory referralId` parameter with no length bound. The string is copied into memory and emitted as an event log with no validation. Because EVM memory expansion costs grow quadratically beyond ~724 bytes, a sufficiently large `referralId` causes the transaction's gas consumption to grow without bound, enabling block stuffing by any unprivileged depositor.

---

### Finding Description

Every pool contract in scope exposes two public `deposit()` entry points that accept a `string memory referralId` parameter supplied entirely by the caller:

```solidity
function deposit(string memory referralId) external payable ...
function deposit(address token, uint256 amount, string memory referralId) external ...
```

In every case the string is passed directly to an event emission with zero length validation:

```solidity
emit SwapOccurred(msg.sender, rsETHAmount, fee, referralId);
```

The affected contracts and their deposit signatures are identical in structure:

| Contract | File |
|---|---|
| `RSETHPoolV3` | `contracts/pools/RSETHPoolV3.sol` |
| `RSETHPoolV3ExternalBridge` | `contracts/pools/RSETHPoolV3ExternalBridge.sol` |
| `RSETHPoolV3WithNativeChainBridge` | `contracts/pools/RSETHPoolV3WithNativeChainBridge.sol` |
| `RSETHPool` | `contracts/pools/RSETHPool.sol` |
| `RSETHPoolNoWrapper` | `contracts/pools/RSETHPoolNoWrapper.sol` |
| `RSETHPoolV2ExternalBridge` | `contracts/pools/RSETHPoolV2ExternalBridge.sol` |

The EVM charges memory expansion gas that grows quadratically with allocation size. Copying a `string memory` parameter of length `n` bytes into the EVM's memory word space costs approximately `n/32 + (n/32)^2 / 512` gas. For a 1 MB string this exceeds 2,000,000 gas units from memory expansion alone, before accounting for the `LOG3`/`LOG4` opcode cost which charges 8 gas per byte of event data. There is no upper bound enforced anywhere in the call path. [1](#0-0) [2](#0-1) 

The same pattern is present verbatim in every other pool: [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5) 

---

### Impact Explanation

**Medium — Unbounded gas consumption / block stuffing.**

On every L2 where these pools are deployed (Arbitrum, Optimism, Base, etc.) gas costs are orders of magnitude cheaper than on L1. An attacker can repeatedly call `deposit{value: minDeposit}(largeString)` with a multi-megabyte `referralId`, consuming the majority of each block's gas budget. This prevents legitimate depositors from having their transactions included, constituting a sustained block-stuffing denial-of-service against the deposit path. The attacker's cost is bounded only by their willingness to pay L2 gas fees, which are negligible.

---

### Likelihood Explanation

Any unprivileged depositor can trigger this. No role, whitelist, or special condition is required beyond holding a minimal amount of ETH or a supported LST. The entry point is fully public and the `referralId` parameter is entirely caller-controlled with no on-chain constraint.

---

### Recommendation

Add a maximum length check at the top of every `deposit()` overload before any other logic executes:

```solidity
uint256 constant MAX_REFERRAL_ID_LENGTH = 128; // or a protocol-appropriate bound

function deposit(string memory referralId) external payable ... {
    if (bytes(referralId).length > MAX_REFERRAL_ID_LENGTH) revert ReferralIdTooLong();
    ...
}
```

Apply the same guard to the `(address token, uint256 amount, string memory referralId)` overload in every pool contract. The check must appear before the `limitDailyMint` modifier body executes, as that modifier also performs computation whose cost is fixed and small.

---

### Proof of Concept

```solidity
// Attacker contract — no special privileges needed
contract BlockStuffer {
    IPool pool;
    constructor(address _pool) { pool = IPool(_pool); }

    function stuff() external payable {
        // Build a 500 KB referralId string
        string memory bigId = string(new bytes(500_000));
        // Deposit the minimum viable ETH amount; the gas cost is dominated by the string
        pool.deposit{value: msg.value}(bigId);
    }
}
```

Calling `stuff{value: 1 wei}()` on any of the affected pools will cause the transaction to consume several million gas units from memory expansion and `LOG` data costs alone, with the attacker paying only the (cheap) L2 gas price. Repeating this across consecutive blocks starves the deposit queue for legitimate users. [7](#0-6) [8](#0-7)

### Citations

**File:** contracts/pools/RSETHPoolV3.sol (L150-153)
```text
    event SwapOccurred(address indexed user, uint256 rsETHAmount, uint256 fee, string referralId);
    event SwapOccurred(
        address indexed user, uint256 rsETHAmount, uint256 fee, string referralId, address indexed token
    );
```

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

**File:** contracts/pools/RSETHPoolV3ExternalBridge.sol (L366-384)
```text
    function deposit(string memory referralId)
        external
        payable
        nonReentrant
        whenNotPaused
        limitDailyMint(msg.value, ETH_IDENTIFIER)
    {
        uint256 amount = msg.value;

        if (amount == 0) revert InvalidAmount();

        (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount);

        feeEarnedInETH += fee;

        wrsETH.mint(msg.sender, rsETHAmount);

        emit SwapOccurred(msg.sender, rsETHAmount, fee, referralId);
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

**File:** contracts/pools/RSETHPoolV2ExternalBridge.sol (L289-301)
```text
    function deposit(string memory referralId) external payable nonReentrant whenNotPaused limitDailyMint(msg.value) {
        uint256 amount = msg.value;

        if (amount == 0) revert InvalidAmount();

        (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount);

        feeEarnedInETH += fee;

        wrsETH.mint(msg.sender, rsETHAmount);

        emit SwapOccurred(msg.sender, rsETHAmount, fee, referralId);
    }
```

**File:** contracts/pools/RSETHPoolV3WithNativeChainBridge.sol (L163-167)
```text
    /// @notice Events
    event SwapOccurred(address indexed user, uint256 rsETHAmount, uint256 fee, string referralId);
    event SwapOccurred(
        address indexed user, uint256 rsETHAmount, uint256 fee, string referralId, address indexed depositedToken
    );
```
