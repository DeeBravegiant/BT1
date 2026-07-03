### Title
Missing `dailyMintLimit` Enforcement in `RSETHPoolV2NBA.deposit` Allows Unbounded wrsETH Minting - (File: contracts/pools/RSETHPoolV2NBA.sol)

### Summary

`RSETHPoolV2NBA` mints `wrsETH` tokens via `deposit` without any daily mint cap check, while every other pool variant in the protocol (`RSETHPoolV2`, `RSETHPoolV3`, `RSETHPoolV2ExternalBridge`, `RSETHPoolV3ExternalBridge`, `RSETHPoolV3WithNativeChainBridge`) enforces a `limitDailyMint` modifier on the same minting path. An unprivileged depositor can call `deposit` on `RSETHPoolV2NBA` and mint an unbounded amount of `wrsETH` in a single day, bypassing the protocol's intended daily issuance control.

### Finding Description

The protocol introduced a `dailyMintLimit` / `limitDailyMint` modifier as a risk-management control across its L2 pool family. Every pool that calls `wrsETH.mint` enforces this modifier before minting:

- `RSETHPoolV2.deposit` — `limitDailyMint(msg.value)` [1](#0-0) 
- `RSETHPoolV2ExternalBridge.deposit` — `limitDailyMint(msg.value)` [2](#0-1) 
- `RSETHPoolV3.deposit` (ETH and token) — `limitDailyMint(...)` [3](#0-2) 
- `RSETHPoolV3ExternalBridge.deposit` (ETH and token) — `limitDailyMint(...)` [4](#0-3) 
- `RSETHPoolV3WithNativeChainBridge.deposit` (ETH and token) — `limitDailyMint(...)` [5](#0-4) 

`RSETHPoolV2NBA.deposit`, however, calls `wrsETH.mint` directly with no cap check whatsoever:

```solidity
function deposit(string memory referralId) external payable nonReentrant whenNotPaused {
    ...
    wrsETH.mint(msg.sender, rsETHAmount);   // ← no limitDailyMint modifier
    ...
}
``` [6](#0-5) 

The contract declares no `dailyMintLimit`, `dailyMintAmount`, or `lastMintDay` state variables at all. [7](#0-6) 

This is structurally identical to the external report's root cause: `_assignNewTokenId` (the shared minting primitive) does not check `maxNumberOfKeys`, so any call path that reaches it bypasses the cap. Here, `wrsETH.mint` is the shared minting primitive, and `RSETHPoolV2NBA.deposit` reaches it without passing through the cap gate.

### Impact Explanation

Any depositor can call `RSETHPoolV2NBA.deposit` with an arbitrarily large ETH value in a single transaction and receive a proportionally large `wrsETH` mint, with no per-day ceiling. The protocol's intended daily issuance limit — the primary on-chain risk-management control against large-scale minting events — is entirely absent for this pool. The contract fails to deliver the promised daily-limit guarantee.

**Impact: Low** — Contract fails to deliver promised returns (the daily mint cap), but deposited ETH backs the minted `wrsETH` at the oracle rate, so no direct value is lost under normal oracle conditions.

### Likelihood Explanation

Any unprivileged depositor can trigger this by calling `deposit` with any ETH amount. No special role, no front-running, no oracle manipulation required. Likelihood is **High**.

### Recommendation

Add the `dailyMintLimit` / `limitDailyMint` mechanism to `RSETHPoolV2NBA` consistent with the other pool variants. Introduce `dailyMintLimit`, `dailyMintAmount`, `lastMintDay`, and `startTimestamp` storage variables, implement the `limitDailyMint` modifier, and apply it to `deposit`. A `reinitializer` function should set the initial limit and start timestamp, mirroring the pattern in `RSETHPoolV2.reinitialize`. [8](#0-7) 

### Proof of Concept

1. `RSETHPoolV2NBA` is deployed on a supported chain with `wrsETH` minting rights.
2. Attacker calls `RSETHPoolV2NBA.deposit{value: 1000 ether}("")`.
3. `viewSwapRsETHAmountAndFee(1000 ether)` computes `rsETHAmount`.
4. `wrsETH.mint(attacker, rsETHAmount)` executes with no `dailyMintLimit` check. [6](#0-5) 
5. Attacker repeats in the same day with another 1000 ETH — no revert, no cap.
6. Compare: the same sequence on `RSETHPoolV2` would revert with `DailyMintLimitExceeded` after the first deposit exhausts the daily quota. [9](#0-8)

### Citations

**File:** contracts/pools/RSETHPoolV2.sol (L72-94)
```text
    modifier limitDailyMint(uint256 amount) {
        if (block.timestamp < startTimestamp) {
            revert MintBeforeStartTimestamp();
        }

        // Calculate the amount of rsETH that will be minted
        (uint256 rsETHAmount,) = viewSwapRsETHAmountAndFee(amount);
        uint256 currentDay = getCurrentDay();

        // If the current day is greater than the last mint day, reset the daily mint amount
        if (currentDay > lastMintDay) {
            lastMintDay = currentDay;
            dailyMintAmount = 0;
        }

        // Check if the daily mint amount plus the amount to mint is greater than the daily mint limit
        if (dailyMintAmount + rsETHAmount > dailyMintLimit) {
            revert DailyMintLimitExceeded();
        }

        dailyMintAmount += rsETHAmount;
        _;
    }
```

**File:** contracts/pools/RSETHPoolV2.sol (L207-207)
```text
    function deposit(string memory referralId) external payable nonReentrant whenNotPaused limitDailyMint(msg.value) {
```

**File:** contracts/pools/RSETHPoolV2ExternalBridge.sol (L289-289)
```text
    function deposit(string memory referralId) external payable nonReentrant whenNotPaused limitDailyMint(msg.value) {
```

**File:** contracts/pools/RSETHPoolV3.sol (L251-251)
```text
        limitDailyMint(msg.value, ETH_IDENTIFIER)
```

**File:** contracts/pools/RSETHPoolV3ExternalBridge.sol (L371-371)
```text
        limitDailyMint(msg.value, ETH_IDENTIFIER)
```

**File:** contracts/pools/RSETHPoolV3WithNativeChainBridge.sol (L287-287)
```text
        limitDailyMint(msg.value, ETH_IDENTIFIER)
```

**File:** contracts/pools/RSETHPoolV2NBA.sol (L26-60)
```text
contract RSETHPoolV2NBA is ERC20Upgradeable, AccessControlUpgradeable, ReentrancyGuardUpgradeable {
    IERC20WrsETH public wrsETH;
    uint256 public feeBps; // Basis points for fees
    uint256 public feeEarnedInETH;
    address public rsETHOracle;

    bytes32 public constant BRIDGER_ROLE = keccak256("BRIDGER_ROLE");

    /// @notice New variable added for pausable functionality
    bool public paused;

    /// @notice The pauser role identifier
    bytes32 public constant PAUSER_ROLE = keccak256("PAUSER_ROLE");

    modifier whenNotPaused() {
        if (paused) revert ContractPaused();
        _;
    }

    modifier whenPaused() {
        if (!paused) revert ContractNotPaused();
        _;
    }

    error InvalidAmount();
    error TransferFailed();
    error ContractPaused();
    error ContractNotPaused();
    error InvalidFeeAmount();

    event SwapOccurred(address indexed user, uint256 rsETHAmount, uint256 fee, string referralId);
    event FeesWithdrawn(uint256 feeEarnedInETH);
    event AssetsMovedForBridging(uint256 ethBalanceMinusFees);
    event FeeBpsSet(uint256 feeBps);
    event OracleSet(address oracle);
```

**File:** contracts/pools/RSETHPoolV2NBA.sol (L106-118)
```text
    function deposit(string memory referralId) external payable nonReentrant whenNotPaused {
        uint256 amount = msg.value;

        if (amount == 0) revert InvalidAmount();

        (uint256 rsETHAmount, uint256 fee) = viewSwapRsETHAmountAndFee(amount);

        feeEarnedInETH += fee;

        wrsETH.mint(msg.sender, rsETHAmount);

        emit SwapOccurred(msg.sender, rsETHAmount, fee, referralId);
    }
```
