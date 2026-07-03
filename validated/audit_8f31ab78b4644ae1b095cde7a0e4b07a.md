### Title
Access-controlled `pause()` and admin functions unreachable via L1 forced inclusion when L2 sequencer is down — (File: contracts/pools/RSETHPoolV3ExternalBridge.sol, contracts/pools/RSETHPoolV3.sol, contracts/pools/RSETHPoolV3WithNativeChainBridge.sol, contracts/pools/RSETHPoolNoWrapper.sol, contracts/pools/RSETHPoolV2ExternalBridge.sol)

---

### Summary

Every L2 pool contract in the LRT-rsETH protocol is deployed on rollup chains (Arbitrum, Optimism, Base, Unichain) and uses OpenZeppelin `AccessControlUpgradeable` for role enforcement. The `_checkRole` path checks only the raw `msg.sender`. On Arbitrum and Optimism-stack chains, when the L2 sequencer is unavailable, the only way to submit transactions is via forced inclusion from L1. Forced inclusion aliases the sender address (adds `0x1111000000000000000000000000000000001111` on Arbitrum; uses the cross-domain messenger alias on Optimism). The aliased address holds no role, so every `onlyRole(...)` call reverts. The most critical consequence is that `pause()` — the primary emergency brake — cannot be executed via forced inclusion at the exact moment it is most needed.

---

### Finding Description

All five L2 pool contracts inherit `AccessControlUpgradeable` and gate their emergency and administrative functions behind `onlyRole`:

- `pause()` — `onlyRole(PAUSER_ROLE)`
- `unpause()` — `onlyRole(DEFAULT_ADMIN_ROLE)`
- `setDailyMintLimit()` — `onlyRole(DEFAULT_ADMIN_ROLE)`
- `setRSETHOracle()`, `addSupportedToken()`, `removeSupportedToken()`, `setL1VaultETHForL2Chain()`, etc. — `onlyRole(TIMELOCK_ROLE)` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) 

OpenZeppelin's `AccessControlUpgradeable._checkRole` resolves to a `hasRole(role, msg.sender)` lookup against the internal role mapping. No aliased-address equivalent is ever consulted.

When the L2 sequencer is down, the only submission path is L1 forced inclusion (Arbitrum delayed inbox / Optimism `OptimismPortal` deposit). The EVM `msg.sender` seen by the L2 contract for such a transaction is `original_address + 0x1111000000000000000000000000000000001111` (Arbitrum) or the cross-domain messenger alias (Optimism). Neither the `PAUSER_ROLE` holder's aliased address nor the `DEFAULT_ADMIN_ROLE` holder's aliased address has been granted any role, so every `onlyRole` check reverts.

Meanwhile, `deposit()` is a public function with no role requirement — an attacker can force-include deposit calls without any aliasing problem. [6](#0-5) 

---

### Impact Explanation

**Medium — Temporary freezing of funds / potential theft of user funds.**

The `pause()` function is the protocol's primary emergency response. If an exploit is active on an L2 pool (e.g., oracle rate manipulation causing over-minting of `wrsETH`) and the L2 sequencer is simultaneously down, the admin/pauser cannot halt deposits via forced inclusion. Deposits continue at the exploited rate until the sequencer recovers. The daily mint limit provides partial mitigation but resets every 24 hours; extended sequencer downtime allows repeated exploitation across multiple days. `setDailyMintLimit()` is also blocked by the same aliasing issue, so the admin cannot reduce the cap to zero as a fallback.

---

### Likelihood Explanation

All five pool contracts are explicitly deployed on rollup chains with centralized sequencers:

- `RSETHPoolNoWrapper` — comment names Arbitrum and Unichain explicitly. [7](#0-6) 
- `RSETHPoolV3WithNativeChainBridge` — comment names "Standard Rollups (Optimism, Base, etc.)". [8](#0-7) 
- `RSETHPoolV3ExternalBridge` — uses Stargate/LayerZero and native L2 bridges, targeting Optimism/Base/Arbitrum deployments. [9](#0-8) 

Arbitrum and Optimism sequencer outages have occurred historically. The combination of a known exploit window and sequencer downtime is the same threat model acknowledged in the referenced Wormhole report.

---

### Recommendation

In each pool contract, extend the role check in `pause()` (and other time-sensitive admin functions) to also accept the aliased equivalent of the authorized address. For Arbitrum, the alias offset is `0x1111000000000000000000000000000000001111`. A helper such as:

```solidity
function _applyL1ToL2Alias(address l1Address) internal pure returns (address) {
    unchecked {
        return address(uint160(l1Address) + uint160(0x1111000000000000000000000000000000001111));
    }
}
```

can be used to pre-compute and grant roles to both the canonical address and its aliased form, or to add an aliased-address branch inside the modifier. Alternatively, grant the `PAUSER_ROLE` and `DEFAULT_ADMIN_ROLE` to the aliased versions of the multisig/timelock addresses at deployment time on each L2 chain.

---

### Proof of Concept

1. The Kelp DAO admin multisig holds `PAUSER_ROLE` on `RSETHPoolV3ExternalBridge` deployed on Optimism.
2. An attacker discovers an oracle rate manipulation bug in the pool and begins depositing ETH to receive inflated `wrsETH`.
3. The Optimism sequencer goes down. The attacker force-includes their exploit `deposit()` call via the `OptimismPortal` on L1 — `deposit()` requires no role, so it succeeds with the aliased `msg.sender`.
4. The admin attempts to force-include a `pause()` call from L1. The `msg.sender` seen on L2 is `adminAddress + 0x1111...1111`. `hasRole(PAUSER_ROLE, aliasedAdmin)` returns `false`; the call reverts. [1](#0-0) 
5. The admin also attempts `setDailyMintLimit(1)` to cap further minting; this also reverts for the same reason. [10](#0-9) 
6. The attacker continues draining the pool up to the daily mint limit each day until the sequencer recovers, at which point the admin can finally pause — but funds have already been extracted.

### Citations

**File:** contracts/pools/RSETHPoolV3ExternalBridge.sol (L39-42)
```text
/// @title RSETHPoolV3ExternalBridge
/// @notice This contract is the pool for swapping ETH for rsETH. It uses external bridges (e.g. LayerZero/Stargate) for
/// bridging ETH from L2s to L1 and native bridging for LSTs (e.g. wstETH).
contract RSETHPoolV3ExternalBridge is ERC20Upgradeable, AccessControlUpgradeable, ReentrancyGuardUpgradeable {
```

**File:** contracts/pools/RSETHPoolV3ExternalBridge.sol (L364-384)
```text
    /// @dev Swaps ETH for rsETH
    /// @param referralId The referral id
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

**File:** contracts/pools/RSETHPoolV3ExternalBridge.sol (L860-863)
```text
    function pause() external onlyRole(PAUSER_ROLE) whenNotPaused {
        paused = true;
        emit Paused(msg.sender);
    }
```

**File:** contracts/pools/RSETHPoolV3ExternalBridge.sol (L873-879)
```text
    function setDailyMintLimit(uint256 _dailyMintLimit) external onlyRole(DEFAULT_ADMIN_ROLE) {
        if (_dailyMintLimit == 0) {
            revert InvalidDailyMintLimit();
        }
        dailyMintLimit = _dailyMintLimit;
        emit DailyMintLimitSet(_dailyMintLimit);
    }
```

**File:** contracts/pools/RSETHPoolV3.sol (L592-595)
```text
    function pause() external onlyRole(PAUSER_ROLE) whenNotPaused {
        paused = true;
        emit Paused(msg.sender);
    }
```

**File:** contracts/pools/RSETHPoolV3WithNativeChainBridge.sol (L30-34)
```text
/// @title RSETHPoolV3WithNativeChainBridge
/// @notice This contract is the pool for swapping ETH and supported tokens for rsETH with native chain bridge
/// functionality
/// using chain native bridges for supported assets
contract RSETHPoolV3WithNativeChainBridge is ERC20Upgradeable, AccessControlUpgradeable, ReentrancyGuardUpgradeable {
```

**File:** contracts/pools/RSETHPoolV3WithNativeChainBridge.sol (L666-669)
```text
    function pause() external onlyRole(PAUSER_ROLE) whenNotPaused {
        paused = true;
        emit Paused(msg.sender);
    }
```

**File:** contracts/pools/RSETHPoolNoWrapper.sol (L27-30)
```text
/// @title RSETHPoolNoWrapper
/// @notice This contract is the deposit pool for the chains where there is no rsETH wrapper contract (e.g. Arbitrum,
/// Unichain)
contract RSETHPoolNoWrapper is AccessControlUpgradeable, PausableUpgradeable, ReentrancyGuardUpgradeable {
```

**File:** contracts/pools/RSETHPoolNoWrapper.sol (L673-675)
```text
    function pause() external onlyRole(PAUSER_ROLE) whenNotPaused {
        _pause();
    }
```

**File:** contracts/pools/RSETHPoolV2ExternalBridge.sol (L592-595)
```text
    function pause() external onlyRole(PAUSER_ROLE) whenNotPaused {
        paused = true;
        emit Paused(msg.sender);
    }
```
