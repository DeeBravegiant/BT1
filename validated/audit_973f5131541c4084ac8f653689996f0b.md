### Title
Missing Storage Gap in `LRTConfigRoleChecker` Base Contract Inherited by All Upgradeable Protocol Contracts - (File: contracts/utils/LRTConfigRoleChecker.sol)

### Summary
`LRTConfigRoleChecker` is an abstract base contract that declares a storage variable (`lrtConfig`) but contains no `__gap` array. It is directly inherited by every major upgradeable contract in the protocol. If a future upgrade adds new state variables to `LRTConfigRoleChecker`, the storage layout of all child contracts will shift, causing storage collisions that can corrupt critical protocol state.

### Finding Description
`LRTConfigRoleChecker` declares one storage variable at slot 0:

```solidity
// contracts/utils/LRTConfigRoleChecker.sol
ILRTConfig public lrtConfig;  // slot 0
``` [1](#0-0) 

No `__gap` array follows this variable. The contract is inherited by every major upgradeable contract in the protocol:

- `LRTDepositPool` — `is ILRTDepositPool, LRTConfigRoleChecker, PausableUpgradeable, ReentrancyGuardUpgradeable`
- `NodeDelegator` — `is INodeDelegator, LRTConfigRoleChecker, PausableUpgradeable, ReentrancyGuardUpgradeable`
- `RSETH` — `is Initializable, LRTConfigRoleChecker, ERC20Upgradeable, PausableUpgradeable`
- `LRTOracle` — `is ILRTOracle, LRTConfigRoleChecker, Initializable`
- `LRTWithdrawalManager` — `is ILRTWithdrawalManager, LRTConfigRoleChecker, PausableUpgradeable, ReentrancyGuardUpgradeable`
- `LRTUnstakingVault` — `is ILRTUnstakingVault, LRTConfigRoleChecker, PausableUpgradeable, ReentrancyGuardUpgradeable` [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5) [7](#0-6) 

All child contracts are deployed behind proxies (each has `_disableInitializers()` in their constructor and `initializer`/`reinitializer` functions), confirming they are upgradeable. The codebase already demonstrates live upgrades: `NodeDelegator.initialize2`, `LRTWithdrawalManager.initialize2`/`initialize3`, `RSETH.reinitialize`, and `LRTOracle.reinitialize` all exist. [8](#0-7) [9](#0-8) 

Because `LRTConfigRoleChecker` has no `__gap`, any future upgrade that adds a second storage variable to it (e.g., a secondary config address, a flag, or a counter) will push every child contract's own slot-1 variable into slot 2, overwriting it with whatever was previously at slot 1.

### Impact Explanation
A storage collision caused by adding a variable to `LRTConfigRoleChecker` would silently corrupt the first storage variable of every inheriting child contract. For example:

- In `LRTDepositPool`, slot 1 is `maxNodeDelegatorLimit`. Corruption here would allow unlimited node delegators or block all deposits.
- In `NodeDelegator`, slot 1 is `eigenPod`. Corruption here would redirect ETH staking to an arbitrary address, causing permanent loss of staked ETH.
- In `LRTWithdrawalManager`, slot 1 is `minRsEthAmountToWithdraw`. Corruption here could freeze all withdrawals or allow zero-amount withdrawals. [10](#0-9) [11](#0-10) [12](#0-11) 

**Impact: Low** — Contract fails to deliver promised returns / temporary freezing of funds, contingent on a future upgrade adding variables to the base contract.

### Likelihood Explanation
The protocol has already performed multiple live upgrades across its contracts. The `LRTConfigRoleChecker` base is a natural candidate for extension (e.g., adding a secondary config, a pauser address, or a version flag). The absence of a gap makes any such extension silently dangerous. Likelihood is low-to-medium given the active upgrade history.

### Recommendation
Add a `__gap` array to `LRTConfigRoleChecker` immediately after its existing storage variables, reserving space for future additions:

```solidity
abstract contract LRTConfigRoleChecker {
    ILRTConfig public lrtConfig;

    // Reserve storage slots for future upgrades
    uint256[49] private __gap;

    // ... events, modifiers ...
}
```

This follows the [OpenZeppelin storage gap pattern](https://docs.openzeppelin.com/contracts/4.x/upgradeable#storage_gaps). The gap size (49) accounts for the 1 slot already used by `lrtConfig`, totalling 50 reserved slots.

### Proof of Concept
1. Current storage layout of `LRTDepositPool` (simplified):
   - Slot 0: `lrtConfig` (from `LRTConfigRoleChecker`)
   - Slot 1: `maxNodeDelegatorLimit` (from `LRTDepositPool`)
   - Slot 2: `minAmountToDeposit`
   - ...

2. After an upgrade where `LRTConfigRoleChecker` gains a second variable `newVar`:
   - Slot 0: `lrtConfig`
   - Slot 1: `newVar` ← **overwrites `maxNodeDelegatorLimit`**
   - Slot 2: `maxNodeDelegatorLimit` ← **now reads `minAmountToDeposit`'s old value**
   - Slot 3: `minAmountToDeposit` ← **now reads `isNodeDelegator` mapping slot**

3. All six inheriting upgradeable contracts suffer the same shift simultaneously, with no on-chain warning. [1](#0-0) [13](#0-12)

### Citations

**File:** contracts/utils/LRTConfigRoleChecker.sol (L12-14)
```text
abstract contract LRTConfigRoleChecker {
    ILRTConfig public lrtConfig;

```

**File:** contracts/LRTDepositPool.sol (L26-26)
```text
contract LRTDepositPool is ILRTDepositPool, LRTConfigRoleChecker, PausableUpgradeable, ReentrancyGuardUpgradeable {
```

**File:** contracts/LRTDepositPool.sol (L29-36)
```text
    uint256 public maxNodeDelegatorLimit;
    uint256 public minAmountToDeposit;

    mapping(address => uint256) public isNodeDelegator; // 0: not a node delegator, 1: is a node delegator
    address[] public nodeDelegatorQueue;

    /// @notice maximum amount that can be ignored
    uint256 public maxNegligibleAmount;
```

**File:** contracts/NodeDelegator.sol (L39-39)
```text
contract NodeDelegator is INodeDelegator, LRTConfigRoleChecker, PausableUpgradeable, ReentrancyGuardUpgradeable {
```

**File:** contracts/NodeDelegator.sol (L44-47)
```text
    IEigenPod public eigenPod;

    /// @dev Tracks the balance staked to validators and has yet to have the credentials verified with EigenLayer.
    uint256 public stakedButUnverifiedNativeETH;
```

**File:** contracts/NodeDelegator.sol (L75-77)
```text
    function initialize2() external reinitializer(2) {
        lastNonce = _getNonce();
    }
```

**File:** contracts/RSETH.sol (L13-13)
```text
contract RSETH is Initializable, LRTConfigRoleChecker, ERC20Upgradeable, PausableUpgradeable {
```

**File:** contracts/LRTOracle.sol (L23-23)
```text
contract LRTOracle is ILRTOracle, LRTConfigRoleChecker, Initializable {
```

**File:** contracts/LRTWithdrawalManager.sol (L26-31)
```text
contract LRTWithdrawalManager is
    ILRTWithdrawalManager,
    LRTConfigRoleChecker,
    PausableUpgradeable,
    ReentrancyGuardUpgradeable
{
```

**File:** contracts/LRTWithdrawalManager.sol (L35-37)
```text
    mapping(address asset => uint256) public minRsEthAmountToWithdraw;
    uint256 public withdrawalDelayBlocks;

```

**File:** contracts/LRTWithdrawalManager.sol (L109-121)
```text
    function initialize2(
        uint256 unlockedWithdrawalsCountETHx,
        uint256 unlockedWithdrawalsCountSTETH,
        uint256 unlockedWithdrawalsCountETH
    )
        external
        reinitializer(2)
        onlyRole(LRTConstants.UNLOCKED_WITHDRAWAL_INITIALIZER)
    {
        unlockedWithdrawalsCount[lrtConfig.getLSTToken(LRTConstants.ST_ETH_TOKEN)] = unlockedWithdrawalsCountSTETH;
        unlockedWithdrawalsCount[lrtConfig.getLSTToken(LRTConstants.ETHX_TOKEN)] = unlockedWithdrawalsCountETHx;
        unlockedWithdrawalsCount[LRTConstants.ETH_TOKEN] = unlockedWithdrawalsCountETH;
    }
```

**File:** contracts/LRTUnstakingVault.sol (L25-30)
```text
contract LRTUnstakingVault is
    ILRTUnstakingVault,
    LRTConfigRoleChecker,
    PausableUpgradeable,
    ReentrancyGuardUpgradeable
{
```
